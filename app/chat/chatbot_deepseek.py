import os
import re
import time
import json
from pathlib import Path
from typing import Optional
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


# ── INPUT JSON ───────────────────────────────────────────────────────────────

INPUT_JSON = {
  "product_info": [
    "상품명: [꾸안꾸🐰] 보호본능 오버핏, 썸머 데일리 빈티지 생지 데님 박시 오버핏 카라 반팔셔츠 청남방 (3color)",
    "가격: 25,700원 → 20,500원 (20% 할인)",
    "상품 스타일: 진한 생지 데님 소재에 여유로운 오버핏 실루엣이 돋보이는 카라 반팔 셔츠입니다. 빈티지한 무드와 캐주얼한 디자인이 결합되어 데일리로 활용하기 좋습니다."
  ],
  "confirmed_sentences": [
    "이 유저는 빈티지, 스트릿 스타일을 좋아하는 유저입니다. 상품명에 명시된 '빈티지' 무드와 오버핏의 캐주얼한 디자인이 회원의 빈티지 및 스트릿 선호 스타일과 잘 부합합니다.",
    "SNS를 보다가 수동적으로 노출된 상품으로, 2~3일 전부터 눈여겨보고 있는 상태입니다.",
    "25,700원짜리 상품이 20% 할인되어 20,500원에 판매 중입니다. 가벼운 할인 자극이 있습니다. 유저는 이 가격이 적당하다고 느끼고 있습니다.",
    "리뷰 수는 2529개입니다. 평점은 4.8점으로 매우 높습니다.",
    "상품명에 '보호본능' 같은 마케팅 표현이 있습니다.",
    "이 상품이 유저에게 주는 충동 점수는 69점이고 유저의 취향과 일치하는 점수는 64점입니다."
  ],
  "user_type": {
    "code": "DUTE",
    "axis_summary": [
      "[확신 방식/U] 타인의 검증을 통해 구매 확신을 얻는다. 리뷰 수, 평점, 찜 수, 구매량, 착용 후기, 랭킹처럼 많은 사람의 선택이나 평가를 중요한 신뢰 신호로 본다.",
      "[스타일 방향/T] 현재 많이 보이는 스타일, 유행 키워드, 시즌 트렌드, 대중적 착용감에 끌린다. 상품이 트렌드 흐름 안에 있는지, 지금 입었을 때 어색하지 않은지가 중요하다.",
      "[가격 해석 방식/E] 할인율, 쿠폰, 무료배송, 정가 대비 이득감처럼 거래 조건이 만족도에 영향을 준다. 단, 가격은 U/I와 T/M 판단 이후의 보조 근거로 사용한다.",
      "[구매 동기 참고/D] 필요성보다 감정적 끌림과 즉각적 매력을 더 크게 느끼는 편이다. 단, 상품 피쳐 판단에서는 보조 정보로만 사용한다."
    ],
    "priority_rule": "상품 판단 시 U(확신 방식)와 T(스타일 방향)를 동등한 최우선으로 보고, E(가격 해석 방식)는 보조 기준, D(구매 동기 참고)는 대화 톤 참고용으로만 사용한다",
  }
}


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

    # exit 모델은 CODE/RESULT_SUMMARY 두 줄만 출력해야 하지만, 대화 히스토리에
    # <AXIS:..> 태그가 섞여 들어오면 그 습관이 그대로 옮겨붙어 출력될 때가 있다.
    # (강제 종료로 run_exit이 바로 호출될 때 특히 관찰됨.) 여기서 최종적으로
    # 두 줄만 남기고 그 안에 남은 <AXIS:..> 조각까지 정규식으로 한 번 더 제거한다.
    clean_code_line = AXIS_TAG_RE.sub("", code_line).strip()
    clean_summary_line = AXIS_TAG_RE.sub("", summary_line).strip()

    return f"{clean_code_line}\n{clean_summary_line}"


# ── 내부 태그 처리 ────────────────────────────────────────────────────────────

