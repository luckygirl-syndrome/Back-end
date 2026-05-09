from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.users.router import get_current_user
from app.products.parsers.item_parser import extract_features_from_image

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
