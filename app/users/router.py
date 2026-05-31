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
    return {"status": "success", "user_id": new_user.user_id, "email": new_user.email, "nickname": new_user.nickname}

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
    return {"status": "success", "access_token": access_token, "token_type": "bearer"}

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
    return {"status": "success", "access_token": access_token, "token_type": "bearer", "is_new_user": is_new_user}

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

    return {"status": "success", "message": "구글 계정이 연결되었습니다."}

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
    return {"status": "success", "access_token": access_token, "token_type": "bearer", "is_new_user": is_new_user}

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

    return {"status": "success", "message": "카카오 계정이 연결되었습니다."}

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
    return {"status": "success", "access_token": access_token, "token_type": "bearer", "is_new_user": is_new_user}


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

    return {"status": "success", "message": "애플 계정이 연결되었습니다."}


# 7. 내 프로필 조회
@router.get("/profile", response_model=schemas.ProfileRead)
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

    return {
        "profile_data": {
            "nickname": current_user.nickname,
            "profile_img": profile_img,
            "fbti_name": fbti_name,
        }
    }

# 4. 프로필 수정 (닉네임, 이미지)
@router.patch("/setting/profile")
def update_profile(data: schemas.ProfileUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if data.nickname is not None: current_user.nickname = data.nickname
    if data.profile_img is not None: current_user.profile_img = data.profile_img
    db.commit()
    db.refresh(current_user)
    return {"status": "success", "updated_data": {"nickname": current_user.nickname, "profile_img": current_user.profile_img}}

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
    return {"status": "success", "persona": data}

@router.get("/profile/persona", response_model=schemas.PersonaRead)
def get_my_persona(current_user: models.User = Depends(get_current_user)):
    if not current_user.persona_type: return {"persona": None}
    try:
        return {"persona": json.loads(current_user.persona_type)}
    except Exception:
        return {"persona": None}

# 6. 관심 쇼핑몰 저장/조회
@router.post("/profile/shop")
def update_favorite_shops(data: schemas.UserShopsUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.favorite_shops = json.dumps(data.favorite_shops, ensure_ascii=False)
    db.commit()
    if posthog_client:
        posthog_client.capture(
            distinct_id=str(current_user.user_id),
            event="favorite_shops_updated",
            properties={"shop_count": len(data.favorite_shops)},
        )
    return {"status": "success", "favorite_shops": data.favorite_shops}

@router.get("/profile/shop")
def get_favorite_shops(current_user: models.User = Depends(get_current_user)):
    if not current_user.favorite_shops: return {"favorite_shops": []}
    return {"favorite_shops": json.loads(current_user.favorite_shops)}

# 7. 추구미 저장/조회
@router.post("/profile/chugume")
def update_chugume(data: schemas.ChugumeUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.chu_gu_me = data.chugume_type.value
    db.commit()
    return {"status": "success", "message": f"추구미가 '{current_user.chu_gu_me}'로 설정되었습니다!"}
    
@router.get("/profile/chugume")
def get_chugume(current_user: models.User = Depends(get_current_user)):
    return {"chugume_type": current_user.chu_gu_me}

# 8. 나의 옷장 통계 조회
@router.get("/profile/closet", response_model=schemas.ClosetStatsRead)
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

    return {
        "bought_count": bought_count,
        "bought_price": bought_price,
        "dropped_count": dropped_count,
        "dropped_price": dropped_price
    }