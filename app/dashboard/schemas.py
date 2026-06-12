from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

# -------------------------------------------------------------
# Home Dashboard
# -------------------------------------------------------------
class HomeDashboardData(BaseModel):
    user_name: str
    saved_amount: int
    recent_chat_count: int
    total_chat_count: int

class HomeDashboardResponse(BaseModel):
    status: str
    data: HomeDashboardData

# -------------------------------------------------------------
# Receipts (안 산 영수증)
# -------------------------------------------------------------
class ReceiptListItem(BaseModel):
    user_product_id: int
    product_id: int
    product_name: str
    product_img: Optional[str] = None
    price: int
    discount_rate: Optional[float] = None

class ReceiptListResponse(BaseModel):
    status: str
    data: List[ReceiptListItem]

class ReceiptDetailData(BaseModel):
    user_product_id: int
    mall_name: Optional[str] = None
    brand: Optional[str] = None
    product_name: str
    product_img: Optional[str] = None
    price: int
    discount_rate: Optional[float] = None
    saved_amount: int
    completed_at: Optional[datetime] = None
    duration_days: Optional[int] = None # 고민한 기간

class ReceiptDetailResponse(BaseModel):
    status: str
    data: ReceiptDetailData

# -------------------------------------------------------------
# Stats (또바바 점수 & 통계)
# -------------------------------------------------------------
class RecentScoreItem(BaseModel):
    product_name: str
    score: int

class ConversionStats(BaseModel):
    total: int
    purchased: int

class SatisfactionStats(BaseModel):
    total: int
    satisfied: int

class StatsData(BaseModel):
    recent_scores: List[RecentScoreItem]
    purchase_conversion: ConversionStats
    satisfaction: SatisfactionStats

class StatsResponse(BaseModel):
    status: str
    data: StatsData

# -------------------------------------------------------------
# Considering (결정했나요?)
# -------------------------------------------------------------
class ConsideringListItem(BaseModel):
    user_product_id: int
    product_id: int
    product_img: Optional[str] = None
    product_name: str
    price: int
    duration_days: Optional[int] = None # 고민 중인 기간

class ConsideringListResponse(BaseModel):
    status: str
    data: List[ConsideringListItem]
    nextCursor: Optional[int] = None
    hasNext: bool = False
