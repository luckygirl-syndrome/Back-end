import asyncio
import base64
import functools
import json
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.chat.final_input_json import AllInputVisionProcessor, process_scenario
from app.chat.models import Chat
from app.products.models import Product, UserProduct
from app.users.models import User

import logging as _logging

_logger = _logging.getLogger(__name__)


def _notify_chat(db: Session, user_id: int, user_product_id: int, reply: str):
    """또바 메시지 → FCM 푸시 + 알림함 저장 (항상 발송, 포그라운드 처리는 프론트에서)"""
    try:
        from app.notifications.service import send_push_to_user
        preview = reply[:50] + "..." if len(reply) > 50 else reply
        send_push_to_user(db, user_id, title="또바", body=preview, save_to_inbox=True, notification_type="chat")
    except Exception as e:
        _logger.warning(f"채팅 FCM 알림 실패 (user={user_id}): {e}")


_STATUS_LABEL = {
    "PURCHASED": "구매 완료",
    "ABANDONED": "구매 포기",
    "PENDING": "고민 중",
}

_CODE_ADJUSTMENT = {
    "BUY_CONFIDENT_GROUNDED": 10,
    "BUY_CONDITIONALLY_READY": 5,
    "NEUTRAL_EXPLORING": 0,
    "HOLD_REASONABLE": -5,
    "IMPULSE_JUSTIFICATION": -10,
    "LOW_USE_CLARITY": -10,
}


def _calc_final_score(impulse_score: int, match_score: int, final_code: str) -> int:
    adjustment = _CODE_ADJUSTMENT.get(final_code, 0)
    raw = (match_score - impulse_score) / 2 + adjustment
    return max(0, min(100, round((raw + 60) / 120 * 100)))

FIRST_TURN_TRIGGER = "대화를 시작해줘. 첫 답변 규칙에 따라 2문장으로 시작해."
EXIT_TRIGGER = (
    "대화가 [EXIT] 신호로 종료됩니다. "
    "전체 대화에서 드러난 유저의 구매 판단 태도를 분석하고, "
    "반드시 'CODE: 코드명' 형식으로만 출력해."
)


def _parse_code(text: str) -> Optional[str]:
    m = re.search(r"CODE:\s*(\w+)", text)
    return m.group(1) if m else None

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
        # 이미지 임시 저장 + base64 인코딩
        image_paths = []
        image_b64_list = []
        for i, img in enumerate(images):
            content = await img.read()
            suffix = Path(img.filename or "image.jpg").suffix or ".jpg"
            mime = {"jpg": "image/jpeg", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(suffix, "image/jpeg")
            path = tmp_img_dir / f"img_{i}{suffix}"
            path.write_bytes(content)
            image_paths.append(path)
            b64 = base64.b64encode(content).decode("utf-8")
            image_b64_list.append(f"data:{mime};base64,{b64}")

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
        product_img=json.dumps(image_b64_list, ensure_ascii=False),
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
        "product_name": product_name,
        "price": price,
        "product_info": result.get("product_info", []),
        "confirmed_sentences": result.get("confirmed_sentences", []),
        "user_type": result.get("user_type", {}),
        "impulse_score": impulse_score,
        "match_score": match_score,
        "product_img": image_b64_list[0] if image_b64_list else None,
        "product_imgs": image_b64_list,
    }


