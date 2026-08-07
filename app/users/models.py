from sqlalchemy import Column, Integer, String, Float, BigInteger, Text, DateTime, UniqueConstraint
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
    fbti_type = Column(Text, nullable=True)
    profile_img = Column(String(255), default="0")
    height = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)

    favorite_shops = Column(JSONB, nullable=True)
    style = Column(JSONB, nullable=True)
    age_group = Column(String(20), nullable=True)
    regret_frequency = Column(String(30), nullable=True)
    regret_reasons = Column(JSONB, nullable=True)


class UserSocialProvider(Base):
    __tablename__ = "user_social_providers"
    __table_args__ = (UniqueConstraint("provider", "social_id", name="uq_provider_social_id"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    provider = Column(String(20), nullable=False)
    social_id = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Inquiry(Base):
    __tablename__ = "inquiries"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=True)
    content = Column(Text, nullable=False)
    reply_email = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
