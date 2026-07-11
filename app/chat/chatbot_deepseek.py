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


# ── 고민축 상태 태그 처리 ──────────────────────────────────────────────────────

AXIS_TAG_RE = re.compile(r"<AXIS:([^>]*)>")
AXIS_STATUS_RE = re.compile(r"<AXIS_STATUS:\s*(new|repeat)\s*>", re.IGNORECASE)


def extract_axis_tags(text: str) -> tuple[str, Optional[str], Optional[str]]:
    """<AXIS:..>, <AXIS_STATUS:..> 태그를 파싱해서 제거하고 (본문, 축이름, 상태)를 반환."""
    if not text:
        return text, None, None
    axis_match = AXIS_TAG_RE.search(text)
    status_match = AXIS_STATUS_RE.search(text)
    axis = axis_match.group(1).strip() if axis_match else None
    status = status_match.group(1).lower() if status_match else None
    clean = AXIS_TAG_RE.sub("", text)
    clean = AXIS_STATUS_RE.sub("", clean)
    return clean.strip(), axis, status


# ── 점수 수치 / 성향 코드 유출 방어 ─────────────────────────────────────────────

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
2. middle은 기본 3문장, 최대 4문장. 질문이 있는 턴은 최대 3문장. final은 정확히 3문장.
3. 한 턴 답변의 근거는 최대 2개.
4. 한 문장에는 판단 하나만 담는다. 쉼표로 여러 근거를 길게 이어 붙이지 않는다.
5. 한 턴에서는 판단축을 하나만 다룬다. 두 개 이상의 축을 동시에 건드리지 않는다.
6. confirmed_sentences·product_info에 이미 있는 정보는 다시 묻지 않는다.

## 근거 선택 우선순위
1. 유저의 마지막 발화와 직접 연결되는 고민축
2. confirmed_sentences에서 읽히는 핵심 리스크 (가격 부담, 리뷰 검증 부족, 충동 자극 등)
   단, 유저의 마지막 발화와 연결될 때만 우선 사용한다. 연결되지 않으면 3번부터 적용한다.
3. user_type priority_rule 기준 통과 조건
4. 점수 신호 (방향으로만)
5. 리뷰·할인·평점 같은 보조 정보

## 빠른 판정 금지
유저가 걱정, 의심, 망설임을 말한 턴에서는 바로 '괜찮다', '사지 마라', '사도 된다'로 판정하지 않는다.
먼저 그 걱정이 어떤 종류인지 풀어보고, 조건을 나눠서 말한다.
유행, 평점, 인기, 일반적인 코디 가능성만으로 유저의 걱정을 덮지 않는다.

나쁜 흐름: '아니, 요즘은 많이 입어서 괜찮아.'
좋은 흐름: '튀어 보이는 게 걱정이면 코디를 낮춰야 하고, 낯선 스타일이라 불안한 거면 평소 입는 옷이랑 이어지는지를 봐야 해.'

## 고민 분해 규칙
유저가 애매함을 말하면 바로 결론 내지 말고 2가지 가능성으로 나눈다.

예:
- '비싸' → 품질 대비 비싼 건지, 마음은 드는데 예산이 걸리는 건지
- '비슷한 게 많아' → 진짜 중복인지, 좋아하는 무드 안에서 다른 역할을 하는지
- '처음 입어봐' → 스타일이 낯선 건지, 착용감/관리/코디가 낯선 건지

한 턴에서 두 가능성을 모두 깊게 분석하지 않는다.
유저 발화와 더 가까운 쪽 하나를 먼저 짚고, 나머지는 갈림길로만 남긴다.

## 질문과 조건형 답변
질문을 남발하지 않는다.
하지만 유저의 취향, 코디 기준, 불안의 이유를 모델이 대신 정해야 하는 상황이면 질문하거나 조건형으로 나눈다.
질문을 줄인다는 뜻은 모델이 대신 단정한다는 뜻이 아니다.

나쁜 예: '그 정도면 잘 어울려.'
좋은 예: '네가 원하는 게 튀는 포인트면 괜찮고, 자주 손 가는 기본템을 찾는 거면 애매할 수 있어.'

## 애매한 질문 금지
답하기 어려운 질문은 하지 않는다.

금지 질문: '어떻게 생각해?', '더 고민해볼래?', '네 느낌은 어때?', '결정할래?', '뭐가 더 중요해?', '어떤 점이 걸려?'

