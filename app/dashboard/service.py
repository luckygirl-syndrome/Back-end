from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from app.users.models import User
from app.products.models import UserProduct, Product
from app.dashboard import schemas

def get_home_dashboard(db: Session, user_id: int) -> schemas.HomeDashboardResponse:
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise ValueError("유저를 찾을 수 없습니다.")

    user_name = user.nickname

    # 절약한 금액 총합 계산
    # ✅ 기준 통일: "아쉽지만 포기한 옷" (status = ABANDONED) 의 상품 가격 합
    saved_items = db.query(Product.price).join(
        UserProduct, UserProduct.product_id == Product.product_id
    ).filter(
        UserProduct.user_id == user_id,
        UserProduct.is_purchased == 0,
        UserProduct.status == "ABANDONED"
    ).all()
    
    saved_amount = sum(item[0] for item in saved_items if item[0] is not None)

    # 지금까지 나눈 대화 = 채팅 목록과 동일 (상품별 1건, 분석 전 product_id=0 제외)
    total_chat_count = db.query(UserProduct.product_id).filter(
        UserProduct.user_id == user_id,
        UserProduct.product_id != 0
    ).distinct().count()

    # 지난 3달 동안 나눈 대화 = 최근 3개월 이내 활동한 상품 수
    three_months_ago = datetime.now() - timedelta(days=90)
    recent_chat_count = db.query(UserProduct.product_id).filter(
        UserProduct.user_id == user_id,
        UserProduct.product_id != 0,
        UserProduct.updated_at >= three_months_ago
    ).distinct().count()

    data = schemas.HomeDashboardData(
        user_name=user_name,
        saved_amount=saved_amount,
        recent_chat_count=recent_chat_count,
        total_chat_count=total_chat_count
    )

    return schemas.HomeDashboardResponse(
        status="success",
        data=data
    )

def get_unbought_receipts(db: Session, user_id: int) -> schemas.ReceiptListResponse:
    """안 산 영수증 목록 (is_purchased = 0)"""
    # 고민 끝에 안 사기로 한 상품들
    results = db.query(UserProduct, Product).join(
        Product, UserProduct.product_id == Product.product_id
    ).filter(
        UserProduct.user_id == user_id,
        UserProduct.is_purchased == 0,
        UserProduct.status == "ABANDONED"
    ).order_by(UserProduct.completed_at.desc()).all()

    items = []
    for up, prod in results:
        items.append(schemas.ReceiptListItem(
            user_product_id=up.user_product_id,
            product_id=prod.product_id,
            product_name=prod.product_name,
            product_img=prod.product_img,
            price=prod.price,
            discount_rate=prod.discount_rate
        ))

    return schemas.ReceiptListResponse(
        status="success",
        data=items
    )

def get_receipt_detail(db: Session, user_id: int, user_product_id: int) -> schemas.ReceiptDetailResponse:
    """안 산 영수증 상세 내용"""
    result = db.query(UserProduct, Product).join(
        Product, UserProduct.product_id == Product.product_id
    ).filter(
        UserProduct.user_id == user_id,
        UserProduct.user_product_id == user_product_id,
        UserProduct.is_purchased == 0,
        UserProduct.status == "ABANDONED"
    ).first()

    if not result:
        raise ValueError("해당 영수증을 찾을 수 없습니다.")

    up, prod = result

    # 고민한 기간 계산
    duration_days = None
    if up.requested_at and up.completed_at:
        delta = up.completed_at - up.requested_at
        duration_days = delta.days

    saved_amount = prod.price if prod.price else 0

    data = schemas.ReceiptDetailData(
        user_product_id=up.user_product_id,
        mall_name=prod.platform, # 플랫폼이 쇼핑몰
        brand=None, # 브랜드 컬럼이 현재 없다면 None, 또는 파싱 로직에서 추가 시 필요
        product_name=prod.product_name,
        product_img=prod.product_img,
        price=prod.price,
        discount_rate=prod.discount_rate,
        saved_amount=saved_amount,
        completed_at=up.completed_at,
        duration_days=duration_days
    )

    return schemas.ReceiptDetailResponse(
        status="success",
        data=data
    )

