"""Impulse Score 계산 — docs/Impulse Score & Match Score.md 기반."""

from math import log10


# ==============================================
# M형 플랫폼별 임계값 테이블
# ==============================================

REVIEW_M_TABLE = {
    "무신사":     (100,   650,   0.0,  -0.4,  -0.9),
    "에이블리":   (800,   3000,  0.0,  -0.4,  -0.9),
    "지그재그":   (600,   2500,  0.0,  -0.4,  -0.9),
    "default":    (100,   650,   0.0,  -0.4,  -0.9),
}

DISCOUNT_TABLE = [
    (0,   0,   0.00),
    (1,   10,  0.15),
    (11,  20,  0.35),
    (21,  35,  0.65),
    (36,  50,  1.00),
    (51,  60,  0.75),
    (61,  70,  0.45),
    (71,  100, 0.25),
]

MAX_POSSIBLE = 1.3688  # DUTE 기준 이론적 최댓값 (0.35*1.56 + 0.25*1.2 + 0.18*1.56 + 0.22*1.1)


def _get_m_score(count, table, platform):
    row = table.get(platform, table["default"])
    n, n2, score_low, score_mid, score_high = row
    if n is None:
        return 0.0
    if count <= n:
        return score_low
    elif count <= n2:
        return score_mid
    else:
        return score_high


def _get_discount_score(discount_rate):
    if discount_rate is None:
        return 0.00
    for lo, hi, score in DISCOUNT_TABLE:
        if lo <= discount_rate <= hi:
            return score
    return 0.00


def compute_impulse_score(
    # 상품 데이터
    discount_rate,
    review_count,
    rating,
    personalized_score,  # ai_prompt.py 마케팅 보정 점수 (0~100)
    # 유저 SBTI 플래그
    is_D, is_N,
    is_U, is_I,
    is_T, is_M,
    is_E, is_O,
    # 플랫폼
    platform="default",
) -> int:
    """Impulse Score (0~100) 계산. personalized_score 기반."""

    # Step 1: 피쳐 정규화
    discount_score = _get_discount_score(discount_rate)

    if is_M:
        review_score = _get_m_score(review_count, REVIEW_M_TABLE, platform)
    else:
        review_score = min(log10(review_count + 1) / log10(5001), 1.0) ** 1.5

    if review_count == 0 or rating is None:
        rating_score = 0.3
    else:
        rating_score = max((rating - 3.0) / 2.0, 0.0)

    # title_marketing_score: personalized_score(0~100) → 0~1 정규화
    title_marketing_score = max(0.0, min(1.0, personalized_score / 100.0))

    # Step 2: SBTI Multiplier
    discount_m = 1.2 * (1.3 if is_E else (0.9 if is_O else 1.0))
    rating_m = (1.2 if is_U else 1.0) * (1.2 if is_O else 1.0)
    review_count_m = (1.3 if is_U else (0.7 if is_I else 1.0)) * (1.2 if is_T else 1.0)

    if is_M:
        title_marketing_m = 0.8
    elif is_D:
        title_marketing_m = 1.1
    elif is_N:
        title_marketing_m = 0.9
    else:
        title_marketing_m = 1.0

    # Step 3: 가중합
    raw_score = (
        0.35 * discount_score         * discount_m
      + 0.25 * rating_score           * rating_m
      + 0.18 * review_score           * review_count_m
      + 0.22 * title_marketing_score  * title_marketing_m
    )

    # Step 4: 0~100 변환 (기본 점수 가산)
    base = 10 + (5 if is_D else 0)
    return max(0, min(100, round((raw_score / MAX_POSSIBLE) * 100) + base))
