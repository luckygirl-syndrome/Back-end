"""Match Score 계산 — docs/Impulse Score & Match Score.md 기반.

style_similarity는 LLM 호출이 필요하므로 여기선 외부에서 주입받는다.
"""


def compute_match_score(
    style_match_score: int,      # 0~35 (ai_prompt.py 출력)
    price_reasonable: int,       # 3, 10, 18, 25
    interest_persistence: int,   # 4, 10, 16, 20
    discovery_stability: int,    # 6, 10, 12, 20
) -> int:
    """Match Score (0~100) 계산. 단순 합산."""
    return style_match_score + price_reasonable + interest_persistence + discovery_stability


def compute_match_score_without_style(
    price_reasonable: int,
    interest_persistence: int,
    discovery_stability: int,
) -> int:
    """style_similarity 제외 3피쳐 소계 (0~65)."""
    return price_reasonable + interest_persistence + discovery_stability
