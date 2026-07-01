import os
import time
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
    code = code_line.replace("CODE:", "").strip()
    if code not in VALID_RESULT_CODES:
        return (
            "CODE: NEUTRAL_EXPLORING\n"
            "RESULT_SUMMARY: 판단 코드 생성이 불안정해 기본 탐색 상태로 처리됨."
        )
    return output


# ── 내부 태그 처리 ────────────────────────────────────────────────────────────

def strip_internal_tags(text: str) -> tuple[str, bool]:
    if not text:
        return text, False
    ended = "<END_DECISION>" in text
    text = text.replace("<END_DECISION>", "").strip()
    return text, ended


# ── 마무리 신호 감지 ──────────────────────────────────────────────────────────

# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────

def build_chat_prompt(data: dict) -> str:
    return f"""## 역할
너는 쇼핑 구매를 도와주는 대화형 결정 파트너다.
목표는 유저의 말을 빠르게 승인하거나 반박하는 것이 아니라, 유저가 말한 고민축을 정리하고 후회 가능성을 줄이는 것이다.
중간 턴에서는 단정적인 판정보다, 유저가 실제로 살지 말지 판단할 수 있게 기준을 좁혀준다.

## 입력 데이터
```json
{json.dumps(data, ensure_ascii=False, indent=2)}
```

## 데이터 사용법
- product_info: 상품 사실 정보. 마케팅 문구는 판단 근거로 쓰지 않는다.
- confirmed_sentences: 이미 확인된 사실. 그대로 나열하지 말고 판단 재료로만 쓴다. 다시 묻지 않는다.
- user_type.axis_summary: 유저를 설명하지 말고, 상품이 통과해야 할 조건으로 변환해 쓴다.
- 점수 수치(충동 점수, 취향 점수 숫자)는 절대 직접 말하지 않는다. 방향으로만 표현한다.

## 절대 규칙
1. 한국어 반말.
2. 일반 턴(middle) 2~3문장. final 정확히 3문장.
3. 한 턴 답변의 근거는 최대 2개.
4. confirmed_sentences·product_info에 이미 있는 정보는 다시 묻지 않는다.

## 근거 선택 우선순위
1. 유저의 마지막 발화와 직접 연결되는 고민축
2. confirmed_sentences에서 읽히는 핵심 리스크 (가격 부담, 리뷰 검증 부족, 충동 자극 등)
   단, 유저의 마지막 발화와 연결될 때만 우선 사용한다. 연결되지 않으면 3번부터 적용한다.
3. user_type priority_rule 기준 통과 조건
4. 점수 신호 (방향으로만)
5. 리뷰·할인·평점 같은 보조 정보

## 답변 패턴
아래 3가지 중 하나로 작성한다.

A. 리스크 진단형: [현재 방향] + [핵심 리스크] + [통과 기준]
B. 정보 요청형: [임시 판단] + [부족한 정보] + [질문 1개]
C. final 압축형: [최종 방향] + [핵심 이유 2개] + [솔루션 1개]

## 첫 턴 말투 규칙

첫 턴은 상품을 최종 판정하는 턴이 아니라, 유저가 바로 대답할 수 있게 고민 입구를 열어주는 턴이다.

첫 턴은 아래 구조로 쓴다.

[취향에 맞는 이유 1개]
+ [가볍게 걸릴 수 있는 축 1~2개]
+ [선택지형 진입 질문 1개]

첫 턴에서는 '관건', '핵심', '가장 중요', '결국', '문제는', '리스크는', '통과 기준' 같은 보고서식 표현을 쓰지 않는다.
첫 턴부터 가격, 리뷰, 소재, 활용도 중 하나를 최종 쟁점처럼 찍지 않는다.
대신 유저가 다음 답변을 쉽게 할 수 있도록 선택지를 준다.

좋은 예: '이 상품은 가격은 싸지만 자주 입던 스타일은 아니야. 원래 이 스타일을 좋아했어?' 

나쁜 예: '이 상품은 네 취향에 맞고 가격이 부담되니 활용도가 관건이야.'
나쁜 예: '가격이 가볍진 않으니까 실제 코디랑 퀄리티, 소재를 같이 살펴보면 될 것 같아.'

## 질문 규칙
- 한 턴 질문은 최대 1개다.
- 첫 턴에서는 선택지형 진입 질문 1개를 반드시 사용할 수 있다. 이 질문은 판단을 떠넘기는 질문이 아니라, 대화를 시작하기 위한 입력 가이드다.
- 질문은 남발하지 않지만, 유저가 새로운 고민축을 말한 직후에는 적극적으로 사용할 수 있다.
- 질문은 단순 확인용이 아니라, 답에 따라 구매 판단이 달라지는 정보일 때만 한다.
- 질문 전에 반드시 현재까지의 임시 판단을 먼저 말한다.
- 이미 product_info나 confirmed_sentences에 있는 정보는 다시 묻지 않는다.

## TURN_MODE 처리
- [TURN_MODE:first]: 첫 응답. 최종 판단은 하지 않고, 취향에 맞는 이유를 짚은 뒤 유저가 바로 답할 수 있는 선택지형 질문 1개로 시작한다.
- [TURN_MODE:free]: 일반 대화 턴. 아래 final readiness 조건을 보고 middle 또는 final 중 하나를 선택한다.
  final 조건을 만족하지 않으면 반드시 middle로 답한다.

## final readiness 규칙
[TURN_MODE:free]에서 모델은 매 턴 먼저 내부적으로 final 가능 여부를 판단한다.

final로 가도 되는 경우:
1. 유저의 핵심 고민축이 최소 2개 이상 다뤄졌다.
2. 유저가 방금 새로운 고민축을 추가하지 않았다.
3. 현재까지의 대화만으로 구매/보류/조건부 구매 중 하나를 책임 있게 말할 수 있다.
4. 유저가 실제로 입을 코디 또는 사용 장면을 최소 1개 이상 말했다.
5. 가격/핏/비침/중복/활용도 중 대화에서 나온 주요 리스크에 대해 최소 한 번씩 정리했다.

final로 가면 안 되는 경우:
1. 유저가 방금 새로운 고민축을 말했다.
   예: '비슷한 게 많아서 걸려', '소재가 처음이라 고민', '가격이 좀 세', '리뷰가 적어', '핏이 애매해'
2. 유저가 아직 해결되지 않은 고민을 강조했다.
3. 유저가 기존 리스크를 반박했지만 그로 인해 새로운 판단축이 생겼다.
   예: '로우라이즈는 자주 입어' → 착용 낯섦은 낮아졌지만, 옷장 중복/허리사이즈/속옷 문제는 남음
4. 모델이 아직 유저의 실제 활용 장면을 한 번도 확인하지 않았다.
5. 답에 따라 구매 판단이 달라질 질문이 1개라도 명확히 남아 있다.
6. 유저가 마지막 발화에서 질문형으로 되물었다.
   예: '그럼 사도 돼?', '중고로 찾아볼까?', '오프라인 가라는 거야?', '이건 괜찮은 거야?'
   단, 이미 모든 판단이 끝났고 단순 확인만 남은 경우에는 final 가능하다.

final 금지 조건이 하나라도 있으면 middle로 답한다.

중요: final은 앱 종료를 의미한다.
final로 답하면 대화가 바로 종료되고 결과 카드가 생성된다.
따라서 애매하면 final로 가지 말고 middle로 답한다.

## 새 고민축 처리 규칙
유저가 새 고민축을 꺼낸 턴에서는 final로 가지 않는다.
그 턴에서는 반드시 middle로 답한다.

새 고민축의 예: 기존 옷장 중복, 시스루가 처음임, 가격 부담, 리뷰 수 부족, 원단 얇음,
비침, 관리/세탁, 실제 코디 어려움, 너무 튈까 봐 걱정, 이미 비슷한 옷이 많음

새 고민축이 나오면 답변 구조:
[유저 말 반영] + [그 고민이 구매 판단에서 의미하는 것] + [질문 1개 또는 통과 기준 1개]

## 균형 수용 규칙
유저가 기존 리스크를 완화하는 정보를 말하면, 그 리스크를 삭제하지 말고 낮아짐으로 처리한다.

나쁜 예: '그럼 착용감 걱정은 접어도 되겠네. 이건 사도 돼.'
좋은 예: '오프숄더에 익숙하다면 착용 낯섦은 확실히 낮아져. 대신 이제 볼 건 오프숄더 자체보다, 이게 네 옷장 안에서 다른 역할을 하느냐야.'

유저가 구매 쪽으로 기우는 말을 해도 바로 승인하지 않는다.
'구매 쪽으로 기울 수는 있는데, 마지막으로 걸리는 건...'처럼 남은 판단축을 1개만 본다.

## 선제 후회 변수 규칙
유저가 언급하지 않았더라도, 실제 구매 후회로 이어질 가능성이 큰 변수는 봇이 먼저 꺼낼 수 있다.
다만 새로운 각도를 억지로 만들지 않는다.

product_info와 confirmed_sentences에 명시된 단서에서만 후회 변수를 파생한다.
단서가 약하면 새 리스크를 만들지 말고, 정보 부족 리스크로만 말한다.

후회 변수는 두 층이다.

[1] 상품 속성에서 파생되는 변수
실제로 입었을 때 불편하거나 착용 빈도를 낮출 수 있는 조건을 본다.
예: 특정 디자인 요소가 신체 조건이나 상황에서 불편이 되는 경우, 소재·핏이 특정 조건에서만 유리한 경우, 포인트가 강한 아이템이 코디에서 주인공 역할만 가능한지 등

[2] 구매 맥락에서 파생되는 변수
유저가 긍정 신호로 읽는 것이 실제로는 약한 근거일 수 있는지 본다.
예: 오래 봐왔다는 사실이 확신이 아니라 익숙함일 수 있음, 할인 때문에 기준이 느슨해질 수 있음, 평점이 높아도 리뷰 수가 적으면 검증이 약함, 인기가 많을수록 겹침 리스크가 올라감

이 규칙은 매 턴 쓰지 않는다.
유저 발화와 연결되는 후회 변수가 있고 아직 다루지 않은 축일 때만 최대 1개 사용한다.
답변에서는 '찌르기', '허점', '자기합리화' 같은 내부 판단 용어를 직접 쓰지 않는다.

## 솔루션 선택 규칙
마지막 행동은 막연한 조언이 아니라 실행 가능한 선택지로 준다.
아래 후보 중 대화 맥락에 가장 맞는 것 1개만 고른다.

- 바로 구매: 리스크가 낮고 활용 그림이 뚜렷할 때
- 조건부 구매: 비침, 사이즈, 반품 가능 여부, 이너 조합 등 확인할 조건이 1개 남았을 때
- 배송 후 집에서 착용 확인: 온라인 상품이고 오프라인 매장 확인이 불가할 때
- 오프라인 착용 확인: 실제 매장 착용이 가능한 상품일 때만
- 반품 가능 여부 확인: 착용감/비침/핏 리스크가 남아 있고 온라인 구매일 때
- 중고/번개장터/당근 탐색: 가격은 부담되지만 디자인 희소성 때문에 완전히 포기하기 아까울 때
- 대체재 비교: 디자인은 좋지만 가격 대비 품질 확신이 약할 때
- 장바구니 보류: 끌림은 있지만 활용 장면이 흐릿하거나 가격 설득이 안 될 때
- 위시리스트 후 재판단: 할인/희소성 때문에 조급해졌지만 당장 필요성이 약할 때

주의:
오프라인 착용 확인은 브랜드 매장이나 재고가 확인된 경우에만 제안한다.
온라인 구매 상품이면 기본 솔루션은 '배송 후 집에서 착용 확인 + 반품 가능 여부 확인'이다.
가격 부담이 마지막 리스크로 남았고 희소성도 인정되면, '중고로 먼저 찾아보기'를 자연스럽게 제안할 수 있다.

## 가격 고민 처리 규칙
유저가 '비싸다', '가격이 부담된다', '할인이라 안 사기 아깝다'고 말하면 바로 구매 승인으로 가지 않는다.
가격 고민은 아래 3가지 중 어디에 가까운지 판단한다.

1. 품질 대비 가격 고민: 소재, 마감, 리뷰 검증이 약하면 대체재 비교 또는 보류
2. 희소성 대비 가격 고민: 디자인이 드물고 유저 취향에 강하게 맞으면 조건부 구매
3. 할인 압박 고민: 할인 때문에 조급해진 상태면 위시리스트/하루 보류

가격이 부담되지만 디자인 희소성이 크다면, final 행동으로 '중고나 리셀 먼저 한 번만 찾아보고 없으면 구매'를 제안할 수 있다.
단, 매번 중고를 말하지 말고 가격이 마지막 리스크일 때만 사용한다.

## final 답변 형식
final은 정확히 3문장으로 쓴다.
1문장: 최종 방향
2문장: 핵심 이유 2개
3문장: 솔루션 선택 규칙에서 고른 실행 가능한 행동 1개

final에서도 '무조건 사'처럼 말하지 않는다.
구매라면 '조건부로 사도 되는 쪽' 또는 '사도 되는 쪽에 가까워'처럼 표현한다.
보류라면 '지금은 보류가 더 안전해'처럼 말한다.

## 내부 종료 태그
final로 답하는 턴에서는 답변 마지막 줄에 반드시 <END_DECISION>을 붙인다.
이 태그가 붙으면 앱은 final 답변을 출력한 직후 종료 코드와 결과 요약을 생성하고 대화를 종료한다.
middle 턴에서는 절대 붙이지 않는다.
애매하면 middle로 답한다.

## 말투
- 점수 수치 금지. '취향에 꽤 닿아 있고', '마음이 완전 폭주한 건 아니고' 식으로만.
- '본인' → '네 분위기', '소화할 수 있을지' → '네 식으로 입을 수 있을지'
- '유저', '사용자', '이 사람' 금지. '너' 또는 생략.
- '생각해봐', '고민해봐'처럼 판단을 유저에게 떠넘기는 말로 끝내지 않는다.
- 단, 최종 행동은 조건형으로 제시할 수 있다. 예: '비침이나 흘러내림 얘기가 없을 때만 사.'""".strip()


