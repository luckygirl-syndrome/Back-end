import secrets
import random
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.response import success
from app.core.observability import posthog_client
from app.core.security import (
    create_access_token, create_refresh_token, verify_refresh_token, revoke_refresh_token,
    hash_password, verify_password,
)
from app.core.redis_client import redis_client
from app.core.email import send_password_reset_code, send_signup_verification_code
from app.users import models, schemas
from app.users.router import (
    _verify_google_token, _verify_kakao_token, _verify_apple_token,
    _extract_social_info, _social_login_or_signup,
)

logger = logging.getLogger(__name__)

_RESET_CODE_TTL = 600    # 10분
_RESET_TOKEN_TTL = 600   # 10분
_EMAIL_CODE_TTL = 600    # 10분
_EMAIL_VERIFIED_TTL = 600  # 10분 (회원가입 완료 전까지 유지)

router = APIRouter(prefix="/api", tags=["인증"])

_200 = lambda result: {"200": {"content": {"application/json": {"example": {"isSuccess": True, "code": "200", "message": "OK", "result": result}}}}}


@router.post("/auth/email/request", summary="이메일 인증 코드 발송 (회원가입용)", responses=_200(None))
def email_verify_request(body: schemas.EmailVerifyRequest, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="이미 가입된 이메일입니다.")
    code = f"{random.randint(0, 999999):06d}"
    redis_client.setex(f"email_verify:code:{body.email}", _EMAIL_CODE_TTL, code)
    try:
        send_signup_verification_code(body.email, code)
    except Exception as e:
        logger.error(f"이메일 인증 코드 발송 실패 [{body.email}]: {e}")
        raise HTTPException(status_code=500, detail="이메일 발송에 실패했습니다. 잠시 후 다시 시도해주세요.")
    return success(None)


@router.post("/auth/email/verify", summary="이메일 인증 코드 확인 (회원가입용)", responses=_200(None))
def email_verify_confirm(body: schemas.EmailVerifyConfirm):
    stored = redis_client.get(f"email_verify:code:{body.email}")
    if not stored or stored != body.code:
        raise HTTPException(status_code=400, detail="인증 코드가 올바르지 않거나 만료되었습니다.")
    redis_client.delete(f"email_verify:code:{body.email}")
    redis_client.setex(f"email_verify:verified:{body.email}", _EMAIL_VERIFIED_TTL, "1")
    return success(None)


@router.post("/auth/signup", summary="이메일 회원가입", responses=_200({"userId": 1, "email": "user@example.com", "nickname": "또바바"}))
def signup(user: schemas.UserCreate, db: Session = Depends(get_db)):
    if not redis_client.get(f"email_verify:verified:{user.email}"):
        raise HTTPException(status_code=400, detail="이메일 인증이 완료되지 않았습니다.")
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
    redis_client.delete(f"email_verify:verified:{user.email}")
    if posthog_client:
        posthog_client.capture(distinct_id=str(new_user.user_id), event="user_signed_up",
                               properties={"provider": "email"})
        posthog_client.set(distinct_id=str(new_user.user_id), properties={"nickname": new_user.nickname})
    return success({"userId": new_user.user_id, "email": new_user.email, "nickname": new_user.nickname})


@router.post("/auth/login", summary="이메일 로그인", responses=_200({"accessToken": "eyJ...", "tokenType": "bearer"}))
def login(user_data: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == user_data.email).first()
    if not user or not verify_password(user_data.password, user.password):
        raise HTTPException(status_code=401, detail="로그인 정보가 올바르지 않습니다.")
    access_token = create_access_token(data={"sub": str(user.user_id)})
    refresh_token = create_refresh_token(user.user_id)
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in",
                               properties={"provider": "email"})
    is_fbti_complete = user.fbti_type is not None
    is_profile_complete = user.age_group is not None
    return success({"accessToken": access_token, "refreshToken": refresh_token, "tokenType": "bearer", "isFbtiComplete": is_fbti_complete, "isProfileComplete": is_profile_complete, "nickname": user.nickname or ""})


