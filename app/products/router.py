from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.users.router import get_current_user
from app.products import models, schemas
from app.users import models as user_models
from app.core.response import success

router = APIRouter(prefix="/api/products", tags=["상품 분석"])

_200 = lambda result: {"200": {"content": {"application/json": {"example": {"isSuccess": True, "code": "200", "message": "OK", "result": result}}}}}


# 구매 여부 업데이트
@router.post(
    "/user-product/{user_product_id}/purchase",
    summary="구매 여부 업데이트",
    description="채팅 종료 후 실제 구매 또는 포기 여부를 업데이트합니다.",
    responses=_200({"message": "성공적으로 구매 확정되었습니다."}),
)
def update_purchase(
    user_product_id: int,
    body: schemas.PurchaseStatusUpdate,
    current_user: user_models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from datetime import datetime
    up = db.query(models.UserProduct).filter(
        models.UserProduct.user_product_id == user_product_id,
        models.UserProduct.user_id == current_user.user_id,
    ).first()

    if not up:
        raise HTTPException(status_code=404, detail="해당 채팅방(상품)을 찾을 수 없습니다.")

    if body.is_purchased:
        up.is_purchased = True
        up.status = "PURCHASED"
        up.completed_at = datetime.now()
        msg = "성공적으로 구매 확정되었습니다."
    elif body.is_abandoned:
        up.is_purchased = False
        up.status = "ABANDONED"
        up.completed_at = datetime.now()
        msg = "구매 포기 처리가 완료되었습니다."
    else:
        return success({"message": "변경된 결정 사항이 없습니다."})

    db.commit()
    return success({"message": msg})


# 구매 후 평가 조회
@router.get(
    "/user-product/{user_product_id}/review",
    summary="구매 후 평가 조회",
    responses=_200({"isReturned": False, "satisfaction": "satisfied", "review": "생각보다 훨씬 좋아요!", "status": "PURCHASED"}),
)
def get_review(
    user_product_id: int,
    current_user: user_models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_product = db.query(models.UserProduct).filter(
        models.UserProduct.user_product_id == user_product_id,
        models.UserProduct.user_id == current_user.user_id,
    ).first()

    if not user_product:
        raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다.")

    return success({
        "isReturned": user_product.is_returned,
        "satisfaction": user_product.satisfaction,
        "review": user_product.review,
        "status": user_product.status,
    })


# 구매 후 평가 수정
@router.patch(
    "/user-product/{user_product_id}/review",
    summary="구매 후 평가 수정",
    responses=_200(None),
)
def update_review(
    user_product_id: int,
    body: schemas.ProductReviewCreate,
    current_user: user_models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_product = db.query(models.UserProduct).filter(
        models.UserProduct.user_product_id == user_product_id,
        models.UserProduct.user_id == current_user.user_id,
        models.UserProduct.status.in_(["PURCHASED", "RETURNED"]),
    ).first()

    if not user_product:
        raise HTTPException(status_code=404, detail="평가를 수정할 수 있는 상품을 찾을 수 없습니다.")

    user_product.is_returned = body.is_returned
    user_product.review = body.review

    if body.is_returned:
        user_product.status = "RETURNED"
        user_product.satisfaction = None
    else:
        user_product.status = "PURCHASED"
        user_product.satisfaction = body.satisfaction

    db.commit()
    return success(message="평가가 수정되었습니다.")


# 구매 후 평가 저장
@router.post(
    "/user-product/{user_product_id}/review",
    summary="구매 후 평가 저장",
    responses=_200(None),
)
def create_review(
    user_product_id: int,
    body: schemas.ProductReviewCreate,
    current_user: user_models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_product = db.query(models.UserProduct).filter(
        models.UserProduct.user_product_id == user_product_id,
        models.UserProduct.user_id == current_user.user_id,
        models.UserProduct.status == "PURCHASED",
    ).first()

    if not user_product:
        raise HTTPException(status_code=404, detail="구매 완료된 상품을 찾을 수 없습니다.")

    user_product.is_returned = body.is_returned
    user_product.review = body.review

    if body.is_returned:
        user_product.status = "RETURNED"
        user_product.satisfaction = None
    else:
        user_product.satisfaction = body.satisfaction

    db.commit()
    return success(message="평가가 저장되었습니다.")
