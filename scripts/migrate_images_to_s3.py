"""
기존 DB의 base64 이미지를 S3로 마이그레이션하는 스크립트.
서버에서 직접 실행: python scripts/migrate_images_to_s3.py
"""
import sys
import os
import json
import base64
import uuid

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.products.models import Product
from app.core.s3 import get_s3_client
from app.core.config import settings


def upload_b64_to_s3(b64_str: str, index: int) -> str:
    if b64_str.startswith("http"):
        return b64_str  # 이미 S3 URL이면 스킵

    # data:image/jpeg;base64,... 형식 파싱
    if "," in b64_str:
        header, data = b64_str.split(",", 1)
        mime = header.split(":")[1].split(";")[0]
    else:
        data = b64_str
        mime = "image/jpeg"

    ext = mime.split("/")[-1].replace("jpeg", "jpg")
    key = f"products/{uuid.uuid4().hex}.{ext}"
    content = base64.b64decode(data)

    get_s3_client().put_object(
        Bucket=settings.AWS_S3_BUCKET,
        Key=key,
        Body=content,
        ContentType=mime,
    )
    return f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_S3_REGION}.amazonaws.com/{key}"


def main():
    db = SessionLocal()
    try:
        products = db.query(Product).filter(Product.product_img.isnot(None)).all()
        total = len(products)
        print(f"총 {total}개 상품 처리 시작")

        for i, product in enumerate(products):
            try:
                raw = product.product_img
                img_list = json.loads(raw) if raw else []

                # 이미 모두 S3 URL이면 스킵
                if all(url.startswith("http") for url in img_list):
                    print(f"[{i+1}/{total}] product_id={product.product_id} 이미 변환됨, 스킵")
                    continue

                new_list = []
                for j, img in enumerate(img_list):
                    url = upload_b64_to_s3(img, j)
                    new_list.append(url)

                product.product_img = json.dumps(new_list, ensure_ascii=False)
                db.commit()
                print(f"[{i+1}/{total}] product_id={product.product_id} 완료 ({len(new_list)}장)")

            except Exception as e:
                db.rollback()
                print(f"[{i+1}/{total}] product_id={product.product_id} 실패: {e}")

        print("마이그레이션 완료")
    finally:
        db.close()


if __name__ == "__main__":
    main()
