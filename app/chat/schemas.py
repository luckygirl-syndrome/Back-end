from enum import Enum

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class PriceFeeling(str, Enum):
    cheap = "저렴한 것 같아요"
    okay = "이 정도면 괜찮아요"
    pricey = "좀 비싸긴 한데 못 살 정도는 아니에요"
    too_expensive = "상품은 마음에 들지만 가격이 비싸요"


class Interest(str, Enum):
    today = "오늘 처음 봤어요"
    few_days = "2~3일 됐어요"
    a_week = "1주일 정도 됐어요"
    two_weeks = "2주 이상 고민했어요"


class Discovery(str, Enum):
    search = "쇼핑 앱에서 카테고리 검색 후 찾아보다 발견했어요"
    influencer = "유튜버/인플루언서가 입은 것을 봤어요"
    ranking = "쇼핑 앱에서 랭킹이나 유저 추천을 둘러보다 발견했어요"
    sns = "인스타/틱톡/X 같은 SNS 보다가 발견했어요"
    brand = "브랜드 계정에 신상이 추가된 걸 봤어요"



class SurveyRequest(BaseModel):
    q1: int = Field(..., description="장바구니 기간 (1-5)", example=1)
    q2: int = Field(..., description="연락 이유 (1-4)", example=3)
    q3: int = Field(..., description="구매 확신도 (1-4)", example=2)
    qc: int = Field(..., description="핵심 매력 (1-7)", example=6)

class ChatMessageRequest(BaseModel):
    message: str = Field(..., description="유저 메시지", example="이거 사고 싶어")

class ChatReply(BaseModel):
    user_product_id: int
    reply: str
    is_exit: Optional[bool] = False
    final_score: Optional[int] = None


class ChatMessageResponse(BaseModel):
    # For /messages/ endpoint
    message: str = Field(..., description="LLM 응답 메시지")
    
    # For /room/{id} endpoint (from origin/main)
    role: Optional[str] = None
    content: Optional[str] = None
    created_at: Optional[datetime] = None


class ChatListItem(BaseModel):
    user_product_id: int
    product_name: str
    product_img: Optional[str]
    discount_price: int
    discount_rate: Optional[float] = None
    last_chat_time: str  # "오늘", "어제" 등으로 변환해서 줄 거야
    status_label: str    # "구매 완료", "구매 포기", "고민 중"
    is_purchased: Optional[int]


class ChatListResponse(BaseModel):
    latest_chat: Optional[ChatListItem]
    all_chats: List[ChatListItem]


class ChatRoomDetailResponse(BaseModel):
    user_product_id: int
    product_name: str
    product_img: Optional[str]
    discount_price: int
    original_price: Optional[int] = None
    discount_rate: Optional[float] = None
    platform: Optional[str] = None  # 무신사, 지그재그, 에이블리 등
    product_url: Optional[str] = None  # 쇼핑몰 상품 링크 (상단 shop 아이콘 하이퍼링크용)
    status_label: str
    status: Optional[str] = None  # ANALYZING, PENDING, FINISHED 등 (exit 후 FINISHED → 종료 배너)
    final_score: Optional[int] = None
    messages: List[ChatMessageResponse]

    class Config:
        from_attributes = True
