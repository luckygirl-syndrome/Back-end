import asyncio
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
        up = db.query(UserProduct).filter(UserProduct.user_product_id == user_product_id).first()
        product = db.query(Product).filter(Product.product_id == up.product_id).first() if up else None
        product_name = product.product_name if product else ""
        preview = reply[:50] + "..." if len(reply) > 50 else reply
        body = preview
        send_push_to_user(db, user_id, title="또바바", body=body, save_to_inbox=True, notification_type="chat", user_product_id=user_product_id)
    except Exception as e:
        _logger.warning(f"채팅 FCM 알림 실패 (user={user_id}): {e}")


_STATUS_LABEL = {
    "PURCHASED": "구매 완료",
    "ABANDONED": "구매 포기",
    "PENDING": "고민 중",
}

_CODE_ADJUSTMENT = {
    "BUY_CONFIDENT_GROUNDED": 20,
    "BUY_CONDITIONALLY_READY": 15,
    "NEUTRAL_EXPLORING": 0,
    "HOLD_REASONABLE": -10,
    "IMPULSE_JUSTIFICATION": -25,
    "LOW_USE_CLARITY": -25,
}


def _calc_final_score(impulse_score: int, match_score: int, final_code: str) -> int:
    adjustment = _CODE_ADJUSTMENT.get(final_code, 0)
    raw = (match_score - impulse_score) / 2 + adjustment
    return max(0, min(100, round((raw + 60) / 120 * 100)))

FIRST_TURN_TRIGGER = "[TURN_MODE:first]"


def _parse_code(text: str) -> Optional[str]:
    m = re.search(r"CODE:\s*(\w+)", text)
    return m.group(1) if m else None

_processor: Optional[AllInputVisionProcessor] = None


def get_processor() -> AllInputVisionProcessor:
    global _processor
    if _processor is None:
        _processor = AllInputVisionProcessor()
    return _processor


def _parse_product_info(product_info: List[str]) -> Tuple[str, int, Optional[int], Optional[float]]:
    """product_info 리스트에서 product_name, price, discounted_price, discount_rate 파싱."""
    product_name = "알 수 없음"
    price = 0
    discounted_price = None
    discount_rate = None

    if len(product_info) > 0:
        product_name = product_info[0].removeprefix("상품명: ").strip()

    if len(product_info) > 1:
        price_str = product_info[1]
        m = re.search(r"([\d,]+)원", price_str)
        if m:
            price = int(m.group(1).replace(",", ""))
        m2 = re.search(r"→\s*([\d,]+)원", price_str)
        if m2:
            discounted_price = int(m2.group(1).replace(",", ""))
        m3 = re.search(r"(\d+)% 할인", price_str)
        if m3:
            discount_rate = float(m3.group(1))

    return product_name, price, discounted_price, discount_rate


def _to_int_or_none(value) -> Optional[int]:
    try:
        return int(float(value)) if value is not None else None
    except (ValueError, TypeError):
        return None


