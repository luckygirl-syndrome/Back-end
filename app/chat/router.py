from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.core.redis_client import redis_client
from app.users.models import User
from app.users.router import get_current_user
from app.chat import service
from app.chat.schemas import ChatMessageRequest, PriceFeeling, Interest, Discovery

router = APIRouter(prefix="/api/chat", tags=["chat"])

_200 = lambda result: {"200": {"content": {"application/json": {"example": {"isSuccess": True, "code": "200", "message": "OK", "result": result}}}}}


@router.post(
    "/start",
    summary="상품 이미지 분석 및 채팅 세션 생성",
    description="상품 스크린샷(1장 이상)과 설문 답변을 받아 Gemini로 분석 후 충동 점수·취향 일치 점수를 계산하고 채팅 세션을 생성합니다.",
    responses=_200({
        "user_product_id": 1,
        "product_name": "Healing Off-Shoulder Tee",
        "price": 45500,
        "product_info": ["상품명: Healing Off-Shoulder Tee", "가격: 65,000원 → 45,500원 (30% 할인)"],
        "confirmed_sentences": ["이 유저는 스트릿 스타일을 좋아합니다.", "충동 점수는 54점이고 일치하는 점수는 74점입니다."],
        "user_type": {"code": "DIMO", "axis_summary": ["[확신 방식/I] ..."], "priority_rule": "..."},
        "impulse_score": 54,
        "match_score": 74,
        "product_img": "data:image/jpeg;base64,...",
        "product_imgs": ["data:image/jpeg;base64,...", "data:image/jpeg;base64,..."],
    }),
)
async def start_chat(
    images: List[UploadFile] = File(..., description="상품 스크린샷 (1장 이상)"),
    price_feeling: PriceFeeling = Form(...),
    interest: Interest = Form(...),
    discovery: Discovery = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await service.analyze_and_create_session(
        db=db,
        images=images,
        user=current_user,
        price_feeling=price_feeling.value,
        interest=interest.value,
        discovery=discovery.value,
    )
    if not result:
        raise HTTPException(status_code=500, detail="이미지 분석에 실패했습니다.")
    return success(result)


@router.get(
    "/list",
    summary="채팅 목록 조회",
    description="cursor 기반 무한스크롤. cursor 없으면 첫 페이지, cursor에 이전 응답의 nextCursor 값을 넘기면 다음 페이지.",
    responses=_200({
        "items": [
            {
                "user_product_id": 1,
                "product_name": "Healing Off-Shoulder Tee",
                "product_img": "data:image/jpeg;base64,...",
                "price": 45500,
                "status": "PENDING",
                "statusLabel": "고민 중",
                "impulse_score": 54,
                "match_score": 74,
                "requested_at": "2026-06-06T12:00:00",
                "has_unread": False,
            }
        ],
        "nextCursor": 5,
        "hasNext": True,
    }),
)
def get_chat_list(
    cursor: Optional[int] = None,
    size: int = 20,
    sort: str = "newest",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = service.get_chat_list(db, current_user.user_id, cursor=cursor, size=size, sort=sort)
    return success(result)


@router.get(
    "/{user_product_id}",
    summary="채팅방 상세 조회",
    description="특정 채팅 세션의 분석 결과와 대화 내역을 반환합니다.",
    responses=_200({
        "user_product_id": 1,
        "product_name": "Healing Off-Shoulder Tee",
        "product_img": "data:image/jpeg;base64,...",
        "status": "PENDING",
        "statusLabel": "고민 중",
        "isChatEnded": False,
        "finalCode": None,
        "finalScore": None,
        "impulse_score": 54,
        "match_score": 74,
        "hasReview": False,
        "messages": [
            {"role": "assistant", "content": "일주일째 눈에 밟히고...", "created_at": "2026-06-06T12:00:00"},
            {"role": "user", "content": "이거 살까?", "created_at": "2026-06-06T12:01:00"},
        ],
    }),
)
def get_chat_room(
    user_product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    room = service.get_chat_room(db, user_product_id, current_user.user_id)
    if not room:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    service.mark_chat_read(db, user_product_id, current_user.user_id)
    return success(room)


@router.post(
    "/{user_product_id}/greet",
    summary="첫 봇 인사 생성",
    description="채팅방에 메시지가 없을 때 호출하면 첫 번째 봇 메시지를 생성하고 저장합니다.",
    responses=_200({
        "reply": "일주일째 눈에 밟히고 가격도 걸리지 않는데, 평소 분위기랑은 조금 다른 쪽이라 망설이는 건지 궁금해.",
        "is_exit": False,
        "final_code": None,
    }),
)
async def greet(
    user_product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await service.generate_greeting(db, user_product_id, current_user.user_id)
    if not result:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    return success(result)


@router.post(
    "/{user_product_id}/message",
    summary="채팅 메시지 전송",
    description="유저 메시지를 보내고 봇 답변을 받습니다. multipart/form-data로 message 텍스트와 images 파일을 함께 전송할 수 있습니다.",
    responses=_200({
        "reply": "지금은 옷 자체가 별로라서 망설이는 게 아니라, 네 평소 분위기랑 이 무드가 얼마나 자연스럽게 이어질지가 더 큰 고민 같아.",
        "is_exit": False,
        "finalCode": None,
        "finalScore": None,
    }),
)
async def send_message(
    user_product_id: int,
    message: str = Form(...),
    images: Optional[List[UploadFile]] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await service.send_message(
        db, user_product_id, current_user.user_id, message, images=images or []
    )
    if not result:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    return success(result)


@router.post(
    "/{user_product_id}/exit",
    summary="채팅 종료",
    description="채팅 종료하기 버튼을 누르면 호출합니다. LLM이 대화를 분석해 최종 CODE와 또바바 점수를 반환합니다.",
    responses=_200({
        "reply": "오늘 대화 잘 했어. 결정이 어렵겠지만 네 마음이 답이야.",
        "finalCode": "BUY_CONFIDENT_GROUNDED",
        "finalScore": 73,
    }),
)
async def exit_chat(
    user_product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    redis_client.delete(f"active_chat:{current_user.user_id}")
    result = await service.exit_chat(db, user_product_id, current_user.user_id)
    if not result:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    return success(result)


@router.delete(
    "/{user_product_id}",
    summary="채팅 삭제 (소프트 딜리트)",
    description="채팅방을 숨김 처리합니다. 실제 데이터는 삭제되지 않습니다.",
)
def delete_chat(
    user_product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.products.models import UserProduct
    up = db.query(UserProduct).filter(
        UserProduct.user_product_id == user_product_id,
        UserProduct.user_id == current_user.user_id,
    ).first()
    if not up:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    up.is_hidden = True
    db.commit()
    return success(None)


@router.post("/{user_product_id}/active", summary="채팅방 입장 알림 (알림 억제)")
def enter_chat_room(
    user_product_id: int,
    current_user: User = Depends(get_current_user),
):
    redis_client.setex(f"active_chat:{current_user.user_id}", 3600, user_product_id)
    return success({"message": "채팅방 입장"})


@router.delete("/{user_product_id}/active", summary="채팅방 퇴장 알림 (알림 재개)")
def leave_chat_room(
    user_product_id: int,
    current_user: User = Depends(get_current_user),
):
    redis_client.delete(f"active_chat:{current_user.user_id}")
    return success({"message": "채팅방 퇴장"})
