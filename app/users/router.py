from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from . import models, schemas
from datetime import datetime, timedelta
from jose import jwt, JWTError
from app.core.config import settings
from fastapi.security import APIKeyHeader
import json
from app.core.security import decode_access_token
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
import httpx
from jose import jwt as jose_jwt
from app.products.models import UserProduct, Product
from sqlalchemy import func
from app.core.observability import posthog_client
from app.users.fbti_types import FBTI_TYPES
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


# ── 프로필 ────────────────────────────────────────────────────────

# 내 프로필 조회
@router.get("/profile", summary="내 프로필 조회", responses=_200({"nickname": "또바바", "profileImg": "3", "fbtiName": "도파민 쇼퍼"}))
def get_my_profile(current_user: models.User = Depends(get_current_user)):
    fbti_name = ""
    profile_img = str(current_user.profile_img) if current_user.profile_img else "1"

    if current_user.fbti_type:
        try:
            persona_json = json.loads(current_user.fbti_type)
            fbti_code = persona_json.get("fbti_type", persona_json.get("persona_type", "")).upper()
            fbti_info = FBTI_TYPES.get(fbti_code)
            if fbti_info:
                fbti_name = fbti_info["name"]
                profile_img = str(fbti_info["image_index"])
        except Exception:
            pass

    return success({
        "nickname": current_user.nickname,
        "profileImg": profile_img,
        "fbtiName": fbti_name,
    })



# FBTI 결과 저장
@router.post("/profile/fbti", summary="FBTI 결과 저장", responses=_200({"fbti": {"fbti_type": "DIMO"}}))
def update_fbti(data: schemas.FbtiFinalResult, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.fbti_type = json.dumps(data.model_dump(), ensure_ascii=False)
    db.commit()
    db.refresh(current_user)
    if posthog_client:
        posthog_client.capture(distinct_id=str(current_user.user_id), event="persona_updated")
    return success({"fbti": data.model_dump()})


# FBTI 결과 조회
@router.get("/profile/fbti", summary="FBTI 결과 조회", responses=_200({"fbti": {"fbti_type": "DIMO"}}))
def get_my_persona(current_user: models.User = Depends(get_current_user)):
    if not current_user.fbti_type:
        return success({"fbti": None})
    try:
        return success({"fbti": json.loads(current_user.fbti_type)})
    except Exception:
        return success({"fbti": None})


# 나의 취향 저장/조회
@router.post("/profile/style", summary="스타일 저장", responses=_200({"style": ["스트릿", "캐주얼"]}))
def update_style(data: schemas.StyleUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.style = [s.value for s in data.style]
    db.commit()
    return success({"style": current_user.style})


@router.get("/profile/style", summary="스타일 조회", responses=_200({"style": ["스트릿", "캐주얼"]}))
def get_style(current_user: models.User = Depends(get_current_user)):
    return success({"style": current_user.style or []})


# 온보딩
@router.post("/initial-question", summary="온보딩 응답 저장", responses=_200(None))
def submit_onboarding(data: schemas.OnboardingCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.age_group = data.age_group.value
    current_user.style = [s.value for s in data.style]
    current_user.regret_frequency = data.regret_frequency.value
    if data.regret_frequency == schemas.RegretFrequency.NONE:
        current_user.regret_reasons = []
    else:
        reasons = list(data.regret_reasons or [])
        if data.regret_reason_custom:
            reasons.append(data.regret_reason_custom)
        current_user.regret_reasons = reasons
    db.commit()
    return success(message="온보딩이 완료되었습니다.")


# 나의 옷장 통계
@router.get("/profile/closet", summary="나의 옷장 통계", responses=_200({"boughtCount": 5, "boughtPrice": 230000, "droppedCount": 3, "droppedPrice": 120000}))
def get_closet_stats(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    base = db.query(UserProduct).outerjoin(
        Product, UserProduct.product_id == Product.product_id
    ).filter(UserProduct.user_id == current_user.user_id)

    bought = base.filter(UserProduct.status == "PURCHASED").all()
    dropped = base.filter(UserProduct.status == "ABANDONED").all()

    bought_price = sum(
        int(db.query(Product).filter_by(product_id=up.product_id).first().price or 0)
        for up in bought
        if db.query(Product).filter_by(product_id=up.product_id).first()
    )
    dropped_price = sum(
        int(db.query(Product).filter_by(product_id=up.product_id).first().price or 0)
        for up in dropped
        if db.query(Product).filter_by(product_id=up.product_id).first()
    )

    return success({
        "boughtCount": len(bought),
        "boughtPrice": bought_price,
        "droppedCount": len(dropped),
        "droppedPrice": dropped_price,
    })


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

