from pydantic import BaseModel
from datetime import datetime


class FcmTokenRegister(BaseModel):
    token: str
    device_type: str = "ios"  # ios, android


class NotificationOut(BaseModel):
    id: int
    title: str
    body: str
    notification_type: str = "chat"
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class BroadcastRequest(BaseModel):
    title: str
    body: str
