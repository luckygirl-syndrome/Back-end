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
        logger.info(f"FCM 발송 성공 (token={token[:20]}...)")
        return True
    except Exception as e:
        logger.error(f"FCM 발송 실패 (token={token[:20]}...): {e}")
        return False


def send_push_to_user(db: Session, user_id: int, title: str, body: str, save_to_inbox: bool = False, notification_type: str = "chat", user_product_id: int = None):
    tokens = db.query(FcmToken).filter(FcmToken.user_id == user_id).all()
    logger.info(f"FCM 발송 시도 (user={user_id}, 토큰 수={len(tokens)})")
    for fcm_token in tokens:
        _send_to_token(fcm_token.token, title, body)

    if save_to_inbox:
        notification = Notification(user_id=user_id, title=title, body=body, notification_type=notification_type, user_product_id=user_product_id)
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


def get_notifications(db: Session, user_id: int) -> list[dict]:
    from app.products.models import UserProduct, Product
    rows = (
        db.query(Notification, Product.product_name)
        .outerjoin(UserProduct, Notification.user_product_id == UserProduct.user_product_id)
        .outerjoin(Product, UserProduct.product_id == Product.product_id)
        .filter(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    result = []
    for notif, product_name in rows:
        result.append({
            "id": notif.id,
            "title": notif.title,
            "body": notif.body,
            "notification_type": notif.notification_type,
            "user_product_id": notif.user_product_id,
            "product_name": product_name,
            "is_read": notif.is_read,
            "created_at": notif.created_at,
        })
    return result


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