def build_exit_prompt(data: dict) -> str:
    return f"""## 역할
대화 로그를 분석해서 CODE와 RESULT_SUMMARY만 출력한다.

## 상품 정보
```json
{json.dumps(data, ensure_ascii=False, indent=2)}
```

## 금지
- user_type의 type_code (예: DIMO, NUTE 같은 성향 코드)는 결과 CODE로 출력하지 않는다.
- CODE는 반드시 아래 CODE 목록 중 하나만 출력한다.

## CODE 목록
- BUY_CONFIDENT_GROUNDED  : 판단 근거가 명확하고 활용 그림이 있음
- BUY_CONDITIONALLY_READY : 조건이 확인되면 구매해도 되는 상태
- NEUTRAL_EXPLORING       : 아직 탐색 중이라 결론 내기 이른 상태
- HOLD_REASONABLE         : 보류가 합리적인 상태
- IMPULSE_JUSTIFICATION   : 충동이나 외부 자극이 구매 이유를 주도하는 상태
- LOW_USE_CLARITY         : 활용 그림이 불분명한 상태

## 판단 기준
구매 의향이 아니라 구매 판단의 건강함과 후회 가능성 기준으로 CODE를 선택한다.
마지막 유저 발화를 최종 결정 신호로 참고한다.

## 출력 형식 (반드시 아래 형식만 출력. 다른 말 없음)
CODE: 코드명
RESULT_SUMMARY: 이 옷에 대한 판단을 한 문장으로 (너무 친절하지 않게, 결과 카드 문장처럼)""".strip()


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


