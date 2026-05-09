from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from . import models, schemas
from datetime import datetime, timedelta
from jose import jwt, JWTError
from app.core.config import settings
from fastapi.security import APIKeyHeader
import json
from app.core.security import create_access_token, decode_access_token
from app.products.models import UserProduct, Product
from sqlalchemy import func
from app.core.observability import posthog_client

router = APIRouter(prefix="/api", tags=["유저 관리"])
api_key_header = APIKeyHeader(name="Authorization")

# 인증 함수: 토큰을 읽어서 현재 유저 객체를 반환
def get_current_user(token: str = Depends(api_key_header), db: Session = Depends(get_db)):
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="인증 실패")
    
    email = payload.get("sub")
    user = db.query(models.User).filter(models.User.email == email).first()
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
        password=user.password, 
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
    if not user or user.password != user_data.password:
        raise HTTPException(status_code=401, detail="로그인 정보가 올바르지 않습니다.")
    
    access_token = create_access_token(data={"sub": user.email})
    if posthog_client:
        posthog_client.capture(
            distinct_id=str(user.user_id),
            event="user_logged_in",
        )
    return {"status": "success", "access_token": access_token, "token_type": "bearer"}

# 3. 내 프로필 조회
@router.get("/profile", response_model=schemas.ProfileRead)
def get_my_profile(current_user: models.User = Depends(get_current_user)):
    profile_data = {
        "nickname": current_user.nickname,
        "profile_img": str(current_user.profile_img) if current_user.profile_img else "1",
        "description": ""
    }
    
    if current_user.persona_type:
        try:
            persona_json = json.loads(current_user.persona_type)
            profile_data["description"] = persona_json.get("description", "")
        except:
            pass
            
    return profile_data

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
def update_sbti_complex(data: schemas.SbtiFinalResult, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
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
    except: return {"persona": None}

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