import numpy as np

# 가중치 및 설정값들 (전역 변수 혹은 클래스 속성)
WEIGHT_MATRIX = {
    # [수치형 지표]
    'discount_rate':     {'D': 1.2, 'N': 1.1, 'S': 1.0, 'A': 1.0, 'T': 1.0, 'M': 1.0},
    'review_count':      {'D': 1.0, 'N': 1.2, 'S': 1.2, 'A': 0.9, 'T': 1.5, 'M': 0.6}, # M형 특수로직 적용 대상
    'review_score':      {'D': 1.0, 'N': 1.2, 'S': 1.2, 'A': 1.0, 'T': 1.3, 'M': 1.0},
    'product_like':      {'D': 1.0, 'N': 1.0, 'S': 1.1, 'A': 1.0, 'T': 1.1, 'M': 0.8},
    'is_direct_shipping': {'D': 1.0, 'N': 1.1, 'S': 1.0, 'A': 1.0, 'T': 1.0, 'M': 1.0},
    'free_shipping':     {'D': 1.0, 'N': 1.1, 'S': 1.0, 'A': 1.0, 'T': 1.0, 'M': 1.0},
    
    # [텍스트형 지표]
    'sim_trend_hype':    {'D': 1.0, 'N': 1.0, 'S': 1.5, 'A': 1.0, 'T': 1.5, 'M': 0.8},
    'sim_temptation':    {'D': 1.0, 'N': 1.0, 'S': 1.2, 'A': 1.0, 'T': 1.0, 'M': 1.0},
    'sim_fit_anxiety':   {'D': 1.0, 'N': 1.0, 'S': 1.0, 'A': 1.0, 'T': 1.0, 'M': 1.0},
    'sim_quality_logic': {'D': 1.0, 'N': 1.2, 'S': 1.0, 'A': 1.0, 'T': 1.0, 'M': 1.0},
    'sim_bundle':        {'D': 1.0, 'N': 1.0, 'S': 1.0, 'A': 1.0, 'T': 1.0, 'M': 1.0},
    'sim_confidence':    {'D': 1.0, 'N': 1.2, 'S': 1.2, 'A': 1.0, 'T': 1.2, 'M': 1.0},
}

RISK_LEVELS = [
    (0,  46,  "안심 (Safe)", 1),
    (47, 75,  "주의 (Caution)", 2),
    (76, 100, "위험 (Danger)", 3),
]

# 챗봇 설명용 한글 매핑
FEATURE_KO = {
    'discount_rate': '할인율', 'review_count': '리뷰 수', 'review_score': '평점',
    'product_like': '찜 수', 'is_direct_shipping': '배송 정보', 'free_shipping': '무료 배송',
    'sim_trend_hype': '유행/대란 키워드', 'sim_temptation': '자극적 홍보 문구',
    'sim_fit_anxiety': '핏/체형 보정 문구', 'sim_quality_logic': '소재/퀄리티 강조',
    'sim_bundle': '1+1/묶음 할인', 'sim_confidence': 'MD추천/보증'
}
def soft_step(x, threshold, scale, max_val):
    return max_val / (1 + np.exp(-scale * (x - threshold)))

def gaussian_peak(x, peak, width, max_val):
    return max_val * np.exp(-((x - peak)**2) / (2 * width**2))

def parse_persona(persona_code):
    traits = {'D':0, 'N':0, 'S':0, 'A':0, 'T':0, 'M':0}
    if not persona_code: 
        return traits
    
    # 1. 하이픈 제거
    clean_code = str(persona_code).replace('-', '')
    
    # 2. 한 글자씩 검사 (예: "SDM" -> 'S', 'D', 'M')
    for char in clean_code:
        upper_char = char.upper()
        if upper_char in traits:
            traits[upper_char] = 1
    return traits

