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

router = APIRouter(prefix="/api", tags=["유저 관리"])
api_key_header = APIKeyHeader(name="Authorization")

# 인증 함수: 토큰을 읽어서 현재 유저 객체를 반환
def get_current_user(token: str = Depends(api_key_header), db: Session = Depends(get_db)):
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="인증 실패")

    user_id = payload.get("sub")
    user = db.query(models.User).filter(models.User.user_id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="유저 없음")
    return user

# 1. 회원가입
@router.post("/auth/signup")
def signup(user: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="이미 존재하는 이메일입니다.")
    
    new_user = models.User(
        email=user.email,
        password=hash_password(user.password),
        nickname=user.nickname
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    if posthog_client:
        posthog_client.capture(
            distinct_id=str(new_user.user_id),
            event="user_signed_up",
            properties={"has_nickname": bool(new_user.nickname)},
        )
        posthog_client.set(
            distinct_id=str(new_user.user_id),
            properties={"nickname": new_user.nickname},
        )
    return success({"userId": new_user.user_id, "email": new_user.email, "nickname": new_user.nickname})

# 2. 로그인
@router.post("/auth/login")
def login(user_data: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == user_data.email).first()
    if not user or not verify_password(user_data.password, user.password):
        raise HTTPException(status_code=401, detail="로그인 정보가 올바르지 않습니다.")
    
    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(
            distinct_id=str(user.user_id),
            event="user_logged_in",
        )
    return success({"accessToken": access_token, "tokenType": "bearer"})

# 3. 구글 로그인
@router.post("/auth/google")
def google_login(body: schemas.GoogleLoginRequest, db: Session = Depends(get_db)):
    try:
        idinfo = google_id_token.verify_oauth2_token(
            body.id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="유효하지 않은 구글 토큰입니다.")

    email = idinfo.get("email")
    google_sub = idinfo.get("sub")
    name = idinfo.get("name", "")
    picture = idinfo.get("picture", "")

    if not email:
        raise HTTPException(status_code=400, detail="구글 계정에서 이메일을 가져올 수 없습니다.")

    existing_user = db.query(models.User).filter(models.User.email == email).first()

    if existing_user:
        if existing_user.social_provider != "google":
            raise HTTPException(status_code=400, detail="이미 이메일로 가입된 계정입니다. 일반 로그인을 이용해주세요.")
        user = existing_user
        is_new_user = False
    else:
        user = models.User(
            email=email,
            password=None,
            nickname=name,
            profile_img=picture,
            social_provider="google",
            social_id=google_sub,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new_user = True
        if posthog_client:
            posthog_client.capture(
                distinct_id=str(user.user_id),
                event="user_signed_up",
                properties={"provider": "google", "has_nickname": bool(user.nickname)},
            )

    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(
            distinct_id=str(user.user_id),
            event="user_logged_in",
            properties={"provider": "google"},
        )
    return success({"accessToken": access_token, "tokenType": "bearer", "isNewUser": is_new_user})

# 4. 구글 계정 연결 (기존 로그인 유저)
@router.post("/auth/google/connect")
def google_connect(body: schemas.GoogleLoginRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if current_user.social_provider == "google":
        raise HTTPException(status_code=400, detail="이미 구글 계정이 연결되어 있습니다.")

    try:
        idinfo = google_id_token.verify_oauth2_token(
            body.id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="유효하지 않은 구글 토큰입니다.")

    google_sub = idinfo.get("sub")
    email = idinfo.get("email")

    already_linked = db.query(models.User).filter(
        models.User.social_id == google_sub,
        models.User.social_provider == "google"
    ).first()
    if already_linked:
        raise HTTPException(status_code=400, detail="이미 다른 계정에 연결된 구글 계정입니다.")

    current_user.social_provider = "google"
    current_user.social_id = google_sub
    if email and not current_user.email:
        current_user.email = email
    db.commit()

    return success(message="구글 계정이 연결되었습니다.")

# 5. 카카오 로그인
@router.post("/auth/kakao")
def kakao_login(body: schemas.KakaoLoginRequest, db: Session = Depends(get_db)):
    try:
        jwks_response = httpx.get("https://kauth.kakao.com/.well-known/jwks.json")
        jwks = jwks_response.json()

        header = jose_jwt.get_unverified_header(body.id_token)
        kid = header.get("kid")

        public_key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if not public_key:
            raise HTTPException(status_code=401, detail="유효하지 않은 카카오 토큰입니다.")

        payload = jose_jwt.decode(
            body.id_token,
            public_key,
            algorithms=["RS256"],
            audience=settings.KAKAO_REST_API_KEY,
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="유효하지 않은 카카오 토큰입니다.")

    kakao_sub = payload.get("sub")
    nickname = payload.get("nickname", "")
    picture = payload.get("picture", "")

    if not kakao_sub:
        raise HTTPException(status_code=400, detail="카카오 토큰에서 유저 정보를 가져올 수 없습니다.")

    existing_user = db.query(models.User).filter(
        models.User.social_id == kakao_sub,
        models.User.social_provider == "kakao"
    ).first()

    if existing_user:
        user = existing_user
        is_new_user = False
    else:
        user = models.User(
            email=None,
            password=None,
            nickname=nickname,
            profile_img=picture,
            social_provider="kakao",
            social_id=kakao_sub,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new_user = True
        if posthog_client:
            posthog_client.capture(
                distinct_id=str(user.user_id),
                event="user_signed_up",
                properties={"provider": "kakao", "has_nickname": bool(user.nickname)},
            )

    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(
            distinct_id=str(user.user_id),
            event="user_logged_in",
            properties={"provider": "kakao"},
        )
    return success({"accessToken": access_token, "tokenType": "bearer", "isNewUser": is_new_user})

# 5. 카카오 계정 연결 (기존 로그인 유저)
@router.post("/auth/kakao/connect")
def kakao_connect(body: schemas.KakaoLoginRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if current_user.social_provider == "kakao":
        raise HTTPException(status_code=400, detail="이미 카카오 계정이 연결되어 있습니다.")

    try:
        jwks_response = httpx.get("https://kauth.kakao.com/.well-known/jwks.json")
        jwks = jwks_response.json()

        header = jose_jwt.get_unverified_header(body.id_token)
        kid = header.get("kid")

        public_key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if not public_key:
            raise HTTPException(status_code=401, detail="유효하지 않은 카카오 토큰입니다.")

        payload = jose_jwt.decode(
            body.id_token,
            public_key,
            algorithms=["RS256"],
            audience=settings.KAKAO_REST_API_KEY,
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="유효하지 않은 카카오 토큰입니다.")

    kakao_sub = payload.get("sub")
    if not kakao_sub:
        raise HTTPException(status_code=400, detail="카카오 토큰에서 유저 정보를 가져올 수 없습니다.")

    already_linked = db.query(models.User).filter(
        models.User.social_id == kakao_sub,
        models.User.social_provider == "kakao"
    ).first()
    if already_linked:
        raise HTTPException(status_code=400, detail="이미 다른 계정에 연결된 카카오 계정입니다.")

    current_user.social_provider = "kakao"
    current_user.social_id = kakao_sub
    db.commit()

    return success(message="카카오 계정이 연결되었습니다.")

def _verify_apple_token(id_token: str) -> dict:
    try:
        jwks_response = httpx.get("https://appleid.apple.com/auth/keys")
        jwks = jwks_response.json()

        header = jose_jwt.get_unverified_header(id_token)
        kid = header.get("kid")

        public_key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if not public_key:
            raise HTTPException(status_code=401, detail="유효하지 않은 애플 토큰입니다.")

        payload = jose_jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=settings.APPLE_CLIENT_ID,
            issuer="https://appleid.apple.com",
        )
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="유효하지 않은 애플 토큰입니다.")


# 6. 애플 로그인
@router.post("/auth/apple")
def apple_login(body: schemas.AppleLoginRequest, db: Session = Depends(get_db)):
    payload = _verify_apple_token(body.id_token)

    apple_sub = payload.get("sub")
    email = payload.get("email")

    if not apple_sub:
        raise HTTPException(status_code=400, detail="애플 토큰에서 유저 정보를 가져올 수 없습니다.")

    existing_user = db.query(models.User).filter(
        models.User.social_id == apple_sub,
        models.User.social_provider == "apple"
    ).first()

    if existing_user:
        user = existing_user
        is_new_user = False
    else:
        # 같은 이메일로 가입된 계정이 있으면 충돌 방지
        if email:
            email_user = db.query(models.User).filter(models.User.email == email).first()
            if email_user:
                raise HTTPException(status_code=400, detail="이미 해당 이메일로 가입된 계정입니다. 기존 로그인 방식을 이용해주세요.")

        user = models.User(
            email=email,
            password=None,
            nickname="",
            social_provider="apple",
            social_id=apple_sub,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new_user = True
        if posthog_client:
            posthog_client.capture(
                distinct_id=str(user.user_id),
                event="user_signed_up",
                properties={"provider": "apple", "has_nickname": bool(user.nickname)},
            )

    access_token = create_access_token(data={"sub": str(user.user_id)})
    if posthog_client:
        posthog_client.capture(
            distinct_id=str(user.user_id),
            event="user_logged_in",
            properties={"provider": "apple"},
        )
    return success({"accessToken": access_token, "tokenType": "bearer", "isNewUser": is_new_user})


# 6-1. 애플 계정 연결 (기존 로그인 유저)
@router.post("/auth/apple/connect")
def apple_connect(body: schemas.AppleLoginRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if current_user.social_provider == "apple":
        raise HTTPException(status_code=400, detail="이미 애플 계정이 연결되어 있습니다.")

    payload = _verify_apple_token(body.id_token)

    apple_sub = payload.get("sub")
    if not apple_sub:
        raise HTTPException(status_code=400, detail="애플 토큰에서 유저 정보를 가져올 수 없습니다.")

    already_linked = db.query(models.User).filter(
        models.User.social_id == apple_sub,
        models.User.social_provider == "apple"
    ).first()
    if already_linked:
        raise HTTPException(status_code=400, detail="이미 다른 계정에 연결된 애플 계정입니다.")

    current_user.social_provider = "apple"
    current_user.social_id = apple_sub
    db.commit()

    return success(message="애플 계정이 연결되었습니다.")


# 7. 내 프로필 조회
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

# 4. 닉네임 수정
@router.patch("/setting/nickname")
def update_nickname(data: schemas.NicknameUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.nickname = data.nickname
    db.commit()
    db.refresh(current_user)
    return success({"nickname": current_user.nickname})

# 5. 페르소나(SBTI) 결과 저장/조회
@router.post("/setting/profile/persona")
def update_fbti(data: schemas.FbtiFinalResult, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.persona_type = json.dumps(data.model_dump(), ensure_ascii=False)
    db.commit()
    db.refresh(current_user)
    if posthog_client:
        posthog_client.capture(
            distinct_id=str(current_user.user_id),
            event="persona_updated",
        )
    return success({"persona": data.model_dump()})

@router.get("/profile/persona", response_model=schemas.PersonaRead)
def get_my_persona(current_user: models.User = Depends(get_current_user)):
    if not current_user.persona_type:
        return success({"persona": None})
    try:
        return success({"persona": json.loads(current_user.persona_type)})
    except Exception:
        return success({"persona": None})

# 7. 나의 취향 저장/조회
@router.post("/profile/style")
def update_style(data: schemas.StyleUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.style = [s.value for s in data.style]
    db.commit()
    return success({"style": current_user.style})

@router.get("/profile/style")
def get_style(current_user: models.User = Depends(get_current_user)):
    return success({"style": current_user.style or []})

# 8. 온보딩
@router.post("/initial-question")
def submit_onboarding(data: schemas.OnboardingCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.age_group = data.age_group.value
    current_user.style = [s.value for s in data.style]
    current_user.regret_frequency = data.regret_frequency.value

    if data.regret_frequency == schemas.RegretFrequency.NONE:
        current_user.regret_reasons = []
    else:
        reasons = [r for r in (data.regret_reasons or [])]
        if data.regret_reason_custom:
            reasons.append(data.regret_reason_custom)
        current_user.regret_reasons = reasons

    db.commit()
    return success(message="온보딩이 완료되었습니다.")

# 8. 나의 옷장 통계 조회
@router.get("/profile/closet")
def get_closet_stats(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    # status 기준: PURCHASED = 고심 끝에 구매한 옷, ABANDONED = 아쉽지만 포기한 옷 (고민 중은 제외)
    base = db.query(UserProduct).outerjoin(
        Product, UserProduct.product_id == Product.product_id
    ).filter(UserProduct.user_id == current_user.user_id)

    bought = base.filter(UserProduct.status == "PURCHASED").all()
    dropped = base.filter(UserProduct.status == "ABANDONED").all()

    bought_count = len(bought)
    bought_price = 0
    for up in bought:
        prod = db.query(Product).filter(Product.product_id == up.product_id).first()
        if prod and prod.price is not None:
            bought_price += int(prod.price)

    dropped_count = len(dropped)
    dropped_price = 0
    for up in dropped:
        prod = db.query(Product).filter(Product.product_id == up.product_id).first()
        if prod and prod.price is not None:
            dropped_price += int(prod.price)

    return success({
        "boughtCount": bought_count,
        "boughtPrice": bought_price,
        "droppedCount": dropped_count,
        "droppedPrice": dropped_price,
    })


# 문의하기
@router.post("/profile/inquiry")
def create_inquiry(body: schemas.InquiryCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    inquiry = models.Inquiry(user_id=current_user.user_id, content=body.content)
    db.add(inquiry)
    db.commit()
    return success(message="문의가 접수되었습니다.")
