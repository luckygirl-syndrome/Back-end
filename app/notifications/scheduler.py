import logging
import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.database import SessionLocal
from app.products.models import UserProduct

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


def _check_purchase_followup():
    """구매 1주일 후 만족도 알림"""
    from app.notifications.service import send_push_to_user

    db = SessionLocal()
    try:
        one_week_ago = datetime.datetime.now() - datetime.timedelta(days=7)
        start = one_week_ago.replace(hour=0, minute=0, second=0, microsecond=0)
        end = one_week_ago.replace(hour=23, minute=59, second=59, microsecond=999999)

        purchases = (
            db.query(UserProduct)
            .filter(
                UserProduct.is_purchased == True,
                UserProduct.completed_at >= start,
                UserProduct.completed_at <= end,
            )
            .all()
        )

        for purchase in purchases:
            send_push_to_user(
                db=db,
                user_id=purchase.user_id,
                title="또바바",
                body="일주일 전에 산 옷 마음에 드세요? 또바에게 알려주세요!",
                save_to_inbox=False,
            )
        logger.info(f"구매 후 만족도 알림 발송: {len(purchases)}건")
    except Exception as e:
        logger.error(f"구매 후 만족도 알림 실패: {e}")
    finally:
        db.close()


def _send_consideration_reminder():
    """월·금 21시 고민 중인 옷 리마인더"""
    from app.notifications.service import send_push_to_user
    from sqlalchemy import distinct

    db = SessionLocal()
    try:
        user_ids = (
            db.query(distinct(UserProduct.user_id))
            .filter(UserProduct.status == "PENDING")
            .all()
        )

        for (user_id,) in user_ids:
            send_push_to_user(
                db=db,
                user_id=user_id,
                title="또바바",
                body="요즘 새로 고민중인 옷 있어요? 또바가 해결해줄게요!",
                save_to_inbox=False,
            )
        logger.info(f"고민 옷 리마인더 발송: {len(user_ids)}건")
    except Exception as e:
        logger.error(f"고민 옷 리마인더 실패: {e}")
    finally:
        db.close()


def _send_chat_nudge():
    """채팅 시작 1시간 후 자동 마무리 예고 알림"""
    from app.notifications.service import send_push_to_user

    db = SessionLocal()
    try:
        one_hour_ago = datetime.datetime.now() - datetime.timedelta(hours=1)

        targets = (
            db.query(UserProduct)
            .filter(
                UserProduct.final_code.is_(None),
                UserProduct.requested_at <= one_hour_ago,
                UserProduct.nudge_sent_at.is_(None),
            )
            .all()
        )

        for up in targets:
            send_push_to_user(
                db=db,
                user_id=up.user_id,
                title="또바바",
                body="아직 고민 중이야? 채팅은 12시간 뒤에 자동으로 마무리되니 궁금한 게 있으면 지금 물어봐!",
                save_to_inbox=False,
                notification_type="chat",
                user_product_id=up.user_product_id,
            )
            up.nudge_sent_at = datetime.datetime.now()

        db.commit()
        logger.info(f"채팅 넛지 알림 발송: {len(targets)}건")
    except Exception as e:
        logger.error(f"채팅 넛지 알림 실패: {e}")
    finally:
        db.close()


def _auto_close_chats():
    """nudge 발송 후 12시간 경과 채팅 자동 종료"""
    db = SessionLocal()
    try:
        twelve_hours_ago = datetime.datetime.now() - datetime.timedelta(hours=12)

        targets = (
            db.query(UserProduct)
            .filter(
                UserProduct.final_code.is_(None),
                UserProduct.nudge_sent_at.isnot(None),
                UserProduct.nudge_sent_at <= twelve_hours_ago,
            )
            .all()
        )

        for up in targets:
            up.final_code = "NEUTRAL_EXPLORING"
            up.final_score = max(0, min(100, (up.impulse_score or 50) + (up.preference_score or 50) - 50))

        db.commit()
        logger.info(f"채팅 자동 종료: {len(targets)}건")
    except Exception as e:
        logger.error(f"채팅 자동 종료 실패: {e}")
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(_check_purchase_followup, CronTrigger(hour=21, minute=0))
    scheduler.add_job(
        _send_consideration_reminder,
        CronTrigger(day_of_week="mon,fri", hour=21, minute=0),
    )
    scheduler.add_job(_send_chat_nudge, CronTrigger(minute="*/10"))
    scheduler.add_job(_auto_close_chats, CronTrigger(minute="*/10"))
    scheduler.start()
    logger.info("알림 스케줄러 시작")
