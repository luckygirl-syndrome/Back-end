import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.products.models import UserProduct, Product
from app.users import models, schemas
from app.users.fbti_types import FBTI_TYPES
from app.users.router import get_current_user

router = APIRouter(prefix="/api", tags=["프로필"])

_200 = lambda result: {"200": {"content": {"application/json": {"example": {"isSuccess": True, "code": "200", "message": "OK", "result": result}}}}}


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
        "height": current_user.height,
        "weight": current_user.weight,
    })


@router.post("/profile/fbti", summary="FBTI 결과 저장", responses=_200({"fbti": {"fbti_type": "DIMO"}}))
def update_fbti(data: schemas.FbtiFinalResult, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    from app.core.observability import posthog_client
    current_user.fbti_type = json.dumps(data.model_dump(), ensure_ascii=False)
    db.commit()
    db.refresh(current_user)
    if posthog_client:
        posthog_client.capture(distinct_id=str(current_user.user_id), event="persona_updated")
    return success({"fbti": data.model_dump()})


@router.get("/profile/fbti", summary="FBTI 결과 조회", responses=_200({"fbti": {"fbti_type": "DIMO"}}))
def get_my_persona(current_user: models.User = Depends(get_current_user)):
    if not current_user.fbti_type:
        return success({"fbti": None})
    try:
        return success({"fbti": json.loads(current_user.fbti_type)})
    except Exception:
        return success({"fbti": None})


@router.post("/profile/style", summary="스타일 저장", responses=_200({"style": ["스트릿", "캐주얼"]}))
def update_style(data: schemas.StyleUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.style = [s.value for s in data.style]
    db.commit()
    return success({"style": current_user.style})


@router.get("/profile/style", summary="스타일 조회", responses=_200({"style": ["스트릿", "캐주얼"]}))
def get_style(current_user: models.User = Depends(get_current_user)):
    return success({"style": current_user.style or []})


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
