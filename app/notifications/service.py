import logging
import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy.orm import Session

from app.core.config import settings
from .models import FcmToken, Notification

logger = logging.getLogger(__name__)

_firebase_initialized = False


def _init_firebase():
    global _firebase_initialized
    if not _firebase_initialized:
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)
        _firebase_initialized = True


def _send_to_token(token: str, title: str, body: str) -> bool:
    try:
        _init_firebase()
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            token=token,
        )
        messaging.send(message)
        return True
    except firebase_admin.exceptions.FirebaseError as e:
        logger.error(f"FCM 발송 실패 (token={token[:20]}...): {e}")
        return False


def send_push_to_user(db: Session, user_id: int, title: str, body: str, save_to_inbox: bool = False, notification_type: str = "chat"):
    tokens = db.query(FcmToken).filter(FcmToken.user_id == user_id).all()
    for fcm_token in tokens:
        _send_to_token(fcm_token.token, title, body)

    if save_to_inbox:
        notification = Notification(user_id=user_id, title=title, body=body, notification_type=notification_type)
        db.add(notification)
        db.commit()


def broadcast_announcement(db: Session, title: str, body: str):
    user_ids = [row[0] for row in db.query(FcmToken.user_id).distinct().all()]
    for user_id in user_ids:
        send_push_to_user(db, user_id, title, body, save_to_inbox=True, notification_type="announcement")


def register_token(db: Session, user_id: int, token: str, device_type: str = "ios"):
    existing = db.query(FcmToken).filter(FcmToken.token == token).first()
    if existing:
        existing.user_id = user_id
        existing.device_type = device_type
    else:
        db.add(FcmToken(user_id=user_id, token=token, device_type=device_type))
    db.commit()


def delete_token(db: Session, user_id: int, token: str):
    db.query(FcmToken).filter(
        FcmToken.user_id == user_id,
        FcmToken.token == token,
    ).delete()
    db.commit()


def get_notifications(db: Session, user_id: int) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )


def mark_read(db: Session, user_id: int, notification_id: int):
    db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user_id,
    ).update({"is_read": True})
    db.commit()


def mark_all_read(db: Session, user_id: int):
    db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.is_read == False,
    ).update({"is_read": True})
    db.commit()
