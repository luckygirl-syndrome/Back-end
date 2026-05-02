from sqlalchemy import Column, String, DateTime, Text, ForeignKey, BigInteger
from app.core.database import Base
from datetime import datetime  # <--- 이 줄을 꼭 추가해줘!

class Chat(Base):
    __tablename__ = "chat_log"

    chat_id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    user_product_id = Column(BigInteger, ForeignKey("user_product.user_product_id"), nullable=False)
    role = Column(String(20), nullable=False) # "user" / "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)