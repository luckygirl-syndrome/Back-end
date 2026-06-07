"""공통 질문 응답별 Match Score 피쳐 점수 매핑."""

from typing import List, Dict

# --- price_reasonable (25점 만점) ---
PRICE_REASONABLE = {
    "저렴한 것 같아요": 25,
    "이 정도면 괜찮아요": 18,
    "좀 비싸긴 한데 못 살 정도는 아니에요": 10,
    "상품은 마음에 들지만 가격이 비싸요": 3,
}

# --- interest_persistence (20점 만점) ---
INTEREST_PERSISTENCE = {
    "오늘 처음 봤어요": 4,
    "2~3일 됐어요": 10,
    "1주일 정도 됐어요": 20,
    "2주 이상 고민했어요": 16,
}

# --- discovery_stability (20점 만점) ---
DISCOVERY_STABILITY = {
    "쇼핑 앱에서 카테고리 검색 후 찾아보다 발견했어요": 20,
    "유튜버/인플루언서가 입은 것을 봤어요": 6,
    "쇼핑 앱에서 랭킹이나 유저 추천을 둘러보다 발견했어요": 10,
    "인스타/틱톡/X 같은 SNS 보다가 발견했어요": 6,
    "브랜드 계정에 신상이 추가된 걸 봤어요": 12,
}


def get_all_survey_combinations() -> List[Dict]:
    """price × interest × discovery 모든 유효 조합을 생성.

    Returns: list of dict with keys:
        price_label, interest_label, discovery_label,
        price_reasonable, interest_persistence, discovery_stability,
        subtotal (65점 만점)
    """
    combos = []
    for p_label, p_score in PRICE_REASONABLE.items():
        for i_label, i_score in INTEREST_PERSISTENCE.items():
            for d_label, d_score in DISCOVERY_STABILITY.items():
                combos.append({
                    "price_label": p_label,
                    "interest_label": i_label,
                    "discovery_label": d_label,
                    "price_reasonable": p_score,
                    "interest_persistence": i_score,
                    "discovery_stability": d_score,
                    "subtotal": p_score + i_score + d_score,
                })
    return combos