def _to_float_or_none(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


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

    from app.core.s3 import upload_image

    try:
        # 이미지 임시 저장 + S3 업로드
        image_paths = []
        image_url_list = []
        for i, img in enumerate(images):
            content = await img.read()
            suffix = Path(img.filename or "image.jpg").suffix or ".jpg"
            mime = {"jpg": "image/jpeg", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(suffix, "image/jpeg")
            path = tmp_img_dir / f"img_{i}{suffix}"
            path.write_bytes(content)
            image_paths.append(path)
            url = await asyncio.to_thread(upload_image, content, mime)
            image_url_list.append(url)

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

    product_name, price, discounted_price, discount_rate = _parse_product_info(result.get("product_info", []))
    impulse_score, match_score = _extract_scores(result.get("confirmed_sentences", []))

    # review 원본값은 Product 컬럼에만 저장 — 챗봇은 confirmed_sentences의 리뷰 문장을 읽으므로 prompt_data에서는 제외
    review_count = _to_int_or_none(result.pop("review_count", None))
    review_score = _to_float_or_none(result.pop("review_score", None))

    # Product 레코드 생성
    product = Product(
        product_name=product_name,
        price=price,
        discounted_price=discounted_price,
        discount_rate=discount_rate,
        review_count=review_count,
        review_score=review_score,
        product_img=json.dumps(image_url_list, ensure_ascii=False),
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
        "product_img": image_url_list[0] if image_url_list else None,
        "product_imgs": image_url_list,
    }


def get_chat_list(db: Session, user_id: int, cursor: Optional[int] = None, size: int = 20, sort: str = "newest") -> dict:
    from sqlalchemy import select, func, and_
    from sqlalchemy.orm import load_only

    if sort in ("price_asc", "price_desc"):
        query = (
            db.query(UserProduct, Product)
            .join(Product, UserProduct.product_id == Product.product_id)
            .filter(UserProduct.user_id == user_id)
            .filter(UserProduct.is_hidden.isnot(True))
        )
        if cursor is not None:
            query = query.filter(UserProduct.user_product_id < cursor)
        order = (Product.price.asc(), UserProduct.user_product_id.desc()) if sort == "price_asc" \
            else (Product.price.desc(), UserProduct.user_product_id.desc())
        rows = query.order_by(*order).limit(size + 1).all()
        has_next = len(rows) > size
        rows = rows[:size]
        if not rows:
            return {"items": [], "nextCursor": None, "hasNext": False}
        user_products = [up for up, _ in rows]
        product_map = {prod.product_id: prod for _, prod in rows}
    else:
        query = db.query(UserProduct).filter(UserProduct.user_id == user_id, UserProduct.is_hidden.isnot(True))
        if sort == "oldest":
            if cursor is not None:
                query = query.filter(UserProduct.user_product_id > cursor)
            order = UserProduct.user_product_id.asc()
        else:
            if cursor is not None:
                query = query.filter(UserProduct.user_product_id < cursor)
            order = UserProduct.user_product_id.desc()
        user_products = query.order_by(order).limit(size + 1).all()
        has_next = len(user_products) > size
        user_products = user_products[:size]
        if not user_products:
            return {"items": [], "nextCursor": None, "hasNext": False}
        product_ids = [up.product_id for up in user_products]
        products = (
            db.query(Product)
            .filter(Product.product_id.in_(product_ids))
            .options(load_only(Product.product_id, Product.product_name, Product.price, Product.discounted_price, Product.discount_rate, Product.product_img))
            .all()
        )
        product_map = {p.product_id: p for p in products}

    user_product_ids = [up.user_product_id for up in user_products]

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
            "discount_price": product.discounted_price if product and product.discounted_price else (product.price if product else 0),
            "discount_rate": product.discount_rate if product else None,
            "status": up.status,
            "statusLabel": _STATUS_LABEL.get(up.status or "", "고민 중"),
            "impulse_score": up.impulse_score,
            "match_score": up.preference_score,
            "requested_at": up.requested_at.isoformat() if up.requested_at else None,
            "has_unread": has_unread,
        })

    next_cursor = user_products[-1].user_product_id if has_next else None
    return {"items": items, "nextCursor": next_cursor, "hasNext": has_next}