@router.post("/auth/google", summary="구글 로그인", responses=_200({"accessToken": "eyJ...", "tokenType": "bearer", "isNewUser": False, "isProfileComplete": True}))
def google_login(body: schemas.GoogleLoginRequest, db: Session = Depends(get_db)):
    payload = _verify_google_token(body.id_token)
    info = _extract_social_info("google", payload)
    if not info["email"]:
        raise HTTPException(status_code=400, detail="구글 계정에서 이메일을 가져올 수 없습니다.")
    user, is_new_user = _social_login_or_signup(db, "google", info["social_id"],
                                                 email=info["email"], nickname=info["nickname"],
                                                 profile_img=info["profile_img"])
    access_token = create_access_token(data={"sub": str(user.user_id)})
    refresh_token = create_refresh_token(user.user_id)
    if is_new_user and posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_signed_up", properties={"provider": "google"})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in", properties={"provider": "google"})
    is_profile_complete = user.age_group is not None
    is_fbti_complete = user.fbti_type is not None
    return success({"accessToken": access_token, "refreshToken": refresh_token, "tokenType": "bearer", "isNewUser": is_new_user, "isProfileComplete": is_profile_complete, "isFbtiComplete": is_fbti_complete, "nickname": user.nickname or ""})


@router.post("/auth/kakao", summary="카카오 로그인", responses=_200({"accessToken": "eyJ...", "tokenType": "bearer", "isNewUser": False, "isProfileComplete": True}))
def kakao_login(body: schemas.KakaoLoginRequest, db: Session = Depends(get_db)):
    payload = _verify_kakao_token(body.id_token)
    info = _extract_social_info("kakao", payload)
    if not info["social_id"]:
        raise HTTPException(status_code=400, detail="카카오 토큰에서 유저 정보를 가져올 수 없습니다.")
    user, is_new_user = _social_login_or_signup(db, "kakao", info["social_id"],
                                                 nickname=info["nickname"], profile_img=info["profile_img"])
    access_token = create_access_token(data={"sub": str(user.user_id)})
    refresh_token = create_refresh_token(user.user_id)
    if is_new_user and posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_signed_up", properties={"provider": "kakao"})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in", properties={"provider": "kakao"})
    is_profile_complete = user.age_group is not None
    is_fbti_complete = user.fbti_type is not None
    return success({"accessToken": access_token, "refreshToken": refresh_token, "tokenType": "bearer", "isNewUser": is_new_user, "isProfileComplete": is_profile_complete, "isFbtiComplete": is_fbti_complete, "nickname": user.nickname or ""})


@router.post("/auth/kakao/code", summary="카카오 로그인 (code → token 교환)", responses=_200({"accessToken": "eyJ...", "tokenType": "bearer", "isNewUser": False, "isProfileComplete": True}))
def kakao_code_login(body: schemas.KakaoCodeLoginRequest, db: Session = Depends(get_db)):
    token_res = httpx.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": settings.KAKAO_REST_API_KEY,
            "client_secret": settings.KAKAO_CLIENT_SECRET,
            "redirect_uri": body.redirect_uri,
            "code": body.code,
        },
    )
    if token_res.status_code != 200:
        logger.error(f"[카카오 code 교환 실패] {token_res.text}")
        raise HTTPException(status_code=401, detail="카카오 인가 코드 교환에 실패했습니다.")
    id_token = token_res.json().get("id_token")
    if not id_token:
        raise HTTPException(status_code=401, detail="카카오 토큰 응답에 id_token이 없습니다.")
    # code 플로우의 id_token aud는 REST API 키 — 네이티브 앱 키와 다르므로 별도 검증
    try:
        from jose import jwt as jose_jwt, JWTError
        jwks = httpx.get("https://kauth.kakao.com/.well-known/jwks.json").json()
        kid = jose_jwt.get_unverified_header(id_token).get("kid")
        public_key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if not public_key:
            raise HTTPException(status_code=401, detail="유효하지 않은 카카오 토큰입니다.")
        payload = jose_jwt.decode(id_token, public_key, algorithms=["RS256"], audience=settings.KAKAO_REST_API_KEY)
    except JWTError as e:
        logger.error(f"[카카오 code 로그인 토큰 검증 실패] {e}")
        raise HTTPException(status_code=401, detail="유효하지 않은 카카오 토큰입니다.")
    info = _extract_social_info("kakao", payload)
    if not info["social_id"]:
        raise HTTPException(status_code=400, detail="카카오 토큰에서 유저 정보를 가져올 수 없습니다.")
    user, is_new_user = _social_login_or_signup(db, "kakao", info["social_id"],
                                                 nickname=info["nickname"], profile_img=info["profile_img"])
    access_token = create_access_token(data={"sub": str(user.user_id)})
    refresh_token = create_refresh_token(user.user_id)
    if is_new_user and posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_signed_up", properties={"provider": "kakao"})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in", properties={"provider": "kakao"})
    is_profile_complete = user.age_group is not None
    is_fbti_complete = user.fbti_type is not None
    return success({"accessToken": access_token, "refreshToken": refresh_token, "tokenType": "bearer", "isNewUser": is_new_user, "isProfileComplete": is_profile_complete, "isFbtiComplete": is_fbti_complete, "nickname": user.nickname or ""})