def strip_internal_tags(text: str) -> tuple[str, bool]:
    if not text:
        return text, False
    ended = "<END_DECISION>" in text
    text = text.replace("<END_DECISION>", "").strip()
    return text, ended


# ── 고민축 상태 태그 처리 ──────────────────────────────────────────────────────
# "고민축 몇 개 다뤘는지"를 모델이 매 턴 대화 로그를 다시 훑어서 암산하게 하는 대신,
# 모델은 <AXIS:축이름> 태그만 붙이고 누적 관리는 코드(main 루프의 seen_axes)가 담당한다.

AXIS_TAG_RE = re.compile(r"<AXIS:([^>]*)>")


def extract_axis_tag(text: str) -> tuple[str, Optional[str]]:
    """<AXIS:..> 태그를 파싱해서 제거하고 (본문, 축이름)을 반환."""
    if not text:
        return text, None
    axis_match = AXIS_TAG_RE.search(text)
    axis = axis_match.group(1).strip() if axis_match else None
    clean = AXIS_TAG_RE.sub("", text)
    return clean.strip(), axis


# ── 고민축 이름 정규화 ────────────────────────────────────────────────────────
# 모델이 같은 고민축을 매번 조금씩 다른 이름으로 출력하면 seen_axes에 중복 축적될
# 수 있어서, 자주 겹치는 표현을 하나의 이름으로 모아준다.

AXIS_ALIASES = {
    "활용도": "활용",
    "코디": "활용",
    "착용": "착용감",
    "핏": "사이즈",
    "가격 부담": "가격",
    "할인": "가격",
    "중복 구매": "중복",
}


def normalize_axis(axis: str) -> str:
    axis = axis.strip()
    return AXIS_ALIASES.get(axis, axis)


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
9. “그래”, “응”, “맞아”는 바로 앞 맥락에 대한 동의로 본다. 뒤에 반박·새 걱정·미결 표현이 이어지면 뒤 내용을 우선한다. 유저가 말하지 않은 구체적인 행동까지 임의로 만들지는 않는다.
10. 유저가 “어?”, “아니”, “그 말이 아닌데”라고 하면 이전 판단을 변호하지 않는다. 과하게 해석한 부분을 짧게 인정하고 놓친 내용을 다시 반영한다.
11. 매 답변은 구체적인 질문, 현재 판단, 바로 할 확인 행동 중 하나로 끝낸다. “정해보자”, “이야기해보자”, “이게 중요한 것 같아” 같은 진행 문장으로 끝내지 않는다.
12. 유저가 직접 구매·보류·포기를 말하지 않았는데 현재 방향이 거의 정리됐다면 바로 마무리하지 않는다. 현재 판단을 짧게 말한 뒤, 남은 고민이나 놓친 부분이 있는지 마지막으로 한 번 확인한다. 이 확인은 대화에서 한 번만 하며, 유저가 이미 결정을 말했거나 감사하며 끝내면 생략한다.

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
- 한국어 반말, 기본 2~3문장. 꼭 필요하면 4문장까지 허용한다.
- 질문은 최대 1개다.
- 보고서처럼 나열하거나 유저의 표현을 기계적으로 되풀이하지 않는다.
- 같은 근거, 질문, 결론을 표현만 바꿔 반복하지 않는다.
- “유저”, “사용자”, “본인”, “관건”, “핵심”, “리스크”, “판단축”, “통과 기준” 같은 표현은 쓰지 않는다.
- 상품 맥락에 맞는 패션 용어는 자연스럽게 쓴다.

## INTERNAL TAG
매 답변 마지막 줄에 중심적으로 다룬 고민축 하나만 붙인다.

<AXIS:축이름>

- 축이름은 가격, 활용, 착용감, 코디, 세탁, 사이즈, 중복, 소재, 구매이유처럼 짧게 쓴다.
- 태그는 유저에게 보이지 않는 내부 신호다.
- <END_DECISION>은 출력하지 않는다.
- [이미 다룬 고민축]은 언급된 축의 목록일 뿐, 해결된 축의 목록이 아니다.
""".strip()

def build_exit_prompt(data: dict) -> str:
    return f"""## 역할
