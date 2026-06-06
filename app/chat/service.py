import asyncio
import functools
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.chat.final_input_json import AllInputVisionProcessor, process_scenario
from app.chat.models import Chat
from app.products.models import Product, UserProduct
from app.users.models import User

_processor: Optional[AllInputVisionProcessor] = None


def get_processor() -> AllInputVisionProcessor:
    global _processor
    if _processor is None:
        _processor = AllInputVisionProcessor()
    return _processor


def _parse_product_info(product_info: List[str]) -> Tuple[str, int, Optional[float]]:
    """product_info 리스트에서 product_name, price, discount_rate 파싱."""
    product_name = "알 수 없음"
    price = 0
    discount_rate = None

    if len(product_info) > 0:
        product_name = product_info[0].removeprefix("상품명: ").strip()

    if len(product_info) > 1:
        price_str = product_info[1]
        m = re.search(r"([\d,]+)원", price_str)
        if m:
            price = int(m.group(1).replace(",", ""))
        m2 = re.search(r"(\d+)% 할인", price_str)
        if m2:
            discount_rate = float(m2.group(1))

    return product_name, price, discount_rate


def _extract_scores(confirmed_sentences: List[str]) -> Tuple[int, int]:
    """confirmed_sentences에서 impulse_score, match_score 파싱."""
    for s in confirmed_sentences:
        m = re.search(r"충동 점수는 (\d+)점이고.*일치하는 점수는 (\d+)점", s)
        if m:
            return int(m.group(1)), int(m.group(2))
    return 0, 0


async def analyze_and_create_session(
    db: Session,
    images: List[UploadFile],
    user: User,
    price_feeling: str,
    interest: str,
    discovery: str,
) -> Optional[dict]:
    user_type = (user.fbti_type or "DUTE").strip()

    style_tags = user.style or []
    if isinstance(style_tags, str):
        try:
            style_tags = json.loads(style_tags)
        except Exception:
            style_tags = []

    tmp_img_dir = Path(tempfile.mkdtemp())
    tmp_out_dir = Path(tempfile.mkdtemp())

    try:
        # 이미지 임시 저장 (async 컨텍스트에서)
        image_paths = []
        for i, img in enumerate(images):
            content = await img.read()
            suffix = Path(img.filename or "image.jpg").suffix or ".jpg"
            path = tmp_img_dir / f"img_{i}{suffix}"
            path.write_bytes(content)
            image_paths.append(path)

        # Gemini 호출은 동기 함수라 스레드풀에서 실행
        run_fn = functools.partial(
            process_scenario,
            image_paths=image_paths,
            user_type=user_type,
            style_tags=style_tags,
            price_feeling=price_feeling,
            interest=interest,
            discovery=discovery,
            outputs_dir=tmp_out_dir,
            processor=get_processor(),
        )
        result = await asyncio.to_thread(run_fn)

    finally:
        shutil.rmtree(tmp_img_dir, ignore_errors=True)
        shutil.rmtree(tmp_out_dir, ignore_errors=True)

    if not result:
        return None

    product_name, price, discount_rate = _parse_product_info(result.get("product_info", []))
    impulse_score, match_score = _extract_scores(result.get("confirmed_sentences", []))

    # Product 레코드 생성
    product = Product(
        product_name=product_name,
        price=price,
        discount_rate=discount_rate,
    )
    db.add(product)
    db.flush()

    # UserProduct 레코드 생성
    user_product = UserProduct(
        user_id=user.user_id,
        product_id=product.product_id,
        status="PENDING",
        user_type=user_type,
        impulse_score=impulse_score,
        preference_score=match_score,
        prompt_data=result,
    )
    db.add(user_product)
    db.commit()
    db.refresh(user_product)

    return {
        "user_product_id": user_product.user_product_id,
        "product_info": result.get("product_info", []),
        "confirmed_sentences": result.get("confirmed_sentences", []),
        "user_type": result.get("user_type", {}),
        "impulse_score": impulse_score,
        "match_score": match_score,
    }


def get_chat_list(db: Session, user_id: int) -> List[dict]:
    user_products = (
        db.query(UserProduct)
        .filter(UserProduct.user_id == user_id)
        .order_by(UserProduct.requested_at.desc())
        .all()
    )

    items = []
    for up in user_products:
        product = db.query(Product).filter(Product.product_id == up.product_id).first()
        items.append({
            "user_product_id": up.user_product_id,
            "product_name": product.product_name if product else "알 수 없음",
            "product_img": product.product_img if product else None,
            "price": product.price if product else 0,
            "status": up.status,
            "impulse_score": up.impulse_score,
            "match_score": up.preference_score,
            "requested_at": up.requested_at.isoformat() if up.requested_at else None,
        })
    return items


def get_chat_room(db: Session, user_product_id: int, user_id: int) -> Optional[dict]:
    up = (
        db.query(UserProduct)
        .filter(
            UserProduct.user_product_id == user_product_id,
            UserProduct.user_id == user_id,
        )
        .first()
    )
    if not up:
        return None

    product = db.query(Product).filter(Product.product_id == up.product_id).first()

    messages = (
        db.query(Chat)
        .filter(Chat.user_product_id == user_product_id)
        .order_by(Chat.created_at.asc())
        .all()
    )

    return {
        "user_product_id": up.user_product_id,
        "product_name": product.product_name if product else "알 수 없음",
        "product_img": product.product_img if product else None,
        "status": up.status,
        "impulse_score": up.impulse_score,
        "match_score": up.preference_score,
        "prompt_data": up.prompt_data,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }
