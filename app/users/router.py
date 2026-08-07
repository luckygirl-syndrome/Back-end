from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from . import models
from jose import JWTError
from app.core.config import settings
from fastapi.security import APIKeyHeader
from app.core.security import decode_access_token
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
import httpx
from jose import jwt as jose_jwt
from app.products.models import UserProduct
from app.core.observability import posthog_client
from app.core.response import success
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["유저 관리"])
api_key_header = APIKeyHeader(name="Authorization")

_200 = lambda result: {"200": {"content": {"application/json": {"example": {"isSuccess": True, "code": "200", "message": "OK", "result": result}}}}}


# 인증 함수
def get_current_user(token: str = Depends(api_key_header), db: Session = Depends(get_db)):
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="인증 실패")
    user_id = payload.get("sub")
    user = db.query(models.User).filter(models.User.user_id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="유저 없음")
    return user


# ── 소셜 토큰 검증 헬퍼 ─────────────────────────────────────────

def _verify_google_token(id_token: str) -> dict:
    try:
        audiences = [settings.GOOGLE_CLIENT_ID]
        if settings.GOOGLE_IOS_CLIENT_ID:
            audiences.append(settings.GOOGLE_IOS_CLIENT_ID)
        if settings.GOOGLE_WEB_CLIENT_ID:
            audiences.append(settings.GOOGLE_WEB_CLIENT_ID)
        return google_id_token.verify_oauth2_token(
            id_token, google_requests.Request(), audiences
        )
    except ValueError as e:
        logger.error(f"[구글 로그인 실패] {e}")
        raise HTTPException(status_code=401, detail=f"유효하지 않은 구글 토큰입니다: {e}")


def _verify_kakao_token(id_token: str) -> dict:
    try:
        unverified = jose_jwt.get_unverified_claims(id_token)
        logger.info(f"[카카오 토큰 aud] {unverified.get('aud')} / 설정값: {settings.KAKAO_NATIVE_APP_KEY}")
        jwks = httpx.get("https://kauth.kakao.com/.well-known/jwks.json").json()
        kid = jose_jwt.get_unverified_header(id_token).get("kid")
        public_key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if not public_key:
            raise HTTPException(status_code=401, detail="유효하지 않은 카카오 토큰입니다.")
        return jose_jwt.decode(
            id_token, public_key, algorithms=["RS256"],
            audience=settings.KAKAO_NATIVE_APP_KEY,
        )
    except JWTError as e:
        logger.error(f"[카카오 로그인 실패] {e}")
        raise HTTPException(status_code=401, detail="유효하지 않은 카카오 토큰입니다.")


def _verify_apple_token(id_token: str) -> dict:
    try:
        jwks = httpx.get("https://appleid.apple.com/auth/keys").json()
        kid = jose_jwt.get_unverified_header(id_token).get("kid")
        public_key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if not public_key:
            raise HTTPException(status_code=401, detail="유효하지 않은 애플 토큰입니다.")
        payload = jose_jwt.decode(
            id_token, public_key, algorithms=["RS256"],
            issuer="https://appleid.apple.com",
            options={"verify_aud": False},
        )
        aud = payload.get("aud")
        valid_aud = [aud] if isinstance(aud, str) else (aud or [])
        if settings.APPLE_CLIENT_ID not in valid_aud:
            logger.error(f"[애플 로그인 실패] aud 불일치: {aud} != {settings.APPLE_CLIENT_ID}")
            raise HTTPException(status_code=401, detail="유효하지 않은 애플 토큰입니다.")
        return payload
    except JWTError as e:
        logger.error(f"[애플 로그인 실패] {e}")
        raise HTTPException(status_code=401, detail=f"유효하지 않은 애플 토큰입니다: {e}")


def _verify_token_by_provider(provider: str, id_token: str) -> dict:
    if provider == "google":
        return _verify_google_token(id_token)
    elif provider == "kakao":
        return _verify_kakao_token(id_token)
    elif provider == "apple":
        return _verify_apple_token(id_token)
    raise HTTPException(status_code=400, detail="지원하지 않는 소셜 로그인입니다.")