def get_chat_room(db: Session, user_product_id: int, user_id: int) -> Optional[dict]:
    up = (
        db.query(UserProduct)
        .filter(
            UserProduct.user_product_id == user_product_id,
            UserProduct.user_id == user_id,
        )
        .first()
    )
    if not up or up.is_hidden:
        return None

    from sqlalchemy.orm import load_only
    product = (
        db.query(Product)
        .filter(Product.product_id == up.product_id)
        .options(load_only(Product.product_name, Product.price, Product.discounted_price, Product.discount_rate, Product.product_img))
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
        "original_price": product.price if product else None,
        "discount_price": product.discounted_price if product and product.discounted_price else (product.price if product else 0),
        "discount_rate": product.discount_rate if product else None,
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
                "images": m.images or [],
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


def _save_message(db: Session, user_id: int, user_product_id: int, role: str, content: str, images: list = None) -> None:
    db.add(Chat(user_id=user_id, user_product_id=user_product_id, role=role, content=content, images=images or []))
    db.commit()


async def generate_greeting(db: Session, user_product_id: int, user_id: int) -> Optional[dict]:
    from app.chat.chatbot_deepseek import build_system_prompt, call_deepseek, extract_axis_tags
    from app.core.redis_client import redis_client

    up = (
        db.query(UserProduct)
        .filter(UserProduct.user_product_id == user_product_id, UserProduct.user_id == user_id)
        .first()
    )
    if not up or not up.prompt_data:
        return None

    # 이미 메시지가 있으면 첫 번째 assistant 메시지를 그대로 반환
    existing = (
        db.query(Chat)
        .filter(Chat.user_product_id == user_product_id, Chat.role == "assistant")
        .order_by(Chat.chat_id.asc())
        .first()
    )
    if existing and existing.content:
        return {"reply": existing.content, "is_exit": False, "final_code": None}

    # 동시 중복 호출 방지: Redis 락 (30초 TTL)
    lock_key = f"greet_lock:{user_product_id}"
    acquired = redis_client.set(lock_key, 1, nx=True, ex=30)
    if not acquired:
        # 락 획득 실패 → 다른 요청이 생성 중이므로 잠시 후 DB에서 읽어 반환
        await asyncio.sleep(2)
        existing = (
            db.query(Chat)
            .filter(Chat.user_product_id == user_product_id, Chat.role == "assistant")
            .order_by(Chat.chat_id.asc())
            .first()
        )
        return {"reply": existing.content if existing else None, "is_exit": False, "final_code": None}

    system_prompt = build_system_prompt(up.prompt_data)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": FIRST_TURN_TRIGGER},
    ]

    try:
        raw_reply = await asyncio.to_thread(call_deepseek, messages, up.prompt_data)
    except Exception as e:
        _logger.error(f"[GREET] DeepSeek 호출 실패 (user_product_id={user_product_id}): {e}")
        redis_client.delete(lock_key)
        return None
    reply, _axis, _status = extract_axis_tags(raw_reply)
    _save_message(db, user_id, user_product_id, "assistant", reply)
    _notify_chat(db, user_id, user_product_id, reply)
    redis_client.delete(lock_key)
    return {"reply": reply, "is_exit": False, "final_code": None}


async def send_message(
    db: Session, user_product_id: int, user_id: int, message: str, images: list = None
) -> Optional[dict]:
    from app.chat.chatbot_deepseek import (
        build_system_prompt, call_deepseek, extract_axis_tags,
        decide_should_end, call_deepseek_exit,
        client as deepseek_client, END_DECISION_MODEL,
    )
    from fastapi import HTTPException, UploadFile
    from app.core.s3 import upload_image

    up = (
        db.query(UserProduct)
        .filter(UserProduct.user_product_id == user_product_id, UserProduct.user_id == user_id)
        .first()
    )
    if not up or not up.prompt_data:
        return None

    # 이미 종료된 채팅방은 메시지 전송 차단
    if up.final_code is not None:
        raise HTTPException(status_code=400, detail="이미 종료된 채팅입니다.")

    # 이미지 S3 업로드
    image_urls = []
    for img in (images or []):
        try:
            file_bytes = await img.read()
            mime = img.content_type or "image/jpeg"
            url = await asyncio.to_thread(upload_image, file_bytes, mime, "chat")
            image_urls.append(url)
        except Exception as e:
            _logger.warning(f"채팅 이미지 업로드 실패: {e}")

    system_prompt = build_system_prompt(up.prompt_data)
    history = _build_history(db, user_product_id)

    _save_message(db, user_id, user_product_id, "user", message, images=image_urls)
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": f"[TURN_MODE:free]\n{message}"}]

    try:
        reply = await asyncio.to_thread(call_deepseek, messages, up.prompt_data)
    except Exception as e:
        _logger.error(f"DeepSeek 호출 실패 (user_product_id={user_product_id}): {e}")
        # 저장한 유저 메시지 rollback
        db.query(Chat).filter(
            Chat.user_product_id == user_product_id,
            Chat.role == "user",
            Chat.content == message,
        ).order_by(Chat.chat_id.desc()).limit(1).delete(synchronize_session=False)
        db.commit()
        raise HTTPException(status_code=503, detail="AI 응답 생성에 실패했습니다. 다시 시도해주세요.")

    final_code = _parse_code(reply)
    reply, _axis, _status = extract_axis_tags(reply)
    clean_reply = re.sub(r"\n?CODE:\s*\w+\s*$", "", reply).strip()

    if clean_reply:
        _save_message(db, user_id, user_product_id, "assistant", clean_reply)
        _notify_chat(db, user_id, user_product_id, clean_reply)

    if final_code:
        up.final_code = final_code
        up.final_score = _calc_final_score(up.impulse_score or 0, up.preference_score or 0, final_code)
        up.scored_at = datetime.now()
        db.commit()
        return {
            "reply": clean_reply,
            "is_exit": True,
            "finalCode": final_code,
            "finalScore": up.final_score,
        }

    # 종료 판단 (또바가 먼저 종료 트리거)
    updated_history = _build_history(db, user_product_id)
    end_messages = [{"role": "system", "content": system_prompt}] + updated_history
    end_decision = await asyncio.to_thread(
        decide_should_end,
        deepseek_client,
        END_DECISION_MODEL,
        end_messages,
        message,
        clean_reply,
        set(),
    )
    _logger.info(f"[END_DECISION] user_product_id={user_product_id} result={end_decision}")

    if end_decision.get("should_end"):
        # 마지막 메시지는 이미 저장된 일반 답변(clean_reply) 하나만 유지 —
        # 별도 작별인사를 만들면 또바가 한 턴에 두 마디를 보내게 됨
        try:
            exit_result = await asyncio.to_thread(call_deepseek_exit, end_messages, up.prompt_data)
            exit_code = _parse_code(exit_result)
        except Exception as e:
            _logger.error(f"[END_DECISION] exit flow 실패 (user_product_id={user_product_id}): {e}")
            exit_code = None

        if not exit_code:
            exit_code = "NEUTRAL_EXPLORING"

        up.final_code = exit_code
        up.final_score = _calc_final_score(up.impulse_score or 0, up.preference_score or 0, exit_code)
        up.scored_at = datetime.now()
        db.commit()

        return {
            "reply": clean_reply,
            "is_exit": True,
            "finalCode": exit_code,
            "finalScore": up.final_score,
        }

    db.commit()
    return {
        "reply": clean_reply,
        "is_exit": False,
        "finalCode": None,
        "finalScore": None,
    }