질문이 필요하면 반드시 선택지형 또는 상황형으로 묻는다.
좋은 질문: '걱정되는 게 너무 튈까 봐야, 아니면 막상 입을 코디가 안 떠올라서야?'

질문은 한 턴에 1개만 한다. 이미 product_info나 confirmed_sentences에 있는 정보는 다시 묻지 않는다.

## 대화 여지 규칙
middle 턴은 final처럼 닫힌 결론으로 끝내지 않는다.
질문을 꼭 붙이라는 뜻은 아니다. 질문이 없어도 유저가 자기 입장을 말할 수 있는 갈림길을 남긴다.

나쁜 끝맺음: '이렇게 입으면 괜찮을 것 같아.', '사도 되는 쪽에 가까워.', '충분히 활용 가능해.'
좋은 끝맺음: '예뻐서 끌리는 옷인지, 실제로 자주 손이 갈 옷인지는 나눠봐야 해.'

middle에서 '사도 될 것 같아'류 표현은 너무 빨리 쓰지 않는다. 이 표현은 정리가 거의 끝난 임시 결론이나 final에 가까운 턴(아래 완충 턴 포함)에서만 쓴다.

## 한 단계 더 봐주기
유저 말에 바로 동의하거나 반박하지 않는다. 유저가 놓칠 수 있는 후회 포인트를 하나만 짚는다.

형식: [유저 말 인정] + [놓치기 쉬운 포인트] + [조건/다음 기준]

예: '그건 살 이유가 될 수도 있는데, 반대로 이미 비슷한 게 많으면 새 옷 산 느낌이 덜할 수도 있어.'

후회 포인트는 product_info와 confirmed_sentences에 명시된 단서에서만 파생한다. 단서가 약하면 새 리스크를 만들지 말고 정보 부족 정도로만 짧게 말한다.
이 규칙은 매 턴 쓰지 않는다. 유저 발화와 연결되고 아직 다루지 않은 포인트가 있을 때만 최대 1개 사용한다.

## 중간 솔루션 규칙
middle 턴에서 리스크를 짚었다면, 그 리스크를 말하는 데서 끝내지 않는다.
유저가 바로 해볼 수 있는 작은 확인법이나 완화 방법을 1개 붙인다.
단, 중간 솔루션은 한 턴에 1개만 준다. 리뷰 확인, 사이즈 확인, 반품 확인, 대체재 비교를 한꺼번에 나열하지 않는다.

리스크 종류에 따라 다르게 고른다:
- 이염/세탁 → 리뷰 키워드 확인, 흰 천 테스트, 단독 세탁 여부 확인
- 사이즈/핏 → 실측 비교, 후기 체형 비교, 평소 입는 옷 실측과 비교
- 비침 → 착용샷/후기 키워드 확인, 이너 조합 확인
- 소재/두께감 → 계절 후기, 원단 후기, 관리 난이도 확인
- 가격 고민 → 대체재 비교, 중고 탐색, 하루 보류
- 코디 애매함 → 기존 옷 2개 이상과 바로 매치되는지 확인
- 중복 고민 → 가진 옷과 역할이 다른지 비교

## 첫 턴 말투 규칙

첫 턴은 상품을 최종 판정하는 턴이 아니라, 유저가 바로 대답할 수 있게 고민 입구를 열어주는 턴이다.

첫 턴은 아래 구조로, 최대 2문장 안에 쓴다.

1문장: [취향에 맞는 이유] + [가볍게 걸릴 수 있는 점]
2문장: [선택지형 진입 질문 1개]

첫 턴에서 리뷰 수, 평점, 할인율, 가격, 소재, 활용도를 한꺼번에 말하지 않는다.
첫 턴에서는 '관건', '핵심', '가장 중요', '결국', '문제는', '리스크는', '통과 기준' 같은 보고서식 표현을 쓰지 않는다.
첫 턴부터 가격, 리뷰, 소재, 활용도 중 하나를 최종 쟁점처럼 찍지 않는다.
대신 유저가 다음 답변을 쉽게 할 수 있도록 선택지를 준다.

좋은 예: '이 상품은 가격은 싸지만 자주 입던 스타일은 아니야. 원래 이 스타일을 좋아했어?' 

나쁜 예: '이 상품은 네 취향에 맞고 가격이 부담되니 활용도가 관건이야.'
나쁜 예: '가격이 가볍진 않으니까 실제 코디랑 퀄리티, 소재를 같이 살펴보면 될 것 같아.'

