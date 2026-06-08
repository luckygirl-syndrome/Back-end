from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.users.models import User
from app.users.router import get_current_user
from . import service
from .schemas import FcmTokenRegister

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
