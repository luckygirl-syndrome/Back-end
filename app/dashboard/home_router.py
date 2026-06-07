from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.users.router import get_current_user
from app.users import models

from app.dashboard import schemas
from app.dashboard import service

router = APIRouter(prefix="/api/dashboard", tags=["Home"])

_200 = lambda result: {"200": {"content": {"application/json": {"example": {"isSuccess": True, "code": "200", "message": "OK", "result": result}}}}}


@router.get(
    "/home",
    summary="홈 대시보드",
    description="홈 화면 대시보드 데이터 조회",
    response_model=schemas.HomeDashboardResponse,
    responses=_200({"status": "success", "data": {"user_name": "또바바", "saved_amount": 120000, "recent_chat_count": 2, "total_chat_count": 10}}),
)
def get_home_dashboard(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        user_id = current_user.user_id
        return service.get_home_dashboard(db, user_id)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        print("dashboard error:", e)
        raise HTTPException(status_code=500, detail="홈 데이터를 불러오지 못했어.")


@router.get(
    "/receipts",
    summary="안 산 영수증 목록",
    description="안 산 영수증 목록 조회",
    response_model=schemas.ReceiptListResponse,
    responses=_200({"status": "success", "data": [{"user_product_id": 1, "product_id": 1, "product_name": "Healing Tee", "product_img": "data:image/jpeg;base64,...", "price": 45500, "discount_rate": 30.0}]}),
)
def get_unbought_receipts_list(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        user_id = current_user.user_id
        return service.get_unbought_receipts(db, user_id)
    except Exception as e:
        print("receipts list error:", e)
        raise HTTPException(status_code=500, detail="안 산 영수증의 목록 데이터를 불러오지 못했어.")


@router.get(
    "/receipts/{user_product_id}",
    summary="안 산 영수증 상세",
    description="특정 안 산 영수증 상세 내용 조회",
    response_model=schemas.ReceiptDetailResponse,
    responses=_200({"status": "success", "data": {"user_product_id": 1, "product_name": "Healing Tee", "price": 45500, "discount_rate": 30.0, "saved_amount": 19500, "completed_at": "2026-06-01T12:00:00", "duration_days": 7}}),
)
def get_receipt_detail(
    user_product_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        user_id = current_user.user_id
        return service.get_receipt_detail(db, user_id, user_product_id)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        print("receipt detail error:", e)
        raise HTTPException(status_code=500, detail="영수증 상세 데이터를 불러오지 못했어.")


@router.get(
    "/considering",
    summary="고민 중인 목록",
    description="결정했나요? (고민 중인) 목록 조회",
    response_model=schemas.ConsideringListResponse,
    responses=_200({"status": "success", "data": [{"user_product_id": 2, "product_id": 2, "product_name": "Wide Denim Pants", "product_img": "data:image/jpeg;base64,...", "price": 89000, "duration_days": 3}]}),
)
def get_considering_list(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        user_id = current_user.user_id
        return service.get_considering_items(db, user_id)
    except Exception as e:
        print("considering list error:", e)
        raise HTTPException(status_code=500, detail="고민 중인 목록 데이터를 불러오지 못했어.")