async def exit_chat(db: Session, user_product_id: int, user_id: int) -> Optional[dict]:
    from app.chat.chatbot_deepseek import build_system_prompt, call_deepseek_exit, call_deepseek_farewell

    up = (
        db.query(UserProduct)
        .filter(UserProduct.user_product_id == user_product_id, UserProduct.user_id == user_id)
        .first()
    )
    if not up or not up.prompt_data:
        return None

    # 이미 종료된 경우 마지막 assistant 메시지와 함께 반환 (중복 호출 방지)
    if up.final_code is not None:
        last_msg = (
            db.query(Chat)
            .filter(Chat.user_product_id == user_product_id, Chat.role == "assistant")
            .order_by(Chat.chat_id.desc())
            .first()
        )
        if last_msg and last_msg.content:
            return {
                "reply": last_msg.content,
                "finalCode": up.final_code,
                "finalScore": up.final_score,
            }

    system_prompt = build_system_prompt(up.prompt_data)
    history = _build_history(db, user_product_id)
    messages = [{"role": "system", "content": system_prompt}] + history

    reply = None
    try:
        exit_result, farewell = await asyncio.gather(
            asyncio.to_thread(call_deepseek_exit, messages, up.prompt_data),
            asyncio.to_thread(call_deepseek_farewell, messages),
        )
        final_code = _parse_code(exit_result)
        _logger.info(f"[EXIT] exit result: {repr(exit_result)} | parsed code: {final_code}")
        reply = farewell
    except Exception as e:
        _logger.error(f"[EXIT] DeepSeek 호출 실패 (user_product_id={user_product_id}): {e}")
        final_code = None

    if not final_code:
        final_code = "NEUTRAL_EXPLORING"

    if reply:
        _save_message(db, user_id, user_product_id, "assistant", reply)

    up.final_code = final_code
    up.final_score = _calc_final_score(up.impulse_score or 0, up.preference_score or 0, final_code)
    up.scored_at = datetime.now()
    db.commit()

    _notify_chat(db, user_id, user_product_id, reply or "")

    return {
        "reply": reply,
        "finalCode": final_code,
        "finalScore": up.final_score,
    }
