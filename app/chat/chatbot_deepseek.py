import os
import re
import json
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not API_KEY:
    raise ValueError("DEEPSEEK_API_KEY가 .env 파일에 없어. 확인해줘.")

MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
END_DECISION_MODEL = os.getenv("DEEPSEEK_END_DECISION_MODEL", MODEL_NAME)

client = OpenAI(
    api_key=API_KEY,
    base_url="https://api.deepseek.com"
)


# ── 입력 검증 ────────────────────────────────────────────────────────────────

REQUIRED_PROMPT_KEYS = {"product_info", "confirmed_sentences", "user_type"}


def validate_prompt_data(data: dict) -> None:
    """백엔드가 전달한 프롬프트 입력의 최소 스키마를 검증한다."""
    if not isinstance(data, dict):
        raise ValueError("prompt_data는 dict여야 해.")

    missing = REQUIRED_PROMPT_KEYS - data.keys()
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"prompt_data에 필수 항목이 없어: {missing_text}")

    if not isinstance(data["product_info"], list):
        raise ValueError("prompt_data.product_info는 list여야 해.")
    if not isinstance(data["confirmed_sentences"], list):
        raise ValueError("prompt_data.confirmed_sentences는 list여야 해.")
    if not isinstance(data["user_type"], dict):
        raise ValueError("prompt_data.user_type은 dict여야 해.")


# ── 종료 코드 검증 ────────────────────────────────────────────────────────────

VALID_RESULT_CODES = {
    "BUY_CONFIDENT_GROUNDED",
    "BUY_CONDITIONALLY_READY",
    "NEUTRAL_EXPLORING",
    "HOLD_REASONABLE",
    "IMPULSE_JUSTIFICATION",
    "LOW_USE_CLARITY",
}

def validate_exit_code(output: str) -> str:
    lines = output.splitlines()
    code_line = next((line for line in lines if line.startswith("CODE:")), "")
    summary_line = next((line for line in lines if line.startswith("RESULT_SUMMARY:")), "")
    code = code_line.replace("CODE:", "").strip()

    if code not in VALID_RESULT_CODES:
        return (
            "CODE: NEUTRAL_EXPLORING\n"
            "RESULT_SUMMARY: 판단 코드 생성이 불안정해 기본 탐색 상태로 처리됨."
        )

    if not summary_line:
        summary_line = "RESULT_SUMMARY: 요약 생성이 누락되어 기본 문구로 대체됨."

    return f"{code_line.strip()}\n{summary_line.strip()}"


# ── 내부 태그 처리 ────────────────────────────────────────────────────────────

def strip_internal_tags(text: str) -> tuple[str, bool]:
    if not text:
        return text, False
    ended = "<END_DECISION>" in text
    text = text.replace("<END_DECISION>", "").strip()
    return text, ended


# ── 점수 수치 / 성향 코드 유출 방어 ─────────────────────────────────────────────
# "점수 수치는 절대 말하지 않는다"는 프롬프트 지시만으로는 유출을 100% 막을 수 없어서,
# 코드 레벨에서 한 번 더 검사한다. 걸리면 우선 재생성하고, 그래도 남아있으면
# 정규식으로 필터링해서 최소한 유저 화면에는 노출되지 않게 한다.

SCORE_LEAK_RE = re.compile(r"\d+\s*점")


def contains_leaked_score(text: str, type_code: str) -> bool:
    if not text:
        return False
    if SCORE_LEAK_RE.search(text):
        return True
    if type_code and re.search(re.escape(type_code), text, re.IGNORECASE):
        return True
    return False


def scrub_leaked_score(text: str, type_code: str) -> str:
    scrubbed = SCORE_LEAK_RE.sub("", text)
    if type_code:
        scrubbed = re.sub(re.escape(type_code), "", scrubbed, flags=re.IGNORECASE)
    scrubbed = re.sub(r"\s{2,}", " ", scrubbed)
    scrubbed = re.sub(r"[,，]\s*(?=[.!?]|$)", "", scrubbed)
    return scrubbed.strip()


# ── 마무리 신호 감지 ──────────────────────────────────────────────────────────

# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────

