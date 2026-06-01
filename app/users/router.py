from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from . import models, schemas
from datetime import datetime, timedelta
from jose import jwt, JWTError
from app.core.config import settings
from fastapi.security import APIKeyHeader
import json
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
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
        return google_id_token.verify_oauth2_token(
            id_token, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        logger.error(f"[구글 로그인 실패] {e}")
        raise HTTPException(status_code=401, detail=f"유효하지 않은 구글 토큰입니다: {e}")


def _verify_kakao_token(id_token: str) -> dict:
    try:
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


# ── 인증 엔드포인트 ───────────────────────────────────────────────

# 1. 회원가입
@router.post("/auth/signup")
def signup(user: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="이미 존재하는 이메일입니다.")
    new_user = models.User(
        email=user.email,
        password=hash_password(user.password),
        nickname=user.nickname,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    if posthog_client:
        posthog_client.capture(distinct_id=str(new_user.user_id), event="user_signed_up",
                               properties={"has_nickname": bool(new_user.nickname)})
        posthog_client.set(distinct_id=str(new_user.user_id), properties={"nickname": new_user.nickname})
    return success({"userId": new_user.user_id, "email": new_user.email, "nickname": new_user.nickname})


# 2. 로그인
@router.post("/auth/login")
def login(user_data: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == user_data.email).first()
    if not user or not verify_password(user_data.password, user.password):
        raise HTTPException(status_code=401, detail="로그인 정보가 올바르지 않습니다.")
    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in")
    return success({"accessToken": access_token, "tokenType": "bearer"})


# 3. 구글 로그인
@router.post("/auth/google")
def google_login(body: schemas.GoogleLoginRequest, db: Session = Depends(get_db)):
    payload = _verify_google_token(body.id_token)
    info = _extract_social_info("google", payload)
    if not info["email"]:
        raise HTTPException(status_code=400, detail="구글 계정에서 이메일을 가져올 수 없습니다.")
    user, is_new_user = _social_login_or_signup(db, "google", info["social_id"],
                                                 email=info["email"], nickname=info["nickname"],
                                                 profile_img=info["profile_img"])
    if is_new_user and posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_signed_up",
                               properties={"provider": "google"})
    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in",
                               properties={"provider": "google"})
    return success({"accessToken": access_token, "tokenType": "bearer", "isNewUser": is_new_user})


# 4. 카카오 로그인
@router.post("/auth/kakao")
def kakao_login(body: schemas.KakaoLoginRequest, db: Session = Depends(get_db)):
    payload = _verify_kakao_token(body.id_token)
    info = _extract_social_info("kakao", payload)
    if not info["social_id"]:
        raise HTTPException(status_code=400, detail="카카오 토큰에서 유저 정보를 가져올 수 없습니다.")
    user, is_new_user = _social_login_or_signup(db, "kakao", info["social_id"],
                                                 nickname=info["nickname"], profile_img=info["profile_img"])
    if is_new_user and posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_signed_up",
                               properties={"provider": "kakao"})
    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in",
                               properties={"provider": "kakao"})
    return success({"accessToken": access_token, "tokenType": "bearer", "isNewUser": is_new_user})


# 5. 애플 로그인
@router.post("/auth/apple")
def apple_login(body: schemas.AppleLoginRequest, db: Session = Depends(get_db)):
    payload = _verify_apple_token(body.id_token)
    info = _extract_social_info("apple", payload)
    if not info["social_id"]:
        raise HTTPException(status_code=400, detail="애플 토큰에서 유저 정보를 가져올 수 없습니다.")
    user, is_new_user = _social_login_or_signup(db, "apple", info["social_id"], email=info["email"])
    if is_new_user and posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_signed_up",
                               properties={"provider": "apple"})
    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in",
                               properties={"provider": "apple"})
    return success({"accessToken": access_token, "tokenType": "bearer", "isNewUser": is_new_user})


# ── 소셜 연동/해제 ────────────────────────────────────────────────

