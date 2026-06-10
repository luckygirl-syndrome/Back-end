from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.response import success
from app.users.models import User
from app.users.router import get_current_user
from . import service
from .schemas import FcmTokenRegister, BroadcastRequest

router = APIRouter(prefix="/api/notifications", tags=["notifications"])



@router.post("/fcm-token", summary="FCM 토큰 등록")
def register_fcm_token(
    body: FcmTokenRegister,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.register_token(db, current_user.user_id, body.token, body.device_type)
    return success({"message": "토큰 등록 완료"})


@router.delete("/fcm-token", summary="FCM 토큰 삭제 (로그아웃 시)")
def delete_fcm_token(
    body: FcmTokenRegister,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.delete_token(db, current_user.user_id, body.token)
    return success({"message": "토큰 삭제 완료"})


@router.get("/", summary="알림 목록 조회")
def get_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items = service.get_notifications(db, current_user.user_id)
    return success(items)


@router.patch("/{notification_id}/read", summary="알림 읽음 처리")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.mark_read(db, current_user.user_id, notification_id)
    return success({"message": "읽음 처리 완료"})


@router.patch("/read-all", summary="전체 알림 읽음 처리")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.mark_all_read(db, current_user.user_id)
    return success({"message": "전체 읽음 처리 완료"})


@router.post("/admin/broadcast", summary="공지사항 전체 발송 (관리자 전용)")
def broadcast_notification(
    body: BroadcastRequest,
    x_admin_key: str = Header(...),
    db: Session = Depends(get_db),
):
    if not settings.ADMIN_SECRET_KEY or x_admin_key != settings.ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="관리자 권한 없음")
    service.broadcast_announcement(db, body.title, body.body)
    return success({"message": "공지사항 발송 완료"})