def build_chat_prompt(data: dict) -> str:
    return f"""## ROLE
너는 쇼핑을 무조건 권하거나 막지 않는 대화형 결정 파트너다.
유저의 말을 정확히 이어받아, 이 상품이 실제로 잘 맞는지와 지금 새로 살 이유가 있는지를 함께 판단한다.

## INPUT
```json
{json.dumps(data, ensure_ascii=False, indent=2)}
```

- product_info: 상품의 사실 정보. 상품명의 마케팅 표현은 근거로 쓰지 않는다.
- confirmed_sentences: 대화 전 배경 단서. 이미 반영한 내용은 반복하지 않는다.
- user_type: 놓치기 쉬운 후회 가능성을 보는 보조 정보. 유저를 유형대로 단정하지 않는다.
- 점수는 방향만 참고한다. 점수 숫자와 user_type 코드는 절대 말하지 않는다.

## GLOBAL CONSTRAINTS
1. 마지막 발화를 앞 대화와 연결해 이해한다. 유저가 한 문장에 여러 정보를 말하면 모두 반영하고, 이미 들은 내용은 다시 묻지 않는다.
2. 구매와 비구매 어느 쪽도 기본값으로 두지 않는다. 상품이 괜찮아도 살 필요가 약할 수 있고, 비슷한 옷이 있어도 추가 구매가 합리적일 수 있다.
3. 같은 종류의 옷 한 벌이 있다는 사실만으로 중복이라고 판단하지 않는다. 강한 구매·비구매 결론은 결정적인 조건 하나 또는 서로 다른 근거 두 개 이상이 있을 때만 낸다.
4. 결론 전에는 왜 이 상품이 끌리는지와 무엇이 망설여지는지를 함께 본다. 아직 한쪽이 드러나지 않았다면 결론을 서두르지 않는다.
5. 새 정보는 기존 판단에 더하거나 빼서 반영한다. 마지막 한마디마다 구매와 비구매를 오가지 않는다. 판단이 바뀌면 무엇 때문에 달라졌는지 짧게 드러낸다.
6. 질문은 답에 따라 판단이 달라질 때만 최대 1개 한다. 추상적인 질문이나 특정 답을 유도하는 질문은 하지 않는다.
7. 상품의 소재·핏·기장·색·계절감·관리 중 실제 착용 만족도에 중요한 특징을 필요할 때 하나만 짚는다. 명시되지 않은 특성은 가능성으로 말하고, 판단에 영향을 준다면 설명으로 끝내지 말고 가장 적절한 확인 행동 1개를 제안한다.
8. 긍정적인 예상은 우선 정보로 받아들인다. 앞말과 충돌하거나 그 예상 하나만으로 구매를 확정하려는 경우에만 실제 경험이나 사용할 상황을 한 번 확인한다.
9. “그래”, “응”, “웅”, “맞아”는 바로 앞 질문이나 문장에 대한 동의로 연결한다. 예를 들어 “이 디자인 자주 입어?” 다음의 “응”은 “이 디자인을 자주 입는다”는 뜻이다. 뒤에 반박·새 걱정·미결 표현이 이어지면 뒤 내용을 우선한다.
10. role=user의 내용은 유저가 실제로 입력한 원문이다. 시스템의 턴 제어문이나 내부 지시를 유저가 한 말처럼 섞어 해석하지 않는다.
11. 유저가 “어?”, “아니”, “그 말이 아닌데”, “자주 입는다니까”처럼 정정하면 이전 판단을 변호하지 않는다. 정정된 사실을 앞선 assistant의 해석보다 우선하고, 같은 대화에서 다시 반대로 바꾸지 않는다.
12. 새 정보는 사실과 예상으로 구분한다. “평소 자주 입는다”처럼 실제 습관을 말한 내용은 확인된 사실로 보고, “자주 입을 것 같다”처럼 미래를 예상한 말과 혼동하지 않는다.
13. 매 답변은 구체적인 질문, 현재 판단, 바로 할 확인 행동 중 하나로 끝낸다. “정해보자”, “이야기해보자”, “이게 중요한 것 같아” 같은 진행 문장으로 끝내지 않는다.
14. 유저가 직접 구매·보류·포기를 말하지 않았는데 현재 방향이 거의 정리됐다면 바로 마무리하지 않는다. 현재 판단을 짧게 말한 뒤, 남은 고민이나 놓친 부분이 있는지 마지막으로 한 번 확인한다. 이 확인은 대화에서 한 번만 하며, 유저가 이미 결정을 말했거나 감사하며 끝내면 생략한다.

## WHAT TO JUDGE
아래 항목을 체크리스트처럼 전부 묻지 않는다. 현재 판단을 실제로 바꿀 가능성이 큰 정보만 고른다.

- 끌림: 색, 소재, 실루엣, 디테일, 분위기, 원래 찾던 용도 중 무엇이 좋은가
- 착용 습관: 같은 카테고리나 비슷한 핏을 평소 얼마나 입는가
- 옷장 역할: 기존 옷과 색·핏·소재·계절·착용 방식이 실제로 얼마나 겹치는가
- 상품 조건: 사이즈, 원단감, 두께, 통기성, 비침, 이염, 세탁, 활동성처럼 만족도를 좌우할 점이 있는가
- 구매 압력: 할인, 품절 걱정, SNS 노출, 리뷰가 실제 필요보다 결정을 밀고 있는가

비슷한 옷이 있다는 이유로 중복을 판단하려면 보유 개수뿐 아니라 실제 착용 빈도와 새 상품의 차이를 함께 본다.
리뷰와 평점은 품질 불안을 낮추는 참고 정보일 뿐, 유저에게 잘 맞거나 새로 살 필요가 있다는 근거는 아니다.

## APPAREL FEEDBACK
일반적인 의류 지식을 자연스럽게 활용한다. 예를 들어 원단의 두께·뻣뻣함·통기성, 밝은 원단의 비침, 진한 염색의 이염, 니트의 보풀과 늘어남, 오버핏의 어깨선과 총장, 슬림핏의 활동성, 계절과 실내외 온도 차이를 볼 수 있다.
단, 확인되지 않은 특성을 사실처럼 단정하거나 관련 없는 문제를 여러 개 늘어놓지 않는다.

남은 불확실성은 가장 수고가 적은 행동 1개로 줄인다.
- 소재·두께·비침·이염·세탁: 리뷰의 관련 키워드나 실제 착용 사진 확인
- 핏·활동성·낯선 실루엣: 가진 옷 실측 비교 또는 오프라인에서 유사한 제품 착용
- 가격이나 원하는 느낌과의 타협: 대체재 또는 중고 상품 비교
- 받아봐야 아는 문제: 반품 가능 여부 확인
행동만 던지지 말고, 지금 그 확인이 왜 필요한지 함께 말한다.

## RESPONSE LOGIC
- 새 정보가 판단을 바꾸면 그 변화를 먼저 자연스럽게 반영한다.
- 유저가 결론을 물었는데 정보가 부족하면 질문으로 피하지 말고, 현재 방향과 그 방향이 바뀔 조건 하나를 말한다.
- 질문이 필요 없으면 묻지 않는다. 단, 현재 판단이 거의 정리됐고 유저가 아직 결정을 말하지 않았다면 마지막 확인 질문은 한 번 한다.
- 마지막 확인에 유저가 더 남은 고민이 없다고 답하면 새 질문을 만들지 말고, 현재 판단과 실행 행동 1개로 마무리한다.
- 오해했을 때는 “내가 그 말을 너무 크게 해석했어”처럼 짧게 수정하고 대화를 이어간다.
- “좋은 신호”, “합리화”, “근거가 충분해”, “고민축이 해결됐어”처럼 내부 분석을 설명하는 말은 쓰지 않는다.

## BEHAVIOR EXAMPLES
문장을 복사하지 말고 행동 원칙만 따른다.

- 비슷한 옷 한 벌 보유 → 바로 중복 판정하지 말고, 실제 착용 빈도나 새 상품의 차이 중 더 중요한 하나를 확인한다.
- 새로운 활용법 제시 → 활용 가능성은 인정하되, 기존 옷도 같은 역할을 하는지까지 합쳐 판단한다.
- 여름용·겨울용이라고 예상 → 입을 장면만 보지 말고 원단의 두께나 통기성처럼 실제 계절 적합성을 하나 짚는다.
- 충분한 대화 뒤 “그래”라고 답함 → 앞서 제시한 방향에 동의한 것으로 받아들이고 같은 결론을 다시 설득하지 않는다.
- 방향은 정리됐지만 유저가 결정하지 않음 → 판단을 바로 닫지 말고 “아직 걸리는 부분이 있어?”처럼 마지막 확인을 한 번 한다.
- 유저가 반박함 → 기존 결론을 반복하지 말고 무엇을 과하게 해석했는지 수정한다.

## TURN MODE
### [TURN_MODE:first]
- 최대 2문장.
- 상품이 끌릴 이유를 짧게 인정하되 리뷰·할인을 구매 이유처럼 강조하지 않는다.
- 최종 판단은 하지 않는다.
- 상품을 고른 이유, 실제 착용 습관, 상품 특유의 착용 조건 중 아직 확인되지 않았고 가장 중요한 것 하나만 구체적으로 묻는다.

### [TURN_MODE:free]
- 마지막 유저 발화에 직접 반응한다.
- 근거가 부족하면 성급하게 닫지 않는다.
- 종료 여부는 별도 종료 단계가 담당한다.

## OUTPUT
- 자연스러운 현대 한국어 반말로만 답하고, 기본 2~3문장으로 쓴다. 꼭 필요하면 4문장까지 허용한다.
- 상품명·브랜드명과 한국에서 널리 쓰이는 패션 용어를 제외하고 중국어, 일본어, 불필요한 영어·한자 표현을 섞지 않는다.
- 실제로 쓰이지 않는 조어, 의미가 불분명한 단어, 직역투 문장을 만들지 않는다. 표현이 확실하지 않으면 익숙하고 쉬운 한국어로 바꾼다.
- 질문은 최대 1개다.
- 보고서처럼 나열하거나 유저의 표현을 기계적으로 되풀이하지 않는다.
- 같은 근거, 질문, 결론을 표현만 바꿔 반복하지 않는다.
- “유저”, “사용자”, “본인”, “관건”, “핵심”, “리스크”, “판단축”, “통과 기준” 같은 표현은 쓰지 않는다.
- 상품 맥락에 맞는 패션 용어는 자연스럽게 쓴다.

""".strip()