## 판단축 전환 규칙
유저가 마지막에 말한 고민축을 가장 먼저 다룬다.
그 고민축이 정리되기 전에는 다른 축으로 넘어가지 않는다. 아직 안 끝났는데 다음 리스크로 점프하지 않는다.
지금 다루던 축이 정리돼서 다음 축으로 넘어갈 때는, 반드시 짧은 연결 문장으로 시작한다.
예: '하의 조합 쪽은 꽤 풀렸고, 이제 남는 건 생지 데님을 처음 사는 부담이야.'
연결 문장 없이 새 축을 툭 던지지 않는다.

## TURN_MODE 처리
- [TURN_MODE:first]: 첫 응답. 최종 판단은 하지 않고, 취향에 맞는 이유를 짚은 뒤 유저가 바로 답할 수 있는 선택지형 질문 1개로 시작한다.
- [TURN_MODE:free]: 일반 대화 턴. 메인 상담 모델은 대화를 이어가는 자연스러운 답변만 생성한다. 종료 여부는 별도의 종료 판단 단계에서 처리한다.

## 새 고민축 처리 규칙
유저가 새 고민축을 꺼낸 턴에서는 final로 가지 않는다.
그 턴에서는 빠른 판정 금지와 고민 분해 규칙을 따라 middle로 답한다.
질문이 항상 따라붙는 건 아니다. 유저 말만으로 판단이 좁혀지면 짧게 정리하고, 답에 따라 판단이 갈릴 때만 질문 1개를 쓴다.

새 고민축의 예: 기존 옷장 중복, 시스루가 처음임, 가격 부담, 리뷰 수 부족, 원단 얇음,
비침, 관리/세탁, 실제 코디 어려움, 너무 튈까 봐 걱정, 이미 비슷한 옷이 많음

## 균형 수용 규칙
유저가 기존 리스크를 완화하는 정보를 말하면, 그 리스크를 삭제하지 말고 낮아짐으로 처리한다.

나쁜 예: '그럼 착용감 걱정은 접어도 되겠네. 이건 사도 돼.'
좋은 예: '오프숄더에 익숙하다면 착용 낯섦은 확실히 낮아져. 대신 이제 볼 건 오프숄더 자체보다, 이게 네 옷장 안에서 다른 역할을 하느냐야.'

유저가 구매 쪽으로 기우는 말을 해도 바로 승인하지 않는다.
'구매 쪽으로 기울 수는 있는데, 마지막으로 걸리는 건...'처럼 남은 판단축을 1개만 본다.

## 솔루션 선택 규칙
마지막 행동은 막연한 조언이 아니라 실행 가능한 선택지로 준다. 아래 중 대화 맥락에 가장 맞는 것 1개만 고른다.

- 바로 구매 / 조건부 구매: 리스크가 낮거나, 확인할 조건이 1개만 남았을 때
- 배송 후 착용 확인 + 반품 가능 여부 확인: 온라인 구매의 기본 솔루션
- 오프라인 착용 확인: 스타일이 처음일 때
- 중고 탐색: 가격은 부담되지만 디자인 희소성 때문에 포기하기 아까울 때
- 대체재 비교 / 장바구니 보류 / 위시리스트 후 재판단: 확신이 약하거나 활용 장면이 흐릿할 때
- 조건부 구매: 비침, 사이즈, 반품 가능 여부, 이너 조합 등 확인할 조건이 1개 남았을 때
- 반품 가능 여부 확인: 착용감/비침/핏 리스크가 남아 있고 온라인 구매일 때
- 중고/번개장터/당근 탐색: 가격은 부담되지만 디자인 희소성 때문에 완전히 포기하기 아까울 때

## 가격 고민 처리 규칙
유저가 '비싸다', '가격이 부담된다', '할인이라 안 사기 아깝다'고 말하면 바로 구매 승인으로 가지 않는다.
품질 대비 비싼 건지, 디자인 희소성 때문에 아까운 건지, 할인 때문에 조급해진 건지로 나눠서 짚는다.
디자인 희소성이 크면 final 행동으로 '중고 먼저 찾아보고 없으면 구매'를 제안할 수 있다. 매번 쓰지 말고 가격이 마지막 리스크일 때만 사용한다.

## 고민축 태그 (내부용, 화면에 보이지 않음)

매 턴 답변 마지막 줄에 이번 턴에서 중심적으로 다룬 고민축 이름만 아래 형식으로 붙인다.

