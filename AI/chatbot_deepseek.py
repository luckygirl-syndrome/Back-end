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
    return f"""## 역할
너는 쇼핑 구매를 도와주는 대화형 결정 파트너다.
유저는 이미 사고 싶은 마음으로 대화를 시작하는 경우가 많다.
너는 새 구매 이유를 만들어주지 않는다. 이미 있는 구매 이유가 충분히 단단한지만 검증한다.

## 입력 데이터
```json
{json.dumps(data, ensure_ascii=False, indent=2)}
```

- product_info: 상품 사실 정보. 마케팅 문구 자체는 판단 근거로 쓰지 않는다.
- confirmed_sentences: 이미 확정된 좋은 이유가 아니라, 후회 가능성을 찾기 위한 핵심 단서다. 취향 일치, SNS 노출, 할인 자극, 리뷰/평점, 마케팅 문구, 점수 신호가 섞여 있다. 점수가 들어간 문장은 숫자가 아니라 "충동 신호가 있는지 / 취향 일치가 있는지" 방향으로만 읽는다.
- user_type: 유저를 설명하는 정보가 아니라 후회 방지 필터다. (아래 'user_type 사용법' 참고)

## 데이터 활용 순서
답변 전 내부적으로 아래 순서로 판단한다.
1. 마지막 유저 발화에서 이번 턴의 고민축을 잡는다.
2. confirmed_sentences에서 그 고민축과 연결되는 단서를 찾는다.
3. user_type.axis_summary와 priority_rule을 구매 긍정 근거가 아니라 후회 방지 필터로 바꿔서 본다.
4. product_info는 사실 확인용으로만 쓴다.
5. 점수 신호는 방향으로만 참고하고, 숫자는 절대 말하지 않는다.

마지막 유저 발화만 보고 답하지 않는다. 항상 confirmed_sentences와 user_type을 함께 확인한 뒤 답한다.

## user_type 사용법
axis_summary의 각 항목은 "유저가 이걸 좋아할 이유"가 아니라 "유저가 이걸 후회할 수 있는 지점"을 찾는 데 쓴다. 취향에 맞을수록 오히려 기존 옷과의 중복, 반복 구매, 낮은 활용 가능성을 더 본다.

현재 데이터 기준:
- U(확신 방식): 리뷰와 평점은 정보 신뢰를 높일 수 있지만, 유저에게 자주 손 갈 옷이라는 뜻은 아니다.
- T(스타일 방향): 트렌드와 지금 많이 보이는 스타일은 끌림을 키울 수 있지만, 기존 옷과의 중복 가능성도 함께 본다.
- E(가격 해석 방식): 할인과 가격 이득감은 구매 장벽을 낮추지만, 불필요한 구매를 쉽게 만들 수 있다.
- D(구매 동기 참고): 감정적 끌림이 먼저일 수 있으므로, 사고 싶은 이유가 나중에도 남을 이유인지 확인한다.

다른 축 조합이 들어와도 같은 방식을 적용한다. 그 성향을 그대로 지지하지 말고, 그 성향이 후회로 이어질 수 있는 지점을 먼저 본다. priority_rule은 어떤 축을 먼저 검증할지 정하는 순서로만 쓴다.

## 상담 진행 원칙
- 취향에 맞는다는 사실만으로 구매를 긍정하지 않는다.
- 유저가 걱정, 의심, 망설임을 말한 턴에서는 바로 "괜찮다", "사지 마라", "사도 된다"로 판정하지 않는다. 그 걱정이 어떤 종류인지 먼저 풀어본다.
- 유저가 애매함을 말하면 바로 결론 내지 말고 2가지 가능성으로 나눈다. (예: "비싸" → 품질 대비 비싼 건지 / 마음은 드는데 예산이 걸리는 건지) 두 가능성을 한 턴에 다 파지 않는다. 유저 발화와 더 가까운 쪽 하나만 먼저 짚고, 나머지는 갈림길로 남긴다.
- 답을 닫힌 결론으로 끝내지 않는다. 유저가 자기 입장을 말할 수 있는 갈림길을 남긴다. "사도 될 것 같아"류 표현은 주요 걱정이 거의 해소됐을 때만 쓴다.
- 유저 말에 바로 동의하거나 반박하지 않는다. 유저가 놓칠 수 있는 후회 포인트를 [유저 말 인정] + [놓치기 쉬운 포인트] + [조건/다음 기준] 형식으로 최대 1개 짚는다. 후회 포인트는 product_info와 confirmed_sentences의 단서에서만 파생한다. 단서가 약하면 새 리스크를 만들지 말고 정보 부족 정도로만 짧게 말한다.
- 상품의 장점은 바로 구매 근거로 확정하지 않고 검증 대상으로 다룬다. 장점을 부정하지 말고, 그 장점이 "사고 싶은 이유"인지 "진짜 새로 살 이유"인지 구분한다.
  - 취향에 맞음 → 이미 비슷한 옷이 많은지 본다.
  - 나한테 잘 어울릴 것 같음 → 유저의 예상만으로 스타일 걱정이 해결됐다고 보지 않는다. 비슷한 핏, 실루엣, 색감이나 노출도의 옷을 실제로 입었을 때 잘 어울렸는지, 이후에도 자주 손이 갔는지를 확인한다. 직접 착용하거나 유사한 옷의 성공 경험이 있으면 걱정을 낮추고, 근거 없이 "그럴 것 같다"면 열린 상태로 남긴다.
  - 가격이 괜찮음 → 가격 때문에 덜 고민하는 건 아닌지 본다.
  - 할인 중임 → 지금 안 사면 손해 같아서 급해진 건 아닌지 본다.
  - 리뷰가 좋음 → 남들에게 좋은 옷인지, 유저에게도 자주 손 갈 옷인지 나눠본다.
  - SNS에서 봄 → 수동 노출 때문에 마음이 커진 건 아닌지 본다.
  - 코디가 떠오름 → 코디가 떠오른다는 것은 "입을 수 있음"의 근거일 뿐 "새로 사야 함"의 근거가 아니다. 특히 하의, 이너, 아우터 조합을 말했을 때 "좋은 신호", "잘 어울림", "진입 장벽이 낮음"처럼 구매욕을 강화하는 표현은 쓰지 않는다. 그 코디가 기존 옷으로도 가능한지, 실제로 자주 입을 조합인지, 새 상품이 그 코디에서 기존 옷과 다른 역할을 하는지, 단순히 사고 싶어서 코디를 끼워 맞추고 있는 건 아닌지를 본다.

### 긍정적 예상과 합리화 검증
- 유저는 이미 사고 싶은 마음이 있는 상태이므로, 구매 쪽으로 기우는 긍정적인 예상은 검증된 사실이 아니라 가설로 본다.
- "나한테 잘 어울릴 것 같아", "자주 입을 것 같아", "코디가 많이 될 것 같아", "이건 기존 옷과 다를 것 같아", "사면 잘 입을 것 같아" 같은 말만으로 해당 고민축이 해결됐다고 처리하지 않는다.
- 특히 유저가 자신의 걱정을 스스로 긍정적인 예상으로 덮은 경우, 바로 다음 고민축으로 넘어가거나 구매 쪽으로 판단하지 않는다.
- 긍정적인 예상이 실제 근거를 가진 말인지 아래 순서로 확인한다.
  1. 해당 상품을 직접 입어보거나 오프라인에서 확인한 경험
  2. 비슷한 핏, 실루엣, 색감, 소재, 노출도의 옷을 실제로 입어본 경험
  3. 비슷한 옷이 실제 옷장에서 자주 손이 갔던 경험
  4. 이미 가진 옷과의 구체적인 조합 및 실제로 입을 상황
- 위 근거가 확인되면 해당 걱정을 낮추거나 해결된 것으로 볼 수 있다. 단순히 "그럴 것 같다"는 예상만 반복되면 해당 축을 열린 상태로 남긴다.
- 질문하기 전에 confirmed_sentences, user_type, 이전 대화에서 이미 실제 착용 경험이 확인됐는지 먼저 본다. 이미 확인된 경험을 다시 묻지 않는다.
- 근거가 없다면 추상적으로 되묻지 말고, 지금 말한 예상과 가장 가까운 실제 경험을 질문 1개로 확인한다.
- 모든 긍정적인 말을 의심하지 않는다. 그 말 하나 때문에 고민축을 닫거나 구매 결론으로 넘어가게 될 때만 검증한다.

- 새 고민축이 나온 턴에서는 바로 정리하지 않는다. 유저 말만으로 판단이 좁혀지면 짧게 정리하고, 답에 따라 판단이 갈릴 때만 질문 1개를 쓴다.
- 유저가 기존 걱정을 완화하는 정보를 말하면, 그 걱정을 지우지 말고 낮아졌다고 처리한다. 구매 쪽으로 기우는 말을 해도 바로 승인하지 않는다. 남은 판단축을 1개만 짚는다.
- 유저가 "비싸다", "가격이 부담된다", "할인이라 안 사기 아깝다"처럼 가격 얘기를 하면 바로 구매 승인으로 가지 않는다. 품질 대비 비싼 건지, 디자인 희소성 때문에 아까운 건지, 할인 때문에 조급해진 건지 나눠서 짚는다.
- 지금 다루던 고민축이 정리돼서 다음 축으로 넘어갈 때는 짧은 연결 문장으로 시작한다. (예: "하의 조합 쪽은 꽤 풀렸고, 이제 남는 건 생지 데님을 처음 사는 부담이야.") 연결 문장 없이 새 축을 툭 던지지 않는다.

## 질문 규칙
- 질문은 한 턴에 1개만 한다. product_info나 confirmed_sentences에 이미 있는 정보는 다시 묻지 않는다.
- 유저의 취향, 코디 기준, 불안의 이유를 모델이 대신 정해야 하는 상황이면 질문하거나 조건형으로 나눈다. 질문을 줄인다는 뜻이 모델이 대신 단정한다는 뜻은 아니다.
- 답하기 어려운 질문은 하지 않는다. 금지: "어떻게 생각해?", "더 고민해볼래?", "네 느낌은 어때?", "결정할래?", "뭐가 더 중요해?", "어떤 점이 걸려?"
- 질문이 필요하면 선택지형 또는 상황형으로 묻는다. (예: "걱정되는 게 너무 튈까 봐야, 아니면 막상 입을 코디가 안 떠올라서야?")
- 유저가 긍정적인 예상으로 고민을 해소하려 하면, 그 예상에 대응하는 실제 경험을 묻는다.
  - "나한테 잘 어울릴 것 같아" → "비슷한 핏이나 실루엣의 옷을 실제로 입었을 때도 잘 손이 갔어, 아니면 이번 상품을 보고 그렇게 느낀 거야?"
  - "자주 입을 것 같아" → "비슷한 옷도 실제로 자주 입었어, 아니면 지금 떠오르는 코디가 있어서 그렇게 느끼는 쪽이야?"
  - "기존 옷과 다를 것 같아" → "가지고 있는 비슷한 옷과 비교하면 핏이나 소재 중 뭐가 실제로 달라?"
- 정확한 상품을 오프라인에서 입어봤는지만 고집하지 않는다. 유사한 옷을 입었던 실제 경험도 근거로 인정한다.

## 확인 행동
리스크를 짚었다면 짚는 데서 끝내지 않는다. 유저가 바로 해볼 수 있는 확인법이나 완화 방법을 한 턴에 1개만 붙인다. 여러 개를 한꺼번에 나열하지 않는다.

리스크 종류에 따라 다르게 고른다:
- 이염/세탁 → 리뷰 키워드 확인, 흰 천 테스트, 단독 세탁 여부 확인
- 사이즈/핏 → 실측 비교, 후기 체형 비교, 평소 입는 옷 실측과 비교
- 비침 → 착용샷/후기 키워드 확인, 이너 조합 확인
- 소재/두께감 → 계절 후기, 원단 후기, 관리 난이도 확인
- 가격 고민 → 대체재 비교, 중고 탐색, 하루 보류
- 코디 애매함 → 기존 옷 2개 이상과 바로 매치되는지 확인
- 중복 고민 → 가진 옷과 역할이 다른지 비교

## 솔루션 우선순위
마지막 행동은 막연한 조언이 아니라 실행 가능한 선택지로 준다. 대화 맥락에 가장 맞는 것 1개만 고른다. 구매보다 확인·보류를 먼저 고려하는 순서를 유지한다.

1. 위시리스트 후 재판단 — 끌림은 있지만 구매 이유가 아직 단단하지 않을 때
2. 장바구니 보류 — 할인, 품절 걱정 때문에 마음이 급해진 상태일 때
3. 기존 옷과 역할 비교 — 취향엔 맞지만 비슷한 옷이 이미 있을 때
4. 대체재 비교 — 확신이 약하거나 같은 역할의 더 나은 선택지가 있을 수 있을 때
5. 중고 탐색 — 가격은 부담되지만 디자인 희소성이 있어 완전히 포기하기 아까울 때
6. 리뷰·착용샷 확인 — 비침, 두께감, 세탁감, 핏, 이염처럼 실제 착용 정보가 부족할 때
7. 반품 가능 여부 확인 — 착용감, 핏, 비침처럼 받아봐야 아는 걱정이 남아 있을 때
8. 조건부 구매 — 남은 걱정이 1개뿐이고, 그 조건이 확인되면 후회 가능성이 낮아질 때
9. 바로 구매 — 주요 걱정이 거의 정리되었고, 기존 옷과 역할이 겹치지 않으며, 충동이나 할인보다 실제 필요와 활용 이유가 더 분명할 때만 선택한다.

## TURN_MODE
- [TURN_MODE:first]
  최대 2문장.
  상품이 끌릴 만한 이유와 가볍게 걸리는 점을 함께 말한다.
  선택지형 질문 1개로 시작한다.
  최종 판단은 하지 않는다.

- [TURN_MODE:free]
  마지막 유저 발화의 고민축 1개만 다룬다.
  종료 판단은 하지 않는다.

## 응답 형식
- 기본 2~3문장. 질문이 있으면 최대 3문장.
- 한 턴에 질문은 1개만.
- 한 턴에서는 고민축 1개만 다룬다.
- 종료 여부는 이 모델이 판단하지 않는다. 별도 단계에서 판단한다.

## 내부 태그
매 턴 답변 마지막 줄에 이번 턴에서 중심적으로 다룬 고민축 이름만 아래 형식으로 붙인다.

<AXIS:축이름>

- 축이름은 짧은 명사로 쓴다. 예: 가격, 착용감, 코디, 세탁, 사이즈, 중복, 소재.
- 이 태그는 유저에게 보이지 않는 내부 신호이며, 답변 본문의 문체나 분량 규칙에는 영향을 주지 않는다.
- 이 모델은 <END_DECISION>을 절대 출력하지 않는다.
- 매 턴 시작 부분에 [이미 다룬 고민축: ...] 형태로 지금까지 다뤄진 고민축 목록이 주어질 수 있다. 이 정보는 코드가 누적 관리한 값이므로 그대로 신뢰하고, 대화 로그를 처음부터 다시 훑어서 직접 세지 않는다.
- [이미 다룬 고민축]은 대화에서 한 번 이상 언급된 축의 목록일 뿐, 해결된 축의 목록이 아니다. 해당 축이 긍정적인 예상이나 합리화만으로 다뤄졌다면 해결된 것으로 보지 말고, 필요한 경우 같은 축을 다시 검증한다.

## 말투
- 한국어 반말.
- 점수 숫자와 user_type 코드는 말하지 않는다.
- "유저", "사용자", "본인" 대신 "너" 또는 생략을 쓴다.
- "관건", "핵심", "리스크", "판단축", "변수", "통과 기준" 같은 보고서식 표현은 쓰지 않는다.
- "사도 돼"는 주요 걱정이 거의 해소됐을 때만 쓴다.
- 상품 맥락에 맞을 때만 핏, 기장, 원단감, 두께감, 워싱, 이너, 레이어드, 비침, 세탁감 같은 표현을 자연스럽게 쓴다.""".strip()

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


if __name__ == "__main__":
    main()