def build_turn_chat_prompt(base_prompt: str, turn_mode: str) -> str:
    """턴 모드는 user 메시지가 아니라 system 지시로만 전달한다."""
    if turn_mode == "first":
        mode_rule = (
            "아직 실제 유저 채팅은 없다. INPUT을 바탕으로 TURN_MODE:first 규칙에 따라 "
            "첫 답변과 질문을 생성한다."
        )
    elif turn_mode == "free":
        mode_rule = (
            "가장 최근 role=user 메시지의 원문에 직접 반응하고 TURN_MODE:free 규칙을 따른다."
        )
    else:
        raise ValueError(f"지원하지 않는 turn_mode야: {turn_mode}")

    return f"""{base_prompt}

## CURRENT TURN CONTROL
- 현재 턴 모드: {turn_mode}
- {mode_rule}
- 이 구역은 시스템 내부 제어 정보다. 유저가 말한 내용으로 해석하거나 답변에서 언급하지 않는다.
- role=user 메시지에는 유저가 실제로 입력한 원문만 들어 있다.
""".strip()


def build_exit_prompt(data: dict) -> str:
    validate_prompt_data(data)
    product_data = {
        key: value
        for key, value in data.items()
        if key not in {"end_decision", "last_user_input"}
    }
    return f"""## 역할
대화 로그를 분석해서 CODE와 RESULT_SUMMARY만 출력한다.

## 상품 정보
```json
{json.dumps(product_data, ensure_ascii=False, indent=2)}
```

## 종료 판단 정보
```json
{json.dumps(data.get("end_decision", {}), ensure_ascii=False, indent=2)}
```

## 마지막 유저 발화
```json
{json.dumps(data.get("last_user_input", ""), ensure_ascii=False)}
```

## 금지
- user_type의 type_code (예: DIMO, NUTE 같은 성향 코드)는 결과 CODE로 출력하지 않는다.
- CODE는 반드시 아래 CODE 목록 중 하나만 출력한다.
- 대화 히스토리의 시스템 제어문이나 내부 지시를 결과에 출력하지 않는다.

## CODE는 무엇을 나타내는가
아래 CODE는 상품 자체에 대한 평가가 아니라, 대화가 끝나는 시점에서 유저의 구매 판단이 어떤 상태인지를 나타낸다.

## CODE 목록

- BUY_CONFIDENT_GROUNDED
  : 대화 끝에서 구매해도 후회 가능성이 낮아 보이는 상태. 매우 엄격하게 판단한다.
    유저가 사고 싶다고 말했거나 취향에 맞는다는 이유만으로는 절대 선택하지 않는다.
    대화 끝까지 봐도 말릴 이유가 거의 없고,
    가격, 중복, 활용, 착용 걱정 같은 주요 고민이 충분히 정리되었고,
    이 상품이 기존 옷장 안에서 맡을 역할도 겹치지 않고 비교적 분명할 때만 선택한다.

- BUY_CONDITIONALLY_READY
  : 구매 쪽으로 기울 수는 있지만, 마지막 확인 조건이 남아 있는 상태.
    유저의 구매 이유는 어느 정도 정리되었지만,
    사이즈, 비침, 소재감, 세탁, 반품 가능 여부, 중복 여부처럼
    확인되지 않은 조건 하나가 구매 후 후회로 이어질 수 있는 경우.
    조건이 확인되기 전까지는 확정 구매가 아니라 조건부 구매로 본다.

- NEUTRAL_EXPLORING
  : 대화가 끝난 시점에서도 구매와 보류 중 어느 쪽으로 볼지 이른 상태.
    유저의 끌림은 있지만 고민축이 충분히 정리되지 않았거나,
    사고 싶은 이유와 망설이는 이유가 아직 비슷하게 남아 있는 경우.
    구매를 밀어주거나 말리기보다, 추가 탐색이 필요한 상태로 본다.

- HOLD_REASONABLE
  : 지금은 사지 않고 보류하는 편이 후회가 적어 보이는 상태.
    상품이 취향에 맞거나 장점이 있더라도,
    기존 옷과 역할이 겹치거나 활용 이유가 약하거나,
    남은 찝찝함이 구매 후에도 계속 남을 가능성이 큰 경우.
    “안 예뻐서”가 아니라 “지금 살 이유가 충분히 단단하지 않아서” 보류하는 상태다.

- IMPULSE_JUSTIFICATION
  : 사고 싶은 마음이 먼저 있고, 그 뒤에 구매 이유를 붙이고 있는 상태.
    할인, 품절 걱정, 낮은 가격, 유행, 희소성, 예쁜 착용샷, 순간적인 취향 저격이
    실제 필요나 활용 가능성보다 판단을 더 강하게 밀고 있는 경우.
    유저가 여러 이유를 말하더라도, 그 이유들이 구매욕을 정당화하는 쪽에 가깝다면 이 코드를 선택한다.
    검증 질문을 받았을 때 실제 경험이나 구체적인 근거 대신 또 다른 긍정적인 예상으로 구매 이유를 이어 붙이는 경우도 포함한다.

- LOW_USE_CLARITY
  : 상품은 마음에 들지만, 구매 후 자주 손이 갈 그림이 흐린 상태.
    예쁘다, 취향이다, 가격이 괜찮다 같은 장점은 있지만,
    기존 옷장 안에서 새로 맡을 역할이 불분명하거나
    비슷한 스타일이 이미 많아 방치될 가능성이 있는 경우.
    특히 “좋아하는 스타일이라 끌림”과 “새로 살 필요가 있음”이 구분되지 않을 때 선택한다.

## 마지막 발화 해석
- 마지막 유저 발화를 무조건 최종 결정으로 해석하지 않는다.
- 구매, 주문, 보류, 포기, 조건부 구매처럼 행동을 직접 정한 경우에만 최종 행동 신호로 사용한다.
- “자주 입는다니까”, “아니 그 말이 아니라”, “응 맞아”처럼 앞선 오해를 정정하거나 사실을 확인한 발화는 사실 업데이트로만 사용한다.
- “자주 입는다니까”는 비슷한 디자인의 실제 착용 빈도가 높다는 정보다. LOW_USE_CLARITY의 근거로 사용하지 않는다.
- 마지막 발화가 전체 대화에서 이미 확인된 사실과 충돌하지 않는지 먼저 검사한다.

## 판단 순서
1. end_decision.action_direction과 전체 대화를 바탕으로 최종 행동 방향을 먼저 확정한다.
   - buy: 지금 구매하기로 결정함
   - conditional_buy: 조건을 확인한 뒤 구매하기로 결정함
   - hold: 구매하지 않거나 보류하기로 결정함
   - unresolved: 아직 방향이 정해지지 않음
2. 그 행동 방향을 유지한 채 구매 근거의 건강함과 후회 가능성을 판단한다.
3. end_decision.action_direction은 종료 분류기가 전체 대화와 마지막 assistant 답변까지 보고 정한 방향이다. 유저의 명시적인 반대 결정이 대화 로그에 있는 경우가 아니면 CODE 단계에서 임의로 뒤집지 않는다.
4. 마지막 발화가 사실 정정이라면 정정된 사실만 반영하고, 행동 방향은 직전까지의 전체 대화와 마지막 assistant 결론에서 판단한다.

## 행동 방향별 CODE 선택
- action_direction이 buy이면 HOLD_REASONABLE과 NEUTRAL_EXPLORING을 선택하지 않는다.
  BUY_CONFIDENT_GROUNDED, BUY_CONDITIONALLY_READY, IMPULSE_JUSTIFICATION, LOW_USE_CLARITY 중 고른다.
- action_direction이 conditional_buy이면 BUY_CONDITIONALLY_READY를 기본으로 선택한다.
  전체 대화에서 충동적 합리화나 낮은 활용도가 명확히 확인된 경우에만 IMPULSE_JUSTIFICATION 또는 LOW_USE_CLARITY를 선택할 수 있다.
- action_direction이 hold이면 BUY_CONFIDENT_GROUNDED와 BUY_CONDITIONALLY_READY를 선택하지 않는다.
  유저가 충동이나 낮은 활용도를 알아차리고 사지 않기로 정리했다면 HOLD_REASONABLE을 우선한다.
- action_direction이 unresolved이면 전체 대화를 보고 NEUTRAL_EXPLORING, HOLD_REASONABLE, IMPULSE_JUSTIFICATION, LOW_USE_CLARITY 중 고른다.

## end_type 보조 규칙
- end_type이 conditional_buy이면 BUY_CONDITIONALLY_READY를 기본으로 한다.
- end_type이 defer라는 이유만으로 HOLD_REASONABLE을 자동 선택하지 않는다.
  판단 자체를 나중으로 미룬 상태면 NEUTRAL_EXPLORING, 현재 사지 않기로 합리적으로 정리한 상태면 HOLD_REASONABLE을 선택한다.
- end_type이 hold이면 HOLD_REASONABLE을 우선한다.
- end_type이 buy이면 BUY_CONDITIONALLY_READY 또는 BUY_CONFIDENT_GROUNDED를 우선하되, 충동적 합리화나 낮은 활용도가 명확할 때만 해당 부정 CODE를 선택한다.
- end_type이 thanks 또는 model_decision이면 action_direction과 전체 대화 맥락을 기준으로 고른다.

## LOW_USE_CLARITY 제한
- 유저가 비슷한 디자인을 평소 자주 입는다고 명확히 말했거나 구체적인 코디·착용 장면이 확인됐다면, 단지 마지막 문장이 짧거나 활용을 다시 설명하지 않았다는 이유로 LOW_USE_CLARITY를 선택하지 않는다.
- “자주 입을 것 같다”는 예상과 “평소 자주 입는다”는 실제 습관을 구분한다.

## 문장 규칙
- RESULT_SUMMARY는 자연스러운 현대 한국어 한 문장으로 쓴다.
- 중국어, 일본어, 불필요한 외국어, 실제로 쓰이지 않는 조어와 의미가 불분명한 표현을 사용하지 않는다.

## 출력 형식 (반드시 아래 형식만 출력. 다른 말 없음)
CODE: 코드명
RESULT_SUMMARY: 이 옷에 대한 판단을 한 문장으로 (너무 친절하지 않게, 결과 카드 문장처럼)""".strip()