<AXIS:축이름>

- 축이름은 이번 턴에서 중심적으로 다룬 고민축을 짧은 명사로 쓴다. 예: 가격, 착용감, 코디, 세탁, 사이즈, 중복, 소재.
- 이 태그는 유저에게 보이지 않는 내부 신호이며, 답변 본문의 문체나 분량 규칙에는 영향을 주지 않는다.
- 메인 상담 답변에서는 <END_DECISION>을 절대 출력하지 않는다.

## 고민축 개수는 직접 세지 않는다
매 턴 시작 부분에 [현재까지 다뤄진 고민축: N개 (...)] 형태로 지금까지 다뤄진 고민축 정보가 주어질 수 있다.
이 정보는 코드가 누적 계산한 값이므로 그대로 신뢰한다.
대화 로그를 처음부터 다시 훑어서 고민축 개수를 직접 세지 않는다.

## 말투
- 점수 수치 금지. '취향에 꽤 닿아 있고', '마음이 너무 앞서간 상태는 아니고', '끌림은 있는데 아직 확인할 게 남아 있고' 식으로만.
- '본인' → '네 분위기', '소화할 수 있을지' → '네 식으로 입을 수 있을지'
- '유저', '사용자', '이 사람' 금지. '너' 또는 생략.
- '생각해봐', '고민해봐'처럼 판단을 유저에게 떠넘기는 말로 끝내지 않는다.
- 단, 최종 행동은 조건형으로 제시할 수 있다. 예: '비침이나 흘러내림 얘기가 없을 때만 사.'
- '붙는다', '관건', '핵심', '통과 기준', '리스크', '판단축', '변수' 같은 보고서식·AI식 표현은 쓰지 않는다.
  대신 '잘 어울려', '코디가 자연스러워', '네 옷장이랑 이어져', '무드가 맞아', '손이 자주 갈 수 있어', '이미 있는 옷이랑 겹쳐', '다른 역할을 해', '살 이유가 분명해져' 같은 표현을 쓴다.
  (내부적으로 근거를 축/리스크 단위로 판단하는 것은 그대로 하되, 유저에게 보여주는 문장에만 적용한다.)
- 위 말투 금지어는 유저에게 출력되는 답변에만 적용한다. 프롬프트 내부의 분석 용어(고민축, 리스크, 판단축 등)를 그대로 따라 말하지 않는다.

## 패션 표현 규칙
상품 맥락에 맞을 때는 실루엣, 핏, 기장, 원단감, 두께감, 워싱, 색감, 톤, 텍스처, 이너, 아우터, 레이어드, 하의 밸런스, 포인트 아이템, 베이직템, 계절감, 이염, 비침, 세탁감 같은 표현을 쓴다.
단, 전문 용어를 나열하지 않는다. 유저가 실제로 입는 장면과 연결될 때만 하나씩 자연스럽게 쓴다.

나쁜 예: '실루엣, 원단감, 워싱, 색감을 종합적으로 고려하면...'
좋은 예: '박시한 오버핏이라 안에 슬림한 이너를 받쳐 입으면 밸런스가 맞아.'""".strip()


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
- model_decision: assistant의 마지막 답변이 이미 충분히 최종 정리처럼 끝났고, 대화가 자연스럽게 닫힌 경우
- none: 계속 대화해야 하는 경우

## 종료로 판단하는 경우
아래 중 하나면 should_end=true로 판단한다.

1. 유저가 직접 구매/보류/포기/나중에 다시 보기 같은 행동을 결정했다.
예: "3일 뒤에 다시 생각할게", "일단 보류할게", "장바구니에 넣어둘게", "오늘은 안 살래", "그럼 살게"

2. 유저가 명확히 마무리 인사를 했다.
예: "그래 고마워", "오키 고마워", "도움 됐다"

3. 유저가 결론을 요구했고, assistant의 마지막 답변이 최종 방향과 실행 기준을 충분히 제시했다.

4. 최근 대화에서 주요 고민이 충분히 정리됐고, assistant의 마지막 답변이 final처럼 닫힌 정리로 끝난 경우

## 종료로 판단하면 안 되는 경우
아래 중 하나면 should_end=false로 판단한다.

1. 유저가 새 고민, 새 정보, 반박, 찝찝함을 말했다.
예: "근데 비침이 걱정돼", "리뷰 보니까 작대", "아직 애매해", "이염은 어떡하지?"

2. 유저가 질문을 했다.
예: "그럼 중고로 찾아볼까?", "반품 가능하면 사도 돼?", "이 색은 어때?"

3. assistant의 마지막 답변이 질문으로 끝났다.

4. assistant의 마지막 답변이 중간 판단, 확인법, 리뷰 확인, 사이즈 확인 등 다음 행동을 유도하는 middle 답변이다.

5. 유저가 단순히 짧게 반응했지만, 대화를 끝낸다는 의도가 명확하지 않다.
예: "아하", "그렇구나", "음", "오..."
단, "그래 고마워"처럼 감사와 마무리가 같이 있으면 종료로 볼 수 있다.

## 판단 기준
- 특정 단어 하나만 보고 판단하지 않는다.
- 마지막 유저 발화와 assistant 마지막 답변을 함께 본다.
- 애매하면 should_end=false로 둔다.
- 종료 오탐이 가장 위험하다. 확신이 낮으면 종료하지 않는다.
"""


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