# 소셜 계정 연동
@router.post("/setting/social/link")
def link_social(body: schemas.SocialLinkRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    already = db.query(models.UserSocialProvider).filter_by(
        user_id=current_user.user_id, provider=body.provider
    ).first()
    if already:
        raise HTTPException(status_code=400, detail=f"이미 {body.provider} 계정이 연결되어 있습니다.")

    payload = _verify_token_by_provider(body.provider, body.id_token)
    info = _extract_social_info(body.provider, payload)
    social_id = info["social_id"]
    if not social_id:
        raise HTTPException(status_code=400, detail="소셜 토큰에서 유저 정보를 가져올 수 없습니다.")

    conflict = db.query(models.UserSocialProvider).filter_by(
        provider=body.provider, social_id=social_id
    ).first()
    if conflict:
        raise HTTPException(status_code=400, detail="이미 다른 계정에 연결된 소셜 계정입니다.")

    db.add(models.UserSocialProvider(user_id=current_user.user_id, provider=body.provider, social_id=social_id))
    db.commit()
    return success(message=f"{body.provider} 계정이 연결되었습니다.")


# 소셜 계정 해제
@router.delete("/setting/social/unlink")
def unlink_social(body: schemas.SocialUnlinkRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    providers = db.query(models.UserSocialProvider).filter_by(user_id=current_user.user_id).all()
    has_password = bool(current_user.password)

    if len(providers) <= 1 and not has_password:
        raise HTTPException(status_code=400, detail="마지막 로그인 수단입니다. 해제할 수 없습니다.")

    link = db.query(models.UserSocialProvider).filter_by(
        user_id=current_user.user_id, provider=body.provider
    ).first()
    if not link:
        raise HTTPException(status_code=400, detail="연결된 계정이 없습니다.")

    db.delete(link)
    db.commit()
    return success(message=f"{body.provider} 연결이 해제되었습니다.")


# ── 프로필 ────────────────────────────────────────────────────────

# 내 프로필 조회
@router.get("/profile")
def get_my_profile(current_user: models.User = Depends(get_current_user)):
    fbti_name = ""
    profile_img = str(current_user.profile_img) if current_user.profile_img else "1"

    if current_user.persona_type:
        try:
            persona_json = json.loads(current_user.persona_type)
            fbti_code = persona_json.get("persona_type", "").upper()
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


# 닉네임 수정
@router.patch("/setting/nickname")
def update_nickname(data: schemas.NicknameUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.nickname = data.nickname
    db.commit()
    db.refresh(current_user)
    return success({"nickname": current_user.nickname})


# FBTI 결과 저장
@router.post("/setting/profile/fbti")
def update_fbti(data: schemas.FbtiFinalResult, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.persona_type = json.dumps(data.model_dump(), ensure_ascii=False)
    db.commit()
    db.refresh(current_user)
    if posthog_client:
        posthog_client.capture(distinct_id=str(current_user.user_id), event="persona_updated")
    return success({"persona": data.model_dump()})


# FBTI 결과 조회
@router.get("/profile/fbti", response_model=schemas.PersonaRead)
def get_my_persona(current_user: models.User = Depends(get_current_user)):
    if not current_user.persona_type:
        return success({"persona": None})
    try:
        return success({"persona": json.loads(current_user.persona_type)})
    except Exception:
        return success({"persona": None})


# 나의 취향 저장/조회
@router.post("/profile/style")
def update_style(data: schemas.StyleUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.style = [s.value for s in data.style]
    db.commit()
    return success({"style": current_user.style})


@router.get("/profile/style")
def get_style(current_user: models.User = Depends(get_current_user)):
    return success({"style": current_user.style or []})


# 온보딩
@router.post("/initial-question")
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
@router.get("/profile/closet")
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


# ── 설정 ──────────────────────────────────────────────────────────

# 계정 정보 조회
@router.get("/setting/account")
def get_account(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    providers = db.query(models.UserSocialProvider).filter_by(user_id=current_user.user_id).all()
    provider_list = [p.provider for p in providers]
    # 구 컬럼 폴백
    if current_user.social_provider and current_user.social_provider not in provider_list:
        provider_list.append(current_user.social_provider)
    return success({
        "email": current_user.email,
        "socialProviders": provider_list,
        "hasPassword": bool(current_user.password),
    })


# 문의하기
@router.post("/setting/inquiry")
def create_inquiry(body: schemas.InquiryCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    inquiry = models.Inquiry(user_id=current_user.user_id, content=body.content, reply_email=body.reply_email)
    db.add(inquiry)
    db.commit()
    return success(message="문의가 접수되었습니다.")


# 비밀번호 확인
@router.post("/setting/password/verify")
def verify_password_endpoint(body: schemas.PasswordVerify, current_user: models.User = Depends(get_current_user)):
    if not current_user.password:
        raise HTTPException(status_code=400, detail="소셜 로그인 계정은 비밀번호가 없습니다.")
    if not verify_password(body.current_password, current_user.password):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    return success(message="비밀번호 확인 완료.")


# 비밀번호 변경
@router.patch("/setting/password")
def change_password(body: schemas.PasswordChange, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.password:
        raise HTTPException(status_code=400, detail="소셜 로그인 계정은 비밀번호가 없습니다.")
    current_user.password = hash_password(body.new_password)
    db.commit()
    return success(message="비밀번호가 변경되었습니다.")