대화 로그를 분석해서 CODE와 RESULT_SUMMARY만 출력한다.

## 상품 정보
```json
{json.dumps(data, ensure_ascii=False, indent=2)}
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
- 대화 히스토리에 <AXIS:축이름> 태그가 남아 있더라도, 그 형식을 따라 하지 않는다. <AXIS:...>를 포함한 어떤 내부 태그도 출력하지 않는다.

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

## 판단 기준
구매 의향이 아니라 구매 판단의 건강함과 후회 가능성 기준으로 CODE를 선택한다.
마지막 유저 발화를 최종 결정 신호로 참고한다.

end_decision.end_type이 defer이면 HOLD_REASONABLE을 우선한다.
end_decision.end_type이 hold이면 HOLD_REASONABLE을 우선한다.
end_decision.end_type이 buy이면 BUY_CONDITIONALLY_READY 또는 BUY_CONFIDENT_GROUNDED 중 고른다.
확인할 조건이 남아 있으면 BUY_CONDITIONALLY_READY를 우선한다.
end_decision.end_type이 thanks 또는 model_decision이면 전체 대화 맥락을 기준으로 고른다.

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
  "end_type": "defer|buy|hold|thanks|model_decision|none"
}

## end_type 정의
- defer: 유저가 나중에 다시 생각하겠다, 보류하겠다, 장바구니/위시리스트에 넣겠다고 말한 경우
- buy: 유저가 사겠다, 결제하겠다, 주문하겠다고 말한 경우
- hold: 유저가 안 사겠다, 넘기겠다, 포기하겠다고 말한 경우
- thanks: 유저가 고맙다, 도움 됐다 등 마무리 인사를 한 경우
- model_decision: 유저의 명시적인 종료 발화 없이도, 대화가 자연스럽게 닫혔다고 볼 수 있는 경우. 아래 "model_decision 조건"을 모두 만족할 때만 선택한다.
- none: 계속 대화해야 하는 경우

## 종료로 판단하는 경우
아래 중 하나면 should_end=true로 판단한다.

1. 유저가 직접 구매/보류/포기/나중에 다시 보기 같은 행동을 결정했다.
예: "3일 뒤에 다시 생각할게", "일단 보류할게", "장바구니에 넣어둘게", "오늘은 안 살래", "그럼 살게"

2. 유저가 명확히 마무리 인사를 했다.
예: "그래 고마워", "오키 고마워", "도움 됐다"

3. model_decision 조건: 아래 4가지를 모두 만족할 때만 종료로 본다. 하나라도 충족하지 못하면 종료하지 않는다.
   - assistant의 마지막 답변이 질문으로 끝나지 않았다.
   - 최근 대화에서 새 고민축이 나오지 않았다.
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

6. 유저가 "잘 어울릴 것 같다", "자주 입을 것 같다", "코디가 많이 될 것 같다"처럼 긍정적인 예상만 말했고, 직접 착용 경험, 유사한 옷의 실제 사용 경험, 구체적인 활용 근거가 확인되지 않았다면 해당 고민축은 아직 해결되지 않은 것으로 본다.

7. 이미 다룬 고민축의 개수가 많더라도, 그 축들이 유저의 예상이나 자기합리화만으로 넘어간 경우에는 model_decision으로 종료하지 않는다.

## 판단 기준
- 특정 단어 하나만 보고 판단하지 않는다.
- 마지막 유저 발화와 assistant 마지막 답변을 함께 본다.
- 애매하면 should_end=false로 둔다.
- 종료 오탐이 가장 위험하다. 특히 model_decision은 4가지 조건 중 하나라도 확신이 안 서면 종료하지 않는다.
"""


def decide_should_end(
    client,
    model,
    messages: list[dict],
    last_user_input: str,
    last_assistant_answer: str,
    seen_axes: list[str],
) -> dict:
    prompt = build_end_decision_prompt()

    payload = {
        "recent_conversation": messages[-8:],
        "last_user_input": last_user_input,
        "last_assistant_answer": last_assistant_answer,
        "seen_axis_count": len(seen_axes),
        "seen_axes": list(seen_axes),
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
        }

    should_end = bool(data.get("should_end", False))
    end_type = data.get("end_type", "none")
    reason = data.get("reason", "")

    allowed_types = {"defer", "buy", "hold", "thanks", "model_decision", "none"}
    if end_type not in allowed_types:
        end_type = "none"
        should_end = False

    return {
        "should_end": should_end,
        "reason": reason,
        "end_type": end_type,
    }


# ── API 호출 / 응답 추출 ─────────────────────────────────────────────────────

def call_api(client, model, msgs, system_override=None):
    if system_override:
        call_msgs = [{"role": "system", "content": system_override}] + [
            m for m in msgs if m["role"] != "system"
        ]
    else:
        call_msgs = msgs

    return client.chat.completions.create(
        model=model,
        messages=call_msgs,
        temperature=0.6,
        max_tokens=400,
        extra_body={"thinking": {"type": "disabled"}}
    )


def call_exit_api(client, model, msgs, system_override):
    # exit CODE 생성은 상담 답변과 달리 분류 작업이므로 temperature 0으로 분리한다.
    call_msgs = [{"role": "system", "content": system_override}] + [
        m for m in msgs if m["role"] != "system"
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


# ── 유틸 ────────────────────────────────────────────────────────────────────

DIVIDER      = "─" * 60
THIN_DIVIDER = "·" * 60


def print_meta(turn, elapsed):
    print(f"\n{THIN_DIVIDER}")
    print(f"  턴 {turn}  |  응답 시간: {elapsed:.2f}초  |  모델: {MODEL_NAME}")
    print(THIN_DIVIDER)


def print_bot(text):
    print(f"\n🤖  {text}\n")


def run_exit(client, MODEL_NAME, messages, exit_prompt, data, turn, total_elapsed, label="EXIT"):
    print(f"\n{DIVIDER}")
    print(f"  [{label}]  |  총 턴: {turn}  |  총 소요 시간: {total_elapsed:.2f}초")
    print(f"{DIVIDER}")
    t0 = time.time()
    raw_output = get_exit_msg(client, MODEL_NAME, messages, data, system_override=exit_prompt)
    code_output = validate_exit_code(raw_output)
    elapsed = time.time() - t0
    print(f"\n  종료 코드 / 결과창 요약 ({elapsed:.2f}초)")
    print(f"  {code_output}")
    print(f"{DIVIDER}\n")


# ── 메인 루프 ────────────────────────────────────────────────────────────────

def main():
    print(f"\n{DIVIDER}")
    print(f"  쇼핑 고민 챗봇  |  모델: {MODEL_NAME}")
    print(f"{DIVIDER}")
    print("  종료하려면 'q' 또는 'quit' 입력.")
    print(f"{DIVIDER}\n")

    chat_prompt = build_chat_prompt(INPUT_JSON)
    exit_prompt = build_exit_prompt(INPUT_JSON)

    messages = [{"role": "system", "content": chat_prompt}]
    turn = 0
    total_elapsed = 0.0
    seen_axes: list[str] = []  # 지금까지 다뤄진 고민축. 모델이 암산하지 않고 코드가 누적 관리.

    # ── 첫 답변 ──────────────────────────────────────────────────────────────
    turn += 1
    messages.append({"role": "user", "content": "[TURN_MODE:first]"})
    t0 = time.time()
    raw_bot_msg = get_bot_msg(client, MODEL_NAME, messages, INPUT_JSON)
    elapsed = time.time() - t0
    total_elapsed += elapsed
    clean_text, axis = extract_axis_tag(raw_bot_msg)

    # 메인 모델은 END_DECISION을 출력하지 않지만, 혹시 출력해도 화면에는 보이지 않게 제거만 한다.
    bot_msg, _ignored_ended = strip_internal_tags(clean_text)

    if axis:
        axis = normalize_axis(axis)
        if axis not in seen_axes:
            seen_axes.append(axis)
    messages.append({"role": "assistant", "content": bot_msg})
    print_meta(turn, elapsed)
    print_bot(bot_msg)
    # 첫 턴에는 종료 판단을 돌리지 않는다.

    # ── 대화 루프 ─────────────────────────────────────────────────────────────
    while True:
        user_input = input("👤  나: ").strip()
        if not user_input:
            continue

        # 테스트용 수동 종료
        if user_input.lower() in ("q", "quit", "종료", "그만"):
            run_exit(client, MODEL_NAME, messages, exit_prompt, INPUT_JSON, turn, total_elapsed, label="EXIT")
            break

        # 고민축 목록은 모델이 매 턴 다시 세지 않도록, 코드가 누적해서 넣어준다
        if seen_axes:
            state_line = f"[이미 다룬 고민축: {', '.join(seen_axes)}]\n"
        else:
            state_line = ""

        messages.append({
            "role": "user",
            "content": f"[TURN_MODE:free]\n{state_line}{user_input}"
        })
        turn += 1
        t0 = time.time()
        raw_bot_msg = get_bot_msg(client, MODEL_NAME, messages, INPUT_JSON)
        elapsed = time.time() - t0
        total_elapsed += elapsed
        clean_text, axis = extract_axis_tag(raw_bot_msg)

        # 메인 모델은 END_DECISION을 출력하지 않지만, 혹시 출력해도 화면에는 보이지 않게 제거만 한다.
        bot_msg, _ignored_ended = strip_internal_tags(clean_text)

        if axis:
            axis = normalize_axis(axis)
            if axis not in seen_axes:
                seen_axes.append(axis)

        messages.append({"role": "assistant", "content": bot_msg})
        print_meta(turn, elapsed)
        print_bot(bot_msg)

        end_decision = decide_should_end(
            client=client,
            model=END_DECISION_MODEL,
            messages=messages,
            last_user_input=user_input,
            last_assistant_answer=bot_msg,
            seen_axes=seen_axes,
        )

        if end_decision.get("should_end"):
            exit_data = {
                **INPUT_JSON,
                "end_decision": end_decision,
                "last_user_input": user_input,
            }

            run_exit(
                client,
                MODEL_NAME,
                messages,
                build_exit_prompt(exit_data),
                exit_data,
                turn,
                total_elapsed,
                label="AUTO EXIT",
            )
            break


# ── 서비스 레이어 인터페이스 ─────────────────────────────────────────────────────
# app/chat/service.py가 호출하는 이름들. get_bot_msg/get_exit_msg/extract_axis_tag를
# 감싸서 서비스 레이어가 기대하는 시그니처(복수형 extract_axis_tags 등)로 맞춰준다.

def build_system_prompt(data: dict) -> str:
    return build_chat_prompt(data)


def extract_axis_tags(text: str) -> tuple[str, Optional[str], Optional[str]]:
    clean, axis = extract_axis_tag(text)
    return clean, axis, None


def call_deepseek(messages: list, prompt_data: dict = None) -> str:
    data = prompt_data or {}
    raw = get_bot_msg(client, MODEL_NAME, messages, data)
    reply, _ended = strip_internal_tags(raw)
    return reply


def call_deepseek_exit(messages: list, prompt_data: dict) -> str:
    exit_p = build_exit_prompt(prompt_data)
    raw = get_exit_msg(client, MODEL_NAME, messages, prompt_data, system_override=exit_p)
    return validate_exit_code(raw)


_FAREWELL_TRIGGER = (
    "[TURN_MODE:free]\n"
    "대화가 종료돼. 지금까지 나눈 대화를 바탕으로 따뜻하게 1~2문장으로 마무리해줘."
)


def call_deepseek_farewell(messages: list) -> str:
    msgs = messages + [{"role": "user", "content": _FAREWELL_TRIGGER}]
    raw = get_bot_msg(client, MODEL_NAME, msgs, {})
    reply, _ended = strip_internal_tags(raw)
    reply, _axis, _status = extract_axis_tags(reply)
    return reply


if __name__ == "__main__":
    main()