def get_chat_list(db: Session, user_id: int) -> List[dict]:
    from sqlalchemy import select, func, and_
    from sqlalchemy.orm import load_only

    user_products = (
        db.query(UserProduct)
        .filter(UserProduct.user_id == user_id)
        .order_by(UserProduct.requested_at.desc())
        .all()
    )
    if not user_products:
        return []

    product_ids = [up.product_id for up in user_products]
    user_product_ids = [up.user_product_id for up in user_products]

    products = (
        db.query(Product)
        .filter(Product.product_id.in_(product_ids))
        .options(load_only(Product.product_id, Product.product_name, Product.price, Product.product_img))
        .all()
    )
    product_map = {p.product_id: p for p in products}

    # 채팅방별 마지막 assistant 메시지 한 번에 조회
    subq = (
        select(
            Chat.user_product_id,
            func.max(Chat.created_at).label("last_at"),
        )
        .where(
            and_(
                Chat.user_product_id.in_(user_product_ids),
                Chat.role == "assistant",
            )
        )
        .group_by(Chat.user_product_id)
        .subquery()
    )
    last_msg_rows = db.execute(select(subq)).fetchall()
    last_msg_map = {row.user_product_id: row.last_at for row in last_msg_rows}

    items = []
    for up in user_products:
        product = product_map.get(up.product_id)
        raw_img = product.product_img if product else None
        try:
            img_list = json.loads(raw_img) if raw_img else []
            thumbnail = img_list[0] if img_list else None
        except Exception:
            thumbnail = raw_img

        last_at = last_msg_map.get(up.user_product_id)
        has_unread = bool(last_at and (up.last_read_at is None or last_at > up.last_read_at))

        items.append({
            "user_product_id": up.user_product_id,
            "product_name": product.product_name if product else "알 수 없음",
            "product_img": thumbnail,
            "price": product.price if product else 0,
            "status": up.status,
            "statusLabel": _STATUS_LABEL.get(up.status or "", "고민 중"),
            "impulse_score": up.impulse_score,
            "match_score": up.preference_score,
            "requested_at": up.requested_at.isoformat() if up.requested_at else None,
            "has_unread": has_unread,
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

    from sqlalchemy.orm import load_only
    product = (
        db.query(Product)
        .filter(Product.product_id == up.product_id)
        .options(load_only(Product.product_name, Product.price, Product.product_img))
        .first()
    )

    messages = (
        db.query(Chat)
        .filter(Chat.user_product_id == user_product_id)
        .order_by(Chat.created_at.asc())
        .all()
    )

    raw_img = product.product_img if product else None
    try:
        img_list = json.loads(raw_img) if raw_img else []
    except Exception:
        img_list = [raw_img] if raw_img else []

    return {
        "user_product_id": up.user_product_id,
        "product_name": product.product_name if product else "알 수 없음",
        "price": product.price if product else 0,
        "product_img": img_list[0] if img_list else None,
        "product_imgs": img_list,
        "status": up.status,
        "statusLabel": _STATUS_LABEL.get(up.status or "", "고민 중"),
        "isChatEnded": up.final_code is not None,
        "finalCode": up.final_code,
        "finalScore": up.final_score,
        "impulse_score": up.impulse_score,
        "match_score": up.preference_score,
        "hasReview": up.review is not None,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


def mark_chat_read(db: Session, user_product_id: int, user_id: int) -> None:
    db.query(UserProduct).filter(
        UserProduct.user_product_id == user_product_id,
        UserProduct.user_id == user_id,
    ).update({"last_read_at": datetime.now()})
    db.commit()


def _build_history(db: Session, user_product_id: int) -> list:
    rows = (
        db.query(Chat)
        .filter(Chat.user_product_id == user_product_id)
        .order_by(Chat.created_at.asc())
        .all()
    )
    return [{"role": r.role, "content": r.content} for r in rows]


def _save_message(db: Session, user_id: int, user_product_id: int, role: str, content: str) -> None:
    db.add(Chat(user_id=user_id, user_product_id=user_product_id, role=role, content=content))
    db.commit()


async def generate_greeting(db: Session, user_product_id: int, user_id: int) -> Optional[dict]:
    from app.chat.chatbot_deepseek import build_system_prompt, call_deepseek

    up = (
        db.query(UserProduct)
        .filter(UserProduct.user_product_id == user_product_id, UserProduct.user_id == user_id)
        .first()
    )
    if not up or not up.prompt_data:
        return None

    # 이미 메시지가 있으면 중복 생성 방지
    existing = db.query(Chat).filter(Chat.user_product_id == user_product_id).first()
    if existing:
        return {"reply": None, "is_exit": False, "final_code": None}

    system_prompt = build_system_prompt(up.prompt_data)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": FIRST_TURN_TRIGGER},
    ]

    reply = await asyncio.to_thread(call_deepseek, messages)
    _save_message(db, user_id, user_product_id, "assistant", reply)
    _notify_chat(db, user_id, user_product_id, reply)
    return {"reply": reply, "is_exit": False, "final_code": None}


async def send_message(
    db: Session, user_product_id: int, user_id: int, message: str
) -> Optional[dict]:
    from app.chat.chatbot_deepseek import build_system_prompt, call_deepseek

    up = (
        db.query(UserProduct)
        .filter(UserProduct.user_product_id == user_product_id, UserProduct.user_id == user_id)
        .first()
    )
    if not up or not up.prompt_data:
        return None

    system_prompt = build_system_prompt(up.prompt_data)
    history = _build_history(db, user_product_id)

    _save_message(db, user_id, user_product_id, "user", message)
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": message}]

    reply = await asyncio.to_thread(call_deepseek, messages)

    final_code = _parse_code(reply)
    clean_reply = re.sub(r"\n?CODE:\s*\w+\s*$", "", reply).strip()

    _save_message(db, user_id, user_product_id, "assistant", clean_reply)
    _notify_chat(db, user_id, user_product_id, clean_reply)

    if final_code:
        up.final_code = final_code
        up.final_score = _calc_final_score(up.impulse_score or 0, up.preference_score or 0, final_code)
    db.commit()

    return {
        "reply": clean_reply,
        "is_exit": final_code is not None,
        "finalCode": final_code,
        "finalScore": up.final_score if final_code else None,
    }


async def exit_chat(db: Session, user_product_id: int, user_id: int) -> Optional[dict]:
    from app.chat.chatbot_deepseek import build_system_prompt, call_deepseek
    import logging as _logging

    up = (
        db.query(UserProduct)
        .filter(UserProduct.user_product_id == user_product_id, UserProduct.user_id == user_id)
        .first()
    )
    if not up or not up.prompt_data:
        return None

    system_prompt = build_system_prompt(up.prompt_data)
    history = _build_history(db, user_product_id)
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": EXIT_TRIGGER}]

    reply = await asyncio.to_thread(call_deepseek, messages)

    final_code = _parse_code(reply)
    _logging.getLogger(__name__).info(f"[EXIT] raw reply: {repr(reply)} | parsed code: {final_code}")

    if not final_code:
        final_code = "NEUTRAL_EXPLORING"

    up.final_code = final_code
    up.final_score = _calc_final_score(up.impulse_score or 0, up.preference_score or 0, final_code)
    db.commit()

    return {
        "finalCode": final_code,
        "finalScore": up.final_score,
    }
