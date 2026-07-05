import logging
import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.database import SessionLocal
from app.products.models import UserProduct

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


def _check_purchase_followup():
    """구매 4일 후 만족도 알림"""
    from app.notifications.service import send_push_to_user

    db = SessionLocal()
    try:
        four_days_ago = datetime.datetime.now() - datetime.timedelta(days=4)
        start = four_days_ago.replace(hour=0, minute=0, second=0, microsecond=0)
        end = four_days_ago.replace(hour=23, minute=59, second=59, microsecond=999999)

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
                body="요즘 그 옷 마음에 드세요? 잘 활용하고 계신가요? 😊",
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
                body="혹시 요즘 고민 중인 옷 있지 않아요? 같이 생각해봐요 🛍️",
                save_to_inbox=False,
            )
        logger.info(f"고민 옷 리마인더 발송: {len(user_ids)}건")
    except Exception as e:
        logger.error(f"고민 옷 리마인더 실패: {e}")
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(_check_purchase_followup, CronTrigger(hour=21, minute=0))
    scheduler.add_job(
        _send_consideration_reminder,
        CronTrigger(day_of_week="mon,fri", hour=21, minute=0),
    )
    scheduler.start()
    logger.info("알림 스케줄러 시작")