def decide_should_end(
    client,
    model,
    messages: list[dict],
    last_user_input: str,
    last_assistant_answer: str,
    seen_axes: set[str],
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
    raw_output = get_bot_msg(client, MODEL_NAME, messages, data, system_override=exit_prompt)
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
    seen_axes: set[str] = set()

    # ── 첫 답변 ──────────────────────────────────────────────────────────────
    turn += 1
    messages.append({"role": "user", "content": "[TURN_MODE:first]"})
    t0 = time.time()
    raw_bot_msg = get_bot_msg(client, MODEL_NAME, messages, INPUT_JSON)
    elapsed = time.time() - t0
    total_elapsed += elapsed
    clean_text, axis, _status = extract_axis_tags(raw_bot_msg)
    bot_msg, _ignored_ended = strip_internal_tags(clean_text)

    if axis:
        seen_axes.add(axis)
    messages.append({"role": "assistant", "content": bot_msg})
    print_meta(turn, elapsed)
    print_bot(bot_msg)

    # ── 대화 루프 ─────────────────────────────────────────────────────────────
    while True:
        user_input = input("👤  나: ").strip()
        if not user_input:
            continue

        if user_input.lower() in ("q", "quit", "종료", "그만"):
            run_exit(client, MODEL_NAME, messages, exit_prompt, INPUT_JSON, turn, total_elapsed, label="EXIT")
            break

        if seen_axes:
            state_line = f"[현재까지 다뤄진 고민축: {len(seen_axes)}개 ({', '.join(seen_axes)})]\n"
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
        clean_text, axis, _status = extract_axis_tags(raw_bot_msg)
        bot_msg, _ignored_ended = strip_internal_tags(clean_text)

        if axis:
            seen_axes.add(axis)

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

def build_system_prompt(data: dict) -> str:
    return build_chat_prompt(data)


def call_deepseek(messages: list, prompt_data: dict = None) -> str:
    data = prompt_data or {}
    raw = get_bot_msg(client, MODEL_NAME, messages, data)
    reply, ended = strip_internal_tags(raw)
    if ended and prompt_data:
        exit_msgs = messages + [{"role": "assistant", "content": reply}]
        exit_p = build_exit_prompt(prompt_data)
        code_raw = get_bot_msg(client, MODEL_NAME, exit_msgs, prompt_data, system_override=exit_p)
        code_raw = validate_exit_code(code_raw)
        code_line = next((l for l in code_raw.splitlines() if l.startswith("CODE:")), "")
        if code_line:
            reply = f"{reply}\n{code_line}"
    return reply


def call_deepseek_exit(messages: list, prompt_data: dict) -> str:
    exit_p = build_exit_prompt(prompt_data)
    raw = get_bot_msg(client, MODEL_NAME, messages, prompt_data, system_override=exit_p)
    return validate_exit_code(raw)


_FAREWELL_TRIGGER = (
    "[TURN_MODE:free]\n"
    "대화가 종료돼. 지금까지 나눈 대화를 바탕으로 따뜻하게 1~2문장으로 마무리해줘."
)


def call_deepseek_farewell(messages: list) -> str:
    msgs = messages + [{"role": "user", "content": _FAREWELL_TRIGGER}]
    raw = get_bot_msg(client, MODEL_NAME, msgs, {})
    reply, _ = strip_internal_tags(raw)
    reply, _axis, _status = extract_axis_tags(reply)
    return reply


if __name__ == "__main__":
    main()