def get_stats(db: Session, user_id: int) -> schemas.StatsResponse:
    """홈 통계: 최근 또바바 점수 4개 + 구매 전환율 + 소비 만족도"""
    # 최근 또바바 점수 4개 (final_score 있는 것만, 최신순)
    recent_rows = (
        db.query(UserProduct, Product)
        .join(Product, UserProduct.product_id == Product.product_id)
        .filter(
            UserProduct.user_id == user_id,
            UserProduct.product_id != 0,
            UserProduct.final_score.isnot(None),
        )
        .order_by(UserProduct.updated_at.desc())
        .limit(4)
        .all()
    )
    recent_scores = [
        schemas.RecentScoreItem(product_name=prod.product_name, score=up.final_score)
        for up, prod in recent_rows
    ]

    # 구매 전환율: 전체 user_product 수 vs 구매 확정
    total_count = db.query(UserProduct).filter(
        UserProduct.user_id == user_id,
        UserProduct.product_id != 0,
    ).count()

    purchased_count = db.query(UserProduct).filter(
        UserProduct.user_id == user_id,
        UserProduct.product_id != 0,
        UserProduct.status == "PURCHASED",
    ).count()

    # 소비 만족도: 전체 구매 수 vs "만족" 포함 리뷰
    feedback_total = db.query(UserProduct).filter(
        UserProduct.user_id == user_id,
        UserProduct.status == "PURCHASED",
    ).count()

    satisfied_count = db.query(UserProduct).filter(
        UserProduct.user_id == user_id,
        UserProduct.status == "PURCHASED",
        UserProduct.review.ilike("%만족%"),
    ).count()

    return schemas.StatsResponse(
        status="success",
        data=schemas.StatsData(
            recent_scores=recent_scores,
            purchase_conversion=schemas.ConversionStats(
                total=total_count,
                purchased=purchased_count,
            ),
            satisfaction=schemas.SatisfactionStats(
                total=feedback_total,
                satisfied=satisfied_count,
            ),
        ),
    )


def get_considering_items(db: Session, user_id: int) -> schemas.ConsideringListResponse:
    """결정했나요? 목록 = 채팅에서 고민 중인 상품만, 상품별 최신 1건 (채팅 목록과 동일 방식)"""
    subquery = (
        db.query(
            UserProduct.product_id,
            func.max(UserProduct.updated_at).label("max_updated_at")
        )
        .filter(UserProduct.user_id == user_id)
        .filter(UserProduct.product_id != 0)
        .filter(UserProduct.is_purchased == 0)
        .filter(
            (UserProduct.status == "PENDING") | (UserProduct.status == "FINISHED") | (UserProduct.status == "ANALYZING")
        )
        .group_by(UserProduct.product_id)
        .subquery()
    )
    results = (
        db.query(UserProduct, Product)
        .join(Product, UserProduct.product_id == Product.product_id)
        .join(
            subquery,
            (UserProduct.product_id == subquery.c.product_id)
            & (UserProduct.updated_at == subquery.c.max_updated_at)
        )
        .filter(UserProduct.user_id == user_id)
        .filter(UserProduct.is_purchased == 0)
        .filter(
            (UserProduct.status == "PENDING") | (UserProduct.status == "FINISHED") | (UserProduct.status == "ANALYZING")
        )
        .order_by(UserProduct.updated_at.desc(), UserProduct.user_product_id.desc())
        .all()
    )

    items = []
    now = datetime.now()
    for up, prod in results:
        duration_days = None
        if up.requested_at:
            duration_days = (now - up.requested_at).days

        items.append(schemas.ConsideringListItem(
            user_product_id=up.user_product_id,
            product_id=prod.product_id,
            product_img=prod.product_img,
            product_name=prod.product_name,
            price=prod.price,
            duration_days=duration_days
        ))

    return schemas.ConsideringListResponse(
        status="success",
        data=items
    )