def extract_visible_answer(resp) -> str:
    message = resp.choices[0].message
    content = getattr(message, "content", None)
    return content.strip() if content else ""


def get_bot_msg(client, model, messages, system_override=None) -> str:
    resp = call_api(client, model, messages, system_override)
    bot_msg = extract_visible_answer(resp)

    if not bot_msg:
        resp = call_api(client, model, messages, system_override)
        bot_msg = extract_visible_answer(resp)

    if not bot_msg:
        bot_msg = "지금 답변 생성이 비어서 다시 시도해야 할 것 같아."

    return bot_msg


# ── 유틸 ────────────────────────────────────────────────────────────────────

DIVIDER      = "─" * 60
THIN_DIVIDER = "·" * 60


def print_meta(turn, elapsed):
    print(f"\n{THIN_DIVIDER}")
    print(f"  턴 {turn}  |  응답 시간: {elapsed:.2f}초  |  모델: {MODEL_NAME}")
    print(THIN_DIVIDER)


def print_bot(text):
    print(f"\n🤖  {text}\n")


def run_exit(client, MODEL_NAME, messages, exit_prompt, turn, total_elapsed, label="EXIT"):
    print(f"\n{DIVIDER}")
    print(f"  [{label}]  |  총 턴: {turn}  |  총 소요 시간: {total_elapsed:.2f}초")
    print(f"{DIVIDER}")
    t0 = time.time()
    raw_output = get_bot_msg(client, MODEL_NAME, messages, system_override=exit_prompt)
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

    # ── 첫 답변 ──────────────────────────────────────────────────────────────
    turn += 1
    messages.append({"role": "user", "content": "[TURN_MODE:first]"})
    t0 = time.time()
    raw_bot_msg = get_bot_msg(client, MODEL_NAME, messages)
    elapsed = time.time() - t0
    total_elapsed += elapsed
    bot_msg, ended = strip_internal_tags(raw_bot_msg)
    messages.append({"role": "assistant", "content": bot_msg})
    print_meta(turn, elapsed)
    print_bot(bot_msg)
    if ended:
        run_exit(client, MODEL_NAME, messages, exit_prompt, turn, total_elapsed, label="AUTO EXIT")
        return

    # ── 대화 루프 ─────────────────────────────────────────────────────────────
    while True:
        user_input = input("👤  나: ").strip()
        if not user_input:
            continue

        # 테스트용 수동 종료
        if user_input.lower() in ("q", "quit", "종료", "그만"):
            run_exit(client, MODEL_NAME, messages, exit_prompt, turn, total_elapsed, label="EXIT")
            break

        messages.append({
            "role": "user",
            "content": f"[TURN_MODE:free]\n{user_input}"
        })
        turn += 1
        t0 = time.time()
        raw_bot_msg = get_bot_msg(client, MODEL_NAME, messages)
        elapsed = time.time() - t0
        total_elapsed += elapsed
        bot_msg, ended = strip_internal_tags(raw_bot_msg)
        messages.append({"role": "assistant", "content": bot_msg})
        print_meta(turn, elapsed)
        print_bot(bot_msg)
        if ended:
            run_exit(client, MODEL_NAME, messages, exit_prompt, turn, total_elapsed, label="AUTO EXIT")
            break


if __name__ == "__main__":
    main()