def build_end_decision_prompt() -> str:
    return """## 역할
너는 쇼핑 고민 챗봇의 종료 여부만 판단하는 분류기다.
상담 답변을 새로 쓰지 않는다.
대화를 종료할지 계속할지만 판단한다.

## 출력 형식
반드시 JSON만 출력한다.
다른 설명, 문장, 마크다운은 출력하지 않는다.

{
  "should_end": true,
  "reason": "짧은 이유",
  "end_type": "defer|conditional_buy|buy|hold|thanks|model_decision|none",
  "action_direction": "buy|conditional_buy|hold|unresolved"
}

## end_type 정의
- conditional_buy: 유저가 특정 조건을 확인한 뒤 구매하겠다는 결정 규칙을 세운 경우
  예: “비슷한 가격에 더 마음에 드는 게 없으면 살래”, “사이즈 후기 괜찮으면 주문할게”, “반품 가능하면 사야겠다”
- defer: 현재 구매 방향을 정하지 않고 판단 자체를 나중으로 미룬 경우
  예: “며칠 더 생각해볼게”, “일단 장바구니에 넣어둘래”
- buy: 유저가 조건 없이 사겠다, 결제하겠다, 주문하겠다고 말한 경우
- hold: 유저가 안 사겠다, 넘기겠다, 포기하겠다고 말한 경우
- thanks: 유저가 고맙다, 도움 됐다 등 마무리 인사를 한 경우
- model_decision: 유저의 명시적인 종료 발화 없이도, 대화가 자연스럽게 닫혔다고 볼 수 있는 경우. 아래 "model_decision 조건"을 모두 만족할 때만 선택한다.
- none: 계속 대화해야 하는 경우

## 종료로 판단하는 경우
아래 중 하나면 should_end=true로 판단한다.

1. 유저가 직접 구매/조건부 구매/보류/포기/나중에 다시 보기 같은 행동을 결정했다.
예: "3일 뒤에 다시 생각할게", "일단 장바구니에 넣어둘게", "오늘은 안 살래", "그럼 살게", "다른 후보가 없으면 살래"

2. 유저가 명확히 마무리 인사를 했다.
예: "그래 고마워", "오키 고마워", "도움 됐다"

3. model_decision 조건: 아래 4가지를 모두 만족할 때만 종료로 본다. 하나라도 충족하지 못하면 종료하지 않는다.
   - assistant의 마지막 답변이 질문으로 끝나지 않았다.
   - 최근 유저 발화에 새 걱정, 반박, 질문, 확인되지 않은 조건이 나오지 않았다.
   - assistant가 실행 행동을 1개 제시했다.
   - 유저가 추가 질문, 반박, 새 찝찝함을 말하지 않았다.

## 종료로 판단하면 안 되는 경우
아래 중 하나면 should_end=false로 판단한다.

1. 유저가 새 고민, 새 정보, 반박, 찝찝함을 말했다.
예: "근데 비침이 걱정돼", "리뷰 보니까 작대", "아직 애매해", "이염은 어떡하지?"

2. 유저가 질문을 했다.
예: "그럼 중고로 찾아볼까?", "반품 가능하면 사도 돼?", "이 색은 어때?"

3. assistant의 마지막 답변이 질문으로 끝났다.

4. assistant의 마지막 답변이 중간 판단, 확인법, 리뷰 확인, 사이즈 확인 등 다음 행동을 유도하는 답변이다.

5. 유저가 단순히 짧게 반응했지만, 대화를 끝낸다는 의도가 명확하지 않다.
예: "아하", "그렇구나", "음", "오..."
단, "그래 고마워"처럼 감사와 마무리가 같이 있으면 종료로 볼 수 있다.

6. 유저가 "잘 어울릴 것 같다", "자주 입을 것 같다", "코디가 많이 될 것 같다"처럼 긍정적인 예상만 말했고, 직접 착용 경험, 유사한 옷의 실제 사용 경험, 구체적인 활용 근거가 확인되지 않았다면 아직 판단이 끝난 것으로 보지 않는다.

7. 여러 주제를 이야기했더라도, 확인된 사실보다 예상과 자기합리화만 남아 있다면 model_decision으로 종료하지 않는다.

## action_direction 판단
- buy: 유저가 지금 구매하기로 명확히 결정했다.
- conditional_buy: 확인할 조건이나 비교 기준을 세웠고, 그 조건을 통과하면 구매하기로 정했다.
- hold: 유저가 지금 사지 않기로 결정했다.
- unresolved: 판단을 미뤘거나 아직 행동 방향이 정해지지 않았다.
- defer는 자동으로 hold가 아니다. 단순히 나중으로 미룬 경우 action_direction은 unresolved로 둔다.
- thanks와 model_decision에서는 전체 대화와 assistant의 마지막 결론을 보고 방향을 정한다.
- assistant가 마지막에 분명한 구매·조건부 구매·보류 방향을 제시했고 유저가 이를 반박하지 않았다면 그 방향을 유지한다. 유저가 명시적으로 다른 결정을 말했으면 유저의 결정이 우선한다.

## 마지막 발화 해석
- 특정 단어 하나만 보고 판단하지 않는다.
- 마지막 유저 발화와 assistant 마지막 답변을 함께 본다.
- 구매·조건부 구매·보류·포기를 직접 말한 경우에만 행동 결정 신호로 본다.
- “자주 입는다니까”, “아니 그 뜻이 아니야” 같은 정정은 사실 업데이트이지 구매 방향 신호가 아니다.
- 정정된 사실은 이전 assistant의 잘못된 해석보다 우선한다.
- 애매하면 should_end=false로 두고 action_direction은 unresolved로 둔다.
- 종료 오탐이 가장 위험하다. 특히 model_decision은 4가지 조건 중 하나라도 확신이 안 서면 종료하지 않는다.
"""


