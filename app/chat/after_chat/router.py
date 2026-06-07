from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.users import models
from app.users.router import get_current_user

from app.chat.after_chat import schemas
from app.chat.after_chat import service
from app.core.observability import posthog_client

router = APIRouter(prefix="/api/chat/after", tags=["After Chat"])

_200 = lambda result: {"200": {"content": {"application/json": {"example": {"isSuccess": True, "code": "200", "message": "OK", "result": result}}}}}


@router.post(
    "/purchase",
    summary="구매 여부 업데이트",
    description="채팅 종료 후 실제 구매 또는 포기 여부를 업데이트합니다.",
    responses=_200({"status": "success", "message": "성공적으로 구매 확정되었습니다."}),
)
def update_purchase(
    request: schemas.PurchaseStatusRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        user_id = current_user.user_id
        result = service.update_purchase_status(db, user_id, request)
        if posthog_client:
            decision = "purchased" if request.is_purchased else ("abandoned" if request.is_abandoned else "undecided")
            posthog_client.capture(
                distinct_id=str(user_id),
                event="purchase_decision_made",
                properties={
                    "user_product_id": request.user_product_id,
                    "decision": decision,
                },
            )
        return success({"status": result.status, "message": result.message})
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        print("purchase status error:", e)
        raise HTTPException(status_code=500, detail="구매 여부 업데이트에 실패했습니다.")


@router.post(
    "/feedback",
    summary="2주 후 피드백 제출",
    description="구매 2주 후 만족도 피드백을 저장합니다.",
    responses=_200({"status": "success", "message": "피드백이 성공적으로 저장되었습니다."}),
)
def submit_feedback(
    request: schemas.FeedbackSubmitRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        user_id = current_user.user_id
        result = service.submit_feedback(db, user_id, request)
        if posthog_client:
            posthog_client.capture(
                distinct_id=str(user_id),
                event="feedback_submitted",
                properties={
                    "user_product_id": request.user_product_id,
                    "rating": request.rating,
                    "has_text": bool(request.feedback_text),
                },
            )
        return success({"status": result.status, "message": result.message})
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        print("feedback error:", e)
        raise HTTPException(status_code=500, detail="피드백 저장에 실패했습니다.")
