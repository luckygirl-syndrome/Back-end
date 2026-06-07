from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.users.router import get_current_user
from app.products import models, schemas
from app.users import models as user_models
from app.core.response import success

router = APIRouter(prefix="/api/products", tags=["상품 분석"])

_200 = lambda result: {"200": {"content": {"application/json": {"example": {"isSuccess": True, "code": "200", "message": "OK", "result": result}}}}}


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