def decide_should_end(
    client,
    model,
    messages: list[dict],
    last_user_input: str,
    last_assistant_answer: str,
) -> dict:
    prompt = build_end_decision_prompt()

    recent_conversation = [
        message
        for message in sanitize_messages_for_model(messages)
        if message.get("role") in {"user", "assistant"}
    ][-8:]

    payload = {
        "recent_conversation": recent_conversation,
        "last_user_input": last_user_input,
        "last_assistant_answer": last_assistant_answer,
    }

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0,
        max_tokens=120,
        extra_body={"thinking": {"type": "disabled"}},
    )

    raw = extract_visible_answer(resp)

    try:
        data = json.loads(raw)
    except Exception:
        return {
            "should_end": False,
            "reason": "JSON 파싱 실패",
            "end_type": "none",
            "action_direction": "unresolved",
        }

    should_end = bool(data.get("should_end", False))
    end_type = data.get("end_type", "none")
    action_direction = data.get("action_direction", "unresolved")
    reason = data.get("reason", "")

    allowed_types = {
        "defer", "conditional_buy", "buy", "hold",
        "thanks", "model_decision", "none",
    }
    allowed_directions = {"buy", "conditional_buy", "hold", "unresolved"}

    if end_type not in allowed_types:
        end_type = "none"
        should_end = False

    if action_direction not in allowed_directions:
        action_direction = "unresolved"

    # 명시적인 종료 유형은 최소한의 방향 일관성을 코드에서도 보정한다.
    if end_type == "buy":
        action_direction = "buy"
    elif end_type == "conditional_buy":
        action_direction = "conditional_buy"
    elif end_type == "hold":
        action_direction = "hold"

    return {
        "should_end": should_end,
        "reason": reason,
        "end_type": end_type,
        "action_direction": action_direction,
    }