def _extract_social_info(provider: str, payload: dict) -> dict:
    """provider별로 social_id, email, nickname, profile_img 추출"""
    if provider == "google":
        return {
            "social_id": payload.get("sub"),
            "email": payload.get("email"),
            "nickname": payload.get("name", ""),
            "profile_img": payload.get("picture", ""),
        }
    elif provider == "kakao":
        return {
            "social_id": payload.get("sub"),
            "email": None,
            "nickname": payload.get("nickname", ""),
            "profile_img": payload.get("picture", ""),
        }
    elif provider == "apple":
        return {
            "social_id": payload.get("sub"),
            "email": payload.get("email"),
            "nickname": "",
            "profile_img": None,
        }
    raise HTTPException(status_code=400, detail="지원하지 않는 소셜 로그인입니다.")


def _social_login_or_signup(db: Session, provider: str, social_id: str, email=None, nickname="", profile_img=None):
    """소셜 로그인: 기존 유저 찾기 또는 신규 가입. (user, is_new_user) 반환"""
    # 1. 새 테이블에서 조회
    link = db.query(models.UserSocialProvider).filter_by(
        provider=provider, social_id=social_id
    ).first()
    if link:
        user = db.query(models.User).filter_by(user_id=link.user_id).first()
        if user is None:
            # UserSocialProvider만 남고 User가 삭제된 경우 — 고아 링크 제거 후 신규 가입으로 처리
            db.delete(link)
            db.flush()
        else:
            return user, False

    # 2. 구 컬럼에서 조회 (마이그레이션)
    old_user = db.query(models.User).filter(
        models.User.social_id == social_id,
        models.User.social_provider == provider,
    ).first()
    if old_user:
        db.add(models.UserSocialProvider(user_id=old_user.user_id, provider=provider, social_id=social_id))
        db.commit()
        return old_user, False

    # 3. 신규 가입
    if email:
        email_conflict = db.query(models.User).filter_by(email=email).first()
        if email_conflict:
            raise HTTPException(status_code=400, detail="이미 해당 이메일로 가입된 계정입니다. 기존 로그인 방식을 이용해주세요.")

    user = models.User(email=email, password=None, nickname=nickname or "", profile_img=profile_img)
    db.add(user)
    db.flush()
    db.add(models.UserSocialProvider(user_id=user.user_id, provider=provider, social_id=social_id))
    db.commit()
    db.refresh(user)
    return user, True


# ── 인증 ─────────────────────────────────────────────────────────

@router.get("/auth/check-email", summary="이메일 중복 확인", responses=_200({"available": True}))
def check_email(email: str, db: Session = Depends(get_db)):
    exists = db.query(models.User).filter(models.User.email == email).first()
    return success({"available": not bool(exists)})


# 회원 탈퇴
@router.delete("/users/me", summary="회원 탈퇴", responses=_200(None))
def delete_my_account(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from app.chat.models import Chat
    from app.notifications.models import Notification, FcmToken

    user_id = current_user.user_id

    if posthog_client:
        posthog_client.capture(distinct_id=str(user_id), event="user_deleted")

    # 관련 데이터 순서대로 삭제 (FK 의존성 고려)
    up_ids = [row[0] for row in db.query(UserProduct.user_product_id).filter(UserProduct.user_id == user_id).all()]
    if up_ids:
        db.query(Chat).filter(Chat.user_product_id.in_(up_ids)).delete(synchronize_session=False)
        db.query(Notification).filter(Notification.user_product_id.in_(up_ids)).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.user_id == user_id).delete(synchronize_session=False)
    db.query(FcmToken).filter(FcmToken.user_id == user_id).delete(synchronize_session=False)
    db.query(UserProduct).filter(UserProduct.user_id == user_id).delete(synchronize_session=False)
    db.query(models.UserSocialProvider).filter(models.UserSocialProvider.user_id == user_id).delete(synchronize_session=False)
    db.delete(current_user)
    db.commit()

    return success(None)

