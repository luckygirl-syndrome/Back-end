from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.users.router import get_current_user
from app.products.parsers.item_parser import extract_features_from_image
from app.products import models, schemas
from app.users import models as user_models
from app.core.response import success

router = APIRouter(prefix="/api/products", tags=["상품 분석"])


@router.post("/parse")
async def parse_product_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    image_bytes = await file.read()
    result = extract_features_from_image(image_bytes)

    if not result or result.get("product_name") == "Error":
        raise HTTPException(status_code=400, detail=result.get("details", "분석 실패"))

    return {"status": "success", "data": result}


# 구매 후 평가
@router.post("/{product_id}/review")
def create_review(
    product_id: int,
    body: schemas.ProductReviewCreate,
    current_user: user_models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_product = db.query(models.UserProduct).filter(
        models.UserProduct.product_id == product_id,
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
