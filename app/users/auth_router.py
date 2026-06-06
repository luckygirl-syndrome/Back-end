from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.core.observability import posthog_client
from app.core.security import create_access_token, hash_password, verify_password
from app.users import models, schemas
from app.users.router import (
    _verify_google_token, _verify_kakao_token, _verify_apple_token,
    _extract_social_info, _social_login_or_signup,
)

router = APIRouter(prefix="/api", tags=["인증"])

_200 = lambda result: {"200": {"content": {"application/json": {"example": {"isSuccess": True, "code": "200", "message": "OK", "result": result}}}}}


@router.post("/auth/signup", summary="이메일 회원가입", responses=_200({"userId": 1, "email": "user@example.com", "nickname": "또바바"}))
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


@router.post("/auth/login", summary="이메일 로그인", responses=_200({"accessToken": "eyJ...", "tokenType": "bearer"}))
def login(user_data: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == user_data.email).first()
    if not user or not verify_password(user_data.password, user.password):
        raise HTTPException(status_code=401, detail="로그인 정보가 올바르지 않습니다.")
    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in")
    return success({"accessToken": access_token, "tokenType": "bearer"})


@router.post("/auth/google", summary="구글 로그인", responses=_200({"accessToken": "eyJ...", "tokenType": "bearer", "isNewUser": False}))
def google_login(body: schemas.GoogleLoginRequest, db: Session = Depends(get_db)):
    payload = _verify_google_token(body.id_token)
    info = _extract_social_info("google", payload)
    if not info["email"]:
        raise HTTPException(status_code=400, detail="구글 계정에서 이메일을 가져올 수 없습니다.")
    user, is_new_user = _social_login_or_signup(db, "google", info["social_id"],
                                                 email=info["email"], nickname=info["nickname"],
                                                 profile_img=info["profile_img"])
    if is_new_user and posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_signed_up", properties={"provider": "google"})
    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in", properties={"provider": "google"})
    return success({"accessToken": access_token, "tokenType": "bearer", "isNewUser": is_new_user})


@router.post("/auth/kakao", summary="카카오 로그인", responses=_200({"accessToken": "eyJ...", "tokenType": "bearer", "isNewUser": False}))
def kakao_login(body: schemas.KakaoLoginRequest, db: Session = Depends(get_db)):
    payload = _verify_kakao_token(body.id_token)
    info = _extract_social_info("kakao", payload)
    if not info["social_id"]:
        raise HTTPException(status_code=400, detail="카카오 토큰에서 유저 정보를 가져올 수 없습니다.")
    user, is_new_user = _social_login_or_signup(db, "kakao", info["social_id"],
                                                 nickname=info["nickname"], profile_img=info["profile_img"])
    if is_new_user and posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_signed_up", properties={"provider": "kakao"})
    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in", properties={"provider": "kakao"})
    return success({"accessToken": access_token, "tokenType": "bearer", "isNewUser": is_new_user})


@router.post("/auth/apple", summary="애플 로그인", responses=_200({"accessToken": "eyJ...", "tokenType": "bearer", "isNewUser": False}))
def apple_login(body: schemas.AppleLoginRequest, db: Session = Depends(get_db)):
    payload = _verify_apple_token(body.id_token)
    info = _extract_social_info("apple", payload)
    if not info["social_id"]:
        raise HTTPException(status_code=400, detail="애플 토큰에서 유저 정보를 가져올 수 없습니다.")
    user, is_new_user = _social_login_or_signup(db, "apple", info["social_id"], email=info["email"])
    if is_new_user and posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_signed_up", properties={"provider": "apple"})
    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(distinct_id=str(user.user_id), event="user_logged_in", properties={"provider": "apple"})
    return success({"accessToken": access_token, "tokenType": "bearer", "isNewUser": is_new_user})
