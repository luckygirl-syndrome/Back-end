from sqlalchemy import Column, Integer, String, Float, BigInteger, Text, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.core.database import Base

class User(Base):
    __tablename__ = "users"

    # 언니가 원했던 최신 DB 구조
    user_id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    nickname = Column(String(50))
    email = Column(String(255), unique=True, index=True)
    password = Column(String(100), nullable=True)
    social_provider = Column(String(20), nullable=True)
    social_id = Column(String(255), nullable=True)
    persona_type = Column(String(50), nullable=True)
    profile_img = Column(String(255), default="0")

    favorite_shops = Column(JSONB, nullable=True)
    style = Column(JSONB, nullable=True)
    age_group = Column(String(20), nullable=True)
    regret_frequency = Column(String(30), nullable=True)
    regret_reasons = Column(JSONB, nullable=True)

    mu_like = Column(JSONB, nullable=True)
    mu_regret = Column(JSONB, nullable=True)
    n_pos = Column(Integer, default=0)
    n_neg = Column(Integer, default=0)


class Inquiry(Base):
    __tablename__ = "inquiries"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=True)
    content = Column(Text, nullable=False)
    reply_email = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
