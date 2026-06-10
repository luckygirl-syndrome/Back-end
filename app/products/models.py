from sqlalchemy import Column, String, DateTime, Integer, Float, Text, BigInteger, Boolean
from sqlalchemy.dialects.postgresql import JSONB
import datetime

from app.core.database import Base

class Product(Base):
    __tablename__ = "products"

    product_id = Column(BigInteger, primary_key=True, index=True)
    product_name = Column(String(255))
    category = Column(String(50))
    price = Column(Integer)
    discount_rate = Column(Float)
    is_direct_shipping = Column(Boolean)
    free_shipping = Column(Boolean)
    review_count = Column(Integer)
    review_score = Column(Float)
    product_likes = Column(String(255))
    platform = Column(String(50))
    product_img = Column(Text)
    product_url = Column(String(2048), nullable=True)

    # AI 분석용 심리 축 6개
    sim_temptation = Column(Boolean)
    sim_trend_hype = Column(Boolean)
    sim_fit_anxiety = Column(Boolean)
    sim_quality_logic = Column(Boolean)
    sim_bundle = Column(Boolean)
    sim_confidence = Column(Boolean)
    
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

class UserProduct(Base):
    __tablename__ = "user_product"

    user_product_id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    product_id = Column(BigInteger, nullable=False)
    requested_at = Column(DateTime, default=datetime.datetime.now)
    completed_at = Column(DateTime)
    duration_ms = Column(Integer)
    status = Column(String(50))
    user_type = Column(Text)
    impulse_score = Column(Integer)
    final_score = Column(Integer)
    preference_score = Column(Integer, default=50)
    is_purchased = Column(Boolean)
    prompt_data = Column(JSONB)
    feedback_text = Column(Text)
    feedback_rating = Column(Integer)
    final_code = Column(String(50), nullable=True)
    is_returned = Column(Boolean, nullable=True)
    satisfaction = Column(String(50), nullable=True)
    review = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)