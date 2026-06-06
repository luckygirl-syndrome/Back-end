from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.users.models import User
from app.users.router import get_current_user
from app.chat import service

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post(
    "/start",
    summary="상품 이미지 분석 및 채팅 세션 생성",
    description="""
    상품 스크린샷(1장 이상)과 설문 답변을 받아 Gemini로 분석 후
    충동 점수·취향 일치 점수를 계산하고 채팅 세션을 생성합니다.
    """,
)
async def start_chat(
    images: List[UploadFile] = File(..., description="상품 스크린샷 (1장 이상)"),
    price_feeling: str = Form(..., description="가격 체감 (예: '이 정도면 괜찮아요')"),
    interest: str = Form(..., description="관심 지속 기간 (예: '2~3일 됐어요')"),
    discovery: str = Form(..., description="발견 경로 (예: '인스타/틱톡/X 같은 SNS 보다가 발견했어요')"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await service.analyze_and_create_session(
        db=db,
        images=images,
        user=current_user,
        price_feeling=price_feeling,
        interest=interest,
        discovery=discovery,
    )
    if not result:
        raise HTTPException(status_code=500, detail="이미지 분석에 실패했습니다.")
    return success(result)


@router.get(
    "/list",
    summary="채팅 목록 조회",
    description="현재 유저의 모든 채팅 세션을 최신순으로 반환합니다.",
)
def get_chat_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items = service.get_chat_list(db, current_user.user_id)
    return success(items)


@router.get(
    "/{user_product_id}",
    summary="채팅방 상세 조회",
    description="특정 채팅 세션의 분석 결과와 대화 내역을 반환합니다.",
)
def get_chat_room(
    user_product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    room = service.get_chat_room(db, user_product_id, current_user.user_id)
    if not room:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    return success(room)