def analyze_product_risk(product_json: dict, persona_code: str):
    """
    Args:
        product_json (dict): 크롤링된 상품 정보 (JSON)
        persona_code (str): 유저의 S-BTI 코드 (예: "D-S-T")
    Returns:
        dict: 챗봇에게 전달할 최종 분석 결과
    """
    user_traits = parse_persona(persona_code)
    
    # ---------------------------------------------------------
    # Step 1. Raw Feature Score 계산 (0 ~ Max Score)
    # ---------------------------------------------------------
    raw_scores = {}
    dr = product_json.get('discount_rate', 0)
    
    # [할인율]: 40% 피크, 너비 15 (80% 이상은 의심으로 점수 하락)
    raw_scores['discount_rate'] = gaussian_peak(dr, 40, 15, 15)
    
    # [리뷰/찜]: Soft Step 적용
    raw_scores['review_score'] = soft_step(product_json.get('review_score', 0), 4.3, 10, 8)
    raw_scores['product_like'] = soft_step(product_json.get('product_likes', 0), 1800, 0.002, 4)
    
    # [리뷰 수]: M형 특수 로직 적용
    rc_raw = product_json.get('review_count', 0)
    base_rc_score = soft_step(rc_raw, 1000, 0.003, 6)
    
    if user_traits.get('M', 0) == 1:
        # M형: 5개 이하는 데이터 부족(0점), 그 이상은 적을수록 위험(반전)
        if rc_raw <= 5:
            raw_scores['review_count'] = 0
        else:
            raw_scores['review_count'] = 6 - base_rc_score
    else:
        # 일반: 많을수록 동조 심리 자극(위험)
        raw_scores['review_count'] = base_rc_score

    # [바이너리 피처]
    raw_scores['is_direct_shipping'] = 7 if product_json.get('is_direct_shipping', 0) else 0
    raw_scores['free_shipping'] = 7 if product_json.get('free_shipping', 0) else 0
    
    text_features = ['sim_temptation', 'sim_fit_anxiety', 'sim_trend_hype',
                     'sim_quality_logic', 'sim_bundle', 'sim_confidence']
    for col in text_features:
        raw_scores[col] = 6 if product_json.get(col, 0) else 0

    # ---------------------------------------------------------
    # Step 2. Weighted Score 계산 (가중치 적용)
    # ---------------------------------------------------------
    weighted_breakdown = {}
    stimulus_total = 0
    
    for feat, score in raw_scores.items():
        if score <= 0.01: continue
        
        # 유저 성향에 맞는 가중치 평균 계산
        weights = [WEIGHT_MATRIX[feat][t] for t, lvl in user_traits.items() 
                   if lvl > 0 and t in WEIGHT_MATRIX[feat]]
        w_final = sum(weights) / len(weights) if weights else 1.0
        
        weighted_val = score * w_final
        stimulus_total += weighted_val
        weighted_breakdown[feat] = weighted_val

    # ---------------------------------------------------------
    # Step 3. 최종 점수 산출 (Amplifier + Damping)
    # ---------------------------------------------------------
    # Amplifier 설정 (D=1.2배 / N=0.9배)
    amp_D = 1.2 if user_traits.get('D', 0) == 1 else 1.0
    amp_N = 0.9 if user_traits.get('N', 0) == 1 else 1.0
    total_multiplier = amp_D * amp_N
    
    # Base 25점 + 가중 합산
    raw_total_score = 25 + (stimulus_total * total_multiplier)
    
    # 비선형 댐핑 (90점 초과 시 압축)
    if raw_total_score > 90:
        final_score = 90 + (10 * ((raw_total_score - 90) / (raw_total_score - 90 + 5)))
    else:
        final_score = raw_total_score
        
    final_score = max(0, min(final_score, 100)) # 0~100 클리핑

    # ---------------------------------------------------------
    # Step 4. 결과 구조화 (Risk Stage & Top 3 Factors)
    # ---------------------------------------------------------
    # 위험 단계 판정
    risk_stage_info = next((lvl for lvl in RISK_LEVELS if lvl[0] <= final_score <= lvl[1]), RISK_LEVELS[-1])
    
    # 기여도 기준 Top 3 원인 추출
    sorted_factors = sorted(weighted_breakdown.items(), key=lambda x: x[1], reverse=True)[:2]
    top_causes = []
    for feat, val in sorted_factors:
        desc = ""
        # 챗봇이 말하기 좋게 구체적 수치 포함
        if feat == 'discount_rate': desc = f"{dr}%"
        elif feat == 'review_count': desc = f"{rc_raw}개"
        elif feat == 'review_score': desc = f"{product_json.get('review_score',0)}점"
        
        top_causes.append({
            "feature_key": feat,
            "feature_name": FEATURE_KO.get(feat, feat),
            "score_contribution": round(val, 2),
            "detail": desc
        })

    # analyze_product_risk 함수 맨 마지막에 추가
    return {
        "total_score": round(final_score, 1),
        "risk_label": risk_stage_info[2],
        "risk_level": risk_stage_info[3],
        "top_2_causes": top_causes,
        "breakdown": weighted_breakdown
    }
