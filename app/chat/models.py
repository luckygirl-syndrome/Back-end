from sqlalchemy import Column, String, DateTime, Text, ForeignKey, BigInteger, Index
from app.core.database import Base
from datetime import datetime

class Chat(Base):
    __tablename__ = "chat_log"

    chat_id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    user_product_id = Column(BigInteger, ForeignKey("user_product.user_product_id"), nullable=False)
    role = Column(String(20), nullable=False) # "user" / "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_chat_log_user_product_id", "user_product_id"),
    )