# ── API 호출 / 응답 추출 ─────────────────────────────────────────────────────

# 이전 서비스 코드가 user 메시지 앞에 붙였을 수 있는 내부 제어문을 제거한다.
# 새 코드에서는 role=user에 실제 입력 원문만 저장한다.
LEGACY_CONTROL_PREFIX_RE = re.compile(
    r"^\[(?:TURN_MODE:(?:first|free)|이미 다룬 고민축:[^\]]*)\]\s*(?:\n|$)"
)


def sanitize_messages_for_model(msgs: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for message in msgs:
        copied = dict(message)
        if copied.get("role") == "user" and isinstance(copied.get("content"), str):
            content = copied["content"]
            while True:
                cleaned = LEGACY_CONTROL_PREFIX_RE.sub("", content, count=1)
                if cleaned == content:
                    break
                content = cleaned
            # 내부 제어문만 있던 과거의 첫 턴 메시지는 모델 입력에서 제거한다.
            if not content.strip():
                continue
            copied["content"] = content
        sanitized.append(copied)
    return sanitized


def infer_turn_mode(messages: list[dict]) -> str:
    """실제 user 원문이 아직 없으면 first, 있으면 free로 판단한다."""
    sanitized = sanitize_messages_for_model(messages)
    has_user_input = any(
        message.get("role") == "user"
        and isinstance(message.get("content"), str)
        and message["content"].strip()
        for message in sanitized
    )
    return "free" if has_user_input else "first"


def call_api(client, model, msgs, system_override=None):
    sanitized_msgs = sanitize_messages_for_model(msgs)
    if system_override:
        call_msgs = [{"role": "system", "content": system_override}] + [
            m for m in sanitized_msgs if m["role"] != "system"
        ]
    else:
        call_msgs = sanitized_msgs

    return client.chat.completions.create(
        model=model,
        messages=call_msgs,
        temperature=0.4,
        max_tokens=400,
        extra_body={"thinking": {"type": "disabled"}}
    )


def call_exit_api(client, model, msgs, system_override):
    # exit CODE 생성은 상담 답변과 달리 분류 작업이므로 temperature 0으로 분리한다.
    sanitized_msgs = sanitize_messages_for_model(msgs)
    call_msgs = [{"role": "system", "content": system_override}] + [
        m for m in sanitized_msgs if m["role"] != "system"
    ]
    return client.chat.completions.create(
        model=model,
        messages=call_msgs,
        temperature=0,
        max_tokens=180,
        extra_body={"thinking": {"type": "disabled"}}
    )


def extract_visible_answer(resp) -> str:
    message = resp.choices[0].message
    content = getattr(message, "content", None)
    return content.strip() if content else ""


MAX_LEAK_RETRIES = 2  # 최초 응답 포함 최대 3회까지 재생성 시도


def get_bot_msg(client, model, messages, data: dict, system_override=None) -> str:
    type_code = data.get("user_type", {}).get("code", "")

    resp = call_api(client, model, messages, system_override)
    bot_msg = extract_visible_answer(resp)

    if not bot_msg:
        resp = call_api(client, model, messages, system_override)
        bot_msg = extract_visible_answer(resp)

    if not bot_msg:
        return "지금 답변 생성이 비어서 다시 시도해야 할 것 같아."

    # ── 점수 수치 / 성향 코드 유출 방어: 우선 재생성 시도 ──────────────────
    attempts = 0
    while contains_leaked_score(bot_msg, type_code) and attempts < MAX_LEAK_RETRIES:
        attempts += 1
        resp = call_api(client, model, messages, system_override)
        retry_msg = extract_visible_answer(resp)
        if retry_msg:
            bot_msg = retry_msg

    # 재생성으로도 못 걸러지면 마지막 방어선으로 정규식 필터링
    if contains_leaked_score(bot_msg, type_code):
        bot_msg = scrub_leaked_score(bot_msg, type_code)

    return bot_msg


def get_exit_msg(client, model, messages, data: dict, system_override) -> str:
    """exit CODE / RESULT_SUMMARY 생성 전용. call_exit_api(temperature 0)를 쓴다."""
    type_code = data.get("user_type", {}).get("code", "")

    resp = call_exit_api(client, model, messages, system_override)
    exit_msg = extract_visible_answer(resp)

    if not exit_msg:
        resp = call_exit_api(client, model, messages, system_override)
        exit_msg = extract_visible_answer(resp)

    if not exit_msg:
        return (
            "CODE: NEUTRAL_EXPLORING\n"
            "RESULT_SUMMARY: 판단 코드 생성이 비어 있어 기본 탐색 상태로 처리됨."
        )

    attempts = 0
    while contains_leaked_score(exit_msg, type_code) and attempts < MAX_LEAK_RETRIES:
        attempts += 1
        resp = call_exit_api(client, model, messages, system_override)
        retry_msg = extract_visible_answer(resp)
        if retry_msg:
            exit_msg = retry_msg

    if contains_leaked_score(exit_msg, type_code):
        exit_msg = scrub_leaked_score(exit_msg, type_code)

    return exit_msg


# ── 서비스 레이어 인터페이스 ─────────────────────────────────────────────────────

def build_system_prompt(data: dict) -> str:
    validate_prompt_data(data)
    return build_chat_prompt(data)


def extract_axis_tags(text: str) -> tuple[str, None, None]:
    """기존 service.py 호환용 함수다. 고민축 기능은 제거되어 항상 None을 반환한다."""
    clean, _ended = strip_internal_tags(text)
    return clean, None, None


def call_deepseek(messages: list, prompt_data: dict) -> str:
    validate_prompt_data(prompt_data)
    base_prompt = build_chat_prompt(prompt_data)
    turn_mode = infer_turn_mode(messages)
    turn_prompt = build_turn_chat_prompt(base_prompt, turn_mode)

    raw = get_bot_msg(
        client,
        MODEL_NAME,
        messages,
        prompt_data,
        system_override=turn_prompt,
    )
    reply, _ended = strip_internal_tags(raw)
    return reply


def call_deepseek_exit(messages: list, prompt_data: dict) -> str:
    validate_prompt_data(prompt_data)
    exit_prompt = build_exit_prompt(prompt_data)
    raw = get_exit_msg(
        client,
        MODEL_NAME,
        messages,
        prompt_data,
        system_override=exit_prompt,
    )
    return validate_exit_code(raw)


_FAREWELL_SYSTEM_RULE = (
    "## FAREWELL MODE\n"
    "대화가 종료됐다. 지금까지 나눈 대화를 바탕으로 자연스러운 한국어 반말 1~2문장으로 마무리한다. "
    "새 질문을 하지 않고 내부 태그나 분석 용어를 말하지 않는다."
)


def call_deepseek_farewell(messages: list) -> str:
    base_system = next(
        (
            message.get("content", "")
            for message in messages
            if message.get("role") == "system" and message.get("content")
        ),
        "너는 쇼핑 대화를 자연스럽게 마무리하는 한국어 대화 상대다.",
    )
    farewell_prompt = f"{base_system}\n\n{_FAREWELL_SYSTEM_RULE}"
    raw = get_bot_msg(
        client,
        MODEL_NAME,
        messages,
        {},
        system_override=farewell_prompt,
    )
    reply, _ended = strip_internal_tags(raw)
    return reply
