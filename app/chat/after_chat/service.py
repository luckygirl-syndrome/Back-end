from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.products.models import UserProduct
from app.users.models import User
from app.chat.after_chat import schemas
from app.products.models import Product

def update_purchase_status(db: Session, user_id: int, req: schemas.PurchaseStatusRequest) -> schemas.PurchaseStatusResponse:
    """사용자가 실제로 구매했는지 여부 기록하기"""
    up = db.query(UserProduct).filter(
        UserProduct.user_id == user_id,
        UserProduct.user_product_id == req.user_product_id
    ).first()

    if not up:
        raise ValueError("해당 채팅방(상품)을 찾을 수 없습니다.")

    # 2. 의사결정에 따른 상태값 분기
    if req.is_purchased:
        # [구매 확정]
        up.is_purchased = 1
        up.status = "PURCHASED"  # UserProduct 내의 상태 필드 업데이트
        up.completed_at = datetime.now()  # 결정이 난 시점 (구매 완료)
        msg = "성공적으로 구매 확정되었습니다."
        
    elif req.is_abandoned:
        # [구매 포기]
        up.is_purchased = 0
        up.status = "ABANDONED"  # "안 사기로 함" 상태 기록
        up.completed_at = datetime.now()  # 고민을 끝낸 날 (구매 포기)
        msg = "구매 포기 처리가 완료되었습니다."
        
    else:
        # 결정되지 않은 상태로 호출된 경우
        return schemas.PurchaseStatusResponse(
            status="success", 
            message="변경된 결정 사항이 없습니다."
        )

    # 3. DB 반영
    db.commit()
    db.refresh(up) # 변경된 값 확정

    return schemas.PurchaseStatusResponse(
        status="success",
        message=msg
    )

def submit_feedback(db: Session, user_id: int, req: schemas.FeedbackSubmitRequest) -> schemas.FeedbackSubmitResponse:
    """2주 후 피드백 받아서 저장하기"""
    up = db.query(UserProduct).filter(
        UserProduct.user_id == user_id,
        UserProduct.user_product_id == req.user_product_id
    ).first()

    if not up:
        raise ValueError("해당 상품을 찾을 수 없습니다.")

    # 실제 피드백 데이터를 DB에 저장
    if req.feedback_text is not None:
        up.feedback_text = req.feedback_text
    if req.rating is not None:
        up.feedback_rating = req.rating
        

    db.commit()

    return schemas.FeedbackSubmitResponse(
        status="success",
        message="피드백이 성공적으로 저장되었습니다."
    )


# -------------------------------------------------------------------
# [Scheduler] 하루 한 번 자정 12시 실행 (프레임워크 종속적 로직)
# -------------------------------------------------------------------
# FastAPI 환경에서 매일 자정에 실행하려면 보통 `APScheduler`를 사용합니다.
# 
# 1. 설치: pip install apscheduler
# 2. main.py 혹은 lifespan에 스케줄러 등록
#
# async def daily_midnight_task():
#     # 1) Session 열기
#     db = next(get_db())
#     
#     # 2) "상담을 마친지 2주" 된 유저 찾기 (예시)
#     two_weeks_ago = datetime.now() - timedelta(days=14)
#     target_users = db.query(UserProduct).filter(
#         UserProduct.completed_at <= two_weeks_ago,
#         UserProduct.is_purchased == None # 등등의 조건
#     ).all()
#     
#     # 3) 프론트엔드로 전달 (FCM 푸시, MQ 발송 등)
#     for user in target_users:
#         send_push_notification(user.user_id, "2주 전에 고민했던 상품, 어떻게 하셨나요?")
# 
# # 4) [APScheduler 설정 예시]
# # from apscheduler.schedulers.asyncio import AsyncIOScheduler
# # scheduler = AsyncIOScheduler()
# # scheduler.add_job(daily_midnight_task, 'cron', hour=0, minute=0)
# # scheduler.start()