@router.post("/auth/apple", summary="애플 로그인", responses=_200({"accessToken": "eyJ...", "tokenType": "bearer", "isNewUser": False, "isProfileComplete": True}))
def apple_login(body: schemas.AppleLoginRequest, db: Session = Depends(get_db)):
    payload = _verify_apple_token(body.id_token)
    info = _extract_social_info("apple", payload)
    if not info["social_id"]:
        raise HTTPException(status_code=400, detail="애플 토큰에서 유저 정보를 가져올 수 없습니다.")
    user, is_new_user = _social_login_or_signup(db, "apple", info["social_id"], email=info["email"])
    access_token = create_access_token(data={"sub": str(user.user_id)})
    refresh_token = create_refresh_token(user.user_id)
    if is_new_user and posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_signed_up", properties={"provider": "apple"})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in", properties={"provider": "apple"})
    is_profile_complete = user.age_group is not None
    is_fbti_complete = user.fbti_type is not None
    return success({"accessToken": access_token, "refreshToken": refresh_token, "tokenType": "bearer", "isNewUser": is_new_user, "isProfileComplete": is_profile_complete, "isFbtiComplete": is_fbti_complete, "nickname": user.nickname or ""})


@router.post("/auth/refresh", summary="액세스 토큰 재발급", responses=_200({"accessToken": "eyJ...", "tokenType": "bearer"}))
def refresh_access_token(body: schemas.RefreshTokenRequest, db: Session = Depends(get_db)):
    user_id = verify_refresh_token(body.refreshToken)
    if not user_id:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 리프레시 토큰입니다.")
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="유저를 찾을 수 없습니다.")
    access_token = create_access_token(data={"sub": str(user.user_id)})
    return success({"accessToken": access_token, "tokenType": "bearer"})


@router.post("/auth/logout", summary="로그아웃 (리프레시 토큰 폐기)", responses=_200(None))
def logout(body: schemas.RefreshTokenRequest):
    revoke_refresh_token(body.refreshToken)
    return success(None)


@router.post("/auth/password-reset/request", summary="비밀번호 재설정 코드 발송", responses=_200(None))
def password_reset_request(body: schemas.PasswordResetRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == body.email).first()
    if user:
        code = f"{random.randint(0, 999999):06d}"
        redis_client.setex(f"pw_reset:code:{body.email}", _RESET_CODE_TTL, code)
        try:
            send_password_reset_code(body.email, code)
        except Exception as e:
            logger.error(f"비밀번호 재설정 이메일 발송 실패 [{body.email}]: {e}")
    # 미가입 이메일도 200 반환 (보안)
    return success(None)


@router.post("/auth/password-reset/verify", summary="비밀번호 재설정 코드 검증", responses=_200({"resetToken": "abc123..."}))
def password_reset_verify(body: schemas.PasswordResetVerify):
    stored = redis_client.get(f"pw_reset:code:{body.email}")
    if not stored or stored != body.code:
        raise HTTPException(status_code=400, detail="인증 코드가 올바르지 않거나 만료되었습니다.")
    redis_client.delete(f"pw_reset:code:{body.email}")
    reset_token = secrets.token_urlsafe(32)
    redis_client.setex(f"pw_reset:token:{reset_token}", _RESET_TOKEN_TTL, body.email)
    return success({"resetToken": reset_token})


@router.post("/auth/password-reset/confirm", summary="비밀번호 재설정 확정", responses=_200(None))
def password_reset_confirm(body: schemas.PasswordResetConfirm, db: Session = Depends(get_db)):
    email = redis_client.get(f"pw_reset:token:{body.resetToken}")
    if not email:
        raise HTTPException(status_code=400, detail="재설정 토큰이 유효하지 않거나 만료되었습니다.")
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    user.password = hash_password(body.newPassword)
    db.commit()
    redis_client.delete(f"pw_reset:token:{body.resetToken}")
    return success(None)
