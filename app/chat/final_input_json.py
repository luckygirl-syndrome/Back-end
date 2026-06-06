# ─────────────────────────────────────────────
# 2차 호출 프롬프트: 키워드 + 베이스 점수 → 유저별 보정 점수
# ─────────────────────────────────────────────
SCORING_PROMPT_TEMPLATE = """당신은 쇼핑 마케팅 심리 전문가입니다.
아래 마케팅 키워드와 베이스 충동구매 점수만 보고, 유저의 F-BTI 유형에 맞춰 보정한 최종 personalized_score를 산출하세요.
**마케팅 키워드 목록에 있는 표현만 점수 보정에 사용할 것. 키워드 외 다른 정보(상품명·소재·색상·구성 등)는 절대 참고하지 말 것.**
JSON만 응답. 마크다운 코드블록 없이.

## 입력
- 마케팅 키워드: {marketing_keywords}
- 베이스 점수: {base_score}

## 보정 원칙
- 베이스 점수를 출발점으로, 유저 유형에 따라 키워드별 반응을 고려해 가감.
- **보정 폭 제한 (반드시 준수):**
  · 베이스 점수가 0이면 → 모든 유저 0점. 보정 불가.
  · 최대 상승폭: base_score × 0.3 (예: 베이스 15 → 최대 +4, 즉 19점 상한 / 베이스 30 → 최대 +9, 즉 39점 상한)
  · 최대 하락폭: base_score × 0.4 (예: 베이스 15 → 최대 -6, 즉 9점 하한 / 베이스 30 → 최대 -12, 즉 18점 하한)
  · 보정이 미미한 유형(키워드와 무관)은 base_score 그대로 반환.
- 키워드와 유형 간 궁합이 좋으면 상승 보정, 역효과면 하락 보정.
- 각 키워드가 해당 유형에게 왜 긍정/부정인지 맥락을 반드시 고려할 것.
  예: "문의 폭주"는 M형(차별화 지향)에게 거부감 → 하락 보정.
  예: 대규모 판매량은 M형에게 매력 감소 → 하락 보정, U형·T형에게는 신뢰 상승 → 상승 보정.
  예: MADE·MD픽은 I형·O형에게 강한 신뢰 → 상승 보정, E형에게는 중립.
  예: 핏보장·인생핏 같은 핏 보증은 어느 유형에게나 약한 긍정이나, 특정 유형 강점 키워드가 아님 → 소폭 상승 또는 중립.
- 0~100 범위 유지.

## 유저 F-BTI 유형
{user_fbti}

## 출력 형식 (JSON만)
{{"personalized_score": 0}}
"""

# ─────────────────────────────────────────────
# prompt.py 프롬프트 빌더 최종 버전
# ─────────────────────────────────────────────
FBTI_DESCRIPTIONS = {
    "U": "판매량·랭킹·연예인 착용 등 사회적 증거에 강하게 반응. 많은 사람이 검증한 상품에 신뢰를 느낌.",
    "I": "MADE·사장픽·자체제작 등 제품 자체의 가치와 전문가 보증에 반응. 판매량·랭킹보다 품질과 희소한 가치를 중시. 대중적인 것보단 자신만의 특별한 옷을 원함.",
    "T": "유행·이번 시즌 인기·많이 입는 스타일에 반응. 집단 소속감을 통해 만족.",
    "M": "희소성·독특함에 반응. 판매량이 클수록 오히려 매력이 떨어짐. 나만의 스타일을 원함.",
    "E": "품절임박·1+1·증정 등 할인·혜택에 강하게 반응. 얼마나 이득을 봤는가가 구매 만족의 핵심.",
    "O": "할인보다 물건 자체의 품질·디자인·완성도로 판단. MADE·퀄리티 보증에 반응.",
} #유형 설명 

# ─────────────────────────────────────────────
# 1차 호출 프롬프트: 이미지 → JSON 추출 + 마케팅 키워드 + 베이스 점수
# ─────────────────────────────────────────────
EXTRACT_PROMPT = """당신은 쇼핑 스크린샷에서 상품 정보를 추출하는 전문가입니다.
출력 키와 라벨은 반드시 아래 정의 그대로 사용하세요. JSON만 응답.

## 핵심 원칙

1. ## 핵심 원칙

1. **명시 vs 추론을 구분.** 추론 필드(product_style, style_match_percentage, style_match_reasoning)는 이미지·문맥을 바탕으로 판단 가능. 그 외 필드는 화면에 명시된 정보만 추출 — 추측 금지.
2. **확실하지 않으면 보수적으로** — 단, **양호/0 쪽으로 보수적**. 즉 마케팅 트리거는 0이 default, visibility는 양호가 default.
3. **숫자는 단위 제거**. 한글 단위 변환: "9.2만"→92000, "1.4천"→1400, "5K"→5000. 0과 null 구분 (실제 0이면 0, 정보 없으면 null).
4. **쿠폰가/회원가는 모두 무시.** 일반 모든 사용자에게 적용되는 메인 할인만 인식.

## 필드

1. **product_name** (string|null): 상품명 (마케팅 괄호 포함 원문)
2. **original_price** (number|null): 원가
3. **has_discount** (0|1): 메인 할인 표시(취소선 원가+할인가, 또는 할인율 빨간 강조)가 있을 때만 1. 쿠폰가만 있으면 0.
4. **discounted_price** (number|null): 메인 할인가. 메인 할인 없으면 null.
5. **discount_rate** (number|null): 할인율(%). discounted_price와 동일 기준.
6. **review_count** (number|null): 리뷰 수. 화면에 안 보이면 null.
7. **review_score** (number|null): 평점 0~5. 화면에 명시 없으면 null. (nn%가 만족한 상품이면 5점 만점으로 변환하여 소수점 두자리수까지 출력)

## 마케팅 분석
8. - **product_name에 실제로 있는 텍스트만.** 이미지의 뱃지·배너·플랫폼 UI는 product_name에 없으면 무시.
    - 추출 기준: "이 표현이 없었다면 상품이 덜 매력적으로 보였을까?" → Yes면 추출, No면 제외.
      · 추출 O: 판매량(1만장 이상 명시), 연예인/인플루언서/아이돌 실명 착용 언급, 플랫폼·전문가 보증(MADE/PICK/MD픽/사장픽 등), 희소성(REORDER/품절임박/문의폭주), 구매 욕구 직접 자극(인생핏/핏보장/미친핏 등 품질·핏 보증 표현), **사회적 승인 상황 한정(하객룩/여친룩처럼 "이 옷이면 그 자리에서 통한다"는 보증성 표현 — 단, 단순 장소·용도 태그는 제외)**
      · 추출 X: 옷 설명 일체(소재·색상·핏·실루엣·스타일·시즌·옵션·구성·컬러수·사이즈), **날짜·출고일·입고일(예: 4/1출고, 3월입고)**, 상품 코드·모델명, 이모지 단독, **상황·장소·용도 태그(휴양지룩/여행코디/캠핑룩/출근룩/데이트룩/나들이룩/피크닉룩 등 — 어디서/언제 입는지 설명하는 표현 전부)**, 일반 코디 용어(OO룩 중 판매량·연예인과 무관한 단순 스타일 제안)
    - 없으면 [].

9. **base_score** (integer, 0~100): marketing_keywords만 보고 유저 유형 무관하게 산정한 베이스 충동구매 점수.
    - **marketing_keywords가 []이면 반드시 0. 예외 없음.**
    **스코어링 기준 (키워드가 있을 때만 적용):**
    - 상황·취향 한정 키워드 단독 (하객룩, 핏보장, 미친핏 등): 10~20점
    - 판매량·랭킹 단독 / REORDER 단독: 20~30점
    - 보편 신뢰 키워드 단독 (MADE, PICK, MD픽 등): 25~35점
    - 연예인·인플루언서·아이돌 착용 단독: 35~45점
    - 판매량·랭킹 / 사람 보증 + 다른 키워드 복합: 45~60점
    - 판매량·랭킹 / 강한 키워드 3개 이상 복합: 60~75점
    - 최강 조합 (연예인 착용 + MADE + 판매량): 최대 85점
    - 키워드 과밀(5개↑)·과장 심하면 신뢰도 역전으로 낮게 조정
    - 판매량이 지나치게 크면 식상함으로 반감 고려

## 스타일 분석 및 일치도 평가 

전체 스타일 태그: 심플베이직, 캐주얼, 페미닌, 섹시글램, 힙, 스트릿, 락시크, 스포티, 빈티지, 모리걸, 러블리
회원 선호 스타일 태그: {user_styles}

10. **product_style** (string|null):
    - 상품의 디자인적 특성을 요약한 스타일 정보.
    - 반드시 콤마로만 나열하지 말고, 자연스러운 한 문장 형태로 작성한다.
    - 이미지와 상품명/옵션 텍스트에서 확인 가능한 **재질/소재감, 색상, 핏/실루엣, 기장, 디테일, 패턴, 두께감, 계절감, 무드**를 중심으로 추출.
    - 단순 카테고리명만 쓰지 말고, 실제 상품을 설명할 수 있는 디자인 특징을 자연어로 작성.
    - 가능한 경우 아래 요소를 우선순위대로 포함:
      1) 색상/톤: 아이보리, 블랙, 연청, 차콜, 파스텔톤 등
      2) 소재/소재감: 니트, 데님, 코튼, 레더, 쉬폰, 골지, 헤어리, 탄탄한 소재, 부드러운 소재감 등
      3) 핏/실루엣: 루즈핏, 슬림핏, 와이드핏, 크롭, 오버핏, A라인, H라인 등
      4) 디테일: 버튼, 지퍼, 리본, 셔링, 핀턱, 포켓, 카라, 절개, 스트랩 등
      5) 패턴/무드: 스트라이프, 플라워, 체크, 심플베이직, 캐주얼, 페미닌, 러블리, 빈티지, 스포티, 힙한 무드 등
    - 상품 이미지에서 일부 특성이 명확히 보이면 추론 가능하지만, 보이지 않는 소재명·핏·디테일을 과도하게 추측하지 않는다.
    - 상품명에 명시된 디자인 정보는 활용 가능하지만, 마케팅성 표현은 product_style에 넣지 않는다.
    - 화면에서 디자인 특성을 판단하기 어렵거나 상품 이미지/텍스트에 관련 정보가 없으면 null.

product_style을 기준으로 회원 선호 스타일 태그와 비교하여 일치도를 백분율로 평가한다.
상품 이미지는 product_style이 부족하거나 애매할 때만 보조적으로 참고한다.

[Step 1] product_style 요소 분해
- 먼저 product_style에서 스타일 판단에 필요한 요소를 내부적으로 분해한다.
- 고려 요소:
  - 색상/톤
  - 소재/소재감
  - 핏/실루엣
  - 기장
  - 디테일
  - 패턴
  - 전체 무드

[Step 2] 회원 선호 스타일 태그와 매칭 분석
- 회원 선호 스타일 태그는 {user_styles}에 포함된 태그만 기준으로 한다.
- 전체 스타일 태그 목록은 가능한 스타일 범위를 제한하기 위한 참고 정보로만 사용한다.
- 회원이 선택하지 않은 스타일 태그와 잘 맞는다는 이유로 높은 점수를 주지 않는다.
- 회원이 여러 스타일 태그를 선택한 경우, 모든 태그를 동시에 만족해야 하는 것은 아니다.
- product_style이 선택 태그 중 하나와 강하게 일치하면 높은 점수를 줄 수 있다.
- product_style이 선택 태그 여러 개와 동시에 일치하면 가장 높은 점수를 준다.
- 선택 태그 중 일부와만 일치하고 나머지와 관련이 약한 경우, 관련 없는 태그 자체를 큰 감점 요소로 보지 않는다.
- 단, product_style이 선택 태그 중 하나와는 맞더라도 다른 선택 태그와 명확히 반대되는 무드가 강하면 소폭 감점한다.
- 직접 일치, 유사 일치, 부분 일치, 불일치를 구분한다.

평가 기준:
- 직접 일치: 회원 선호 스타일 태그와 product_style의 표현이 직접적으로 겹침
  예: 회원 태그 "캐주얼" / product_style "캐주얼 무드"
- 유사 일치: 표현은 다르지만 스타일적으로 가까움
  예: 회원 태그 "페미닌" / product_style "쉬폰, 셔링, 플라워, 여리여리한 무드"
- 부분 일치: 색상, 핏, 소재 일부만 맞고 핵심 무드는 약하게 겹침
  예: 회원 태그 "심플베이직" / product_style "단정한 실루엣, 심플한 디자인"
- 불일치: 회원 선호 스타일 태그와 product_style의 핵심 무드가 다름
  예: 회원 태그 "심플베이직" / product_style "화려한 패턴, 프릴, 비비드 컬러"

[Step 3] 점수 산정
style_match_percentage는 아래 기준으로 산정한다.

[Step 3] 점수 산정
style_match_percentage는 아래 기준으로 산정한다.

- 90~100: 상품의 핵심 무드가 회원 선호 스타일과 거의 완전히 일치하고, 선택한 여러 스타일 태그와도 자연스럽게 잘 맞는 경우
- 75~89: 회원 선호 스타일 중 하나 이상과 뚜렷하게 잘 맞고, 전체적인 분위기도 크게 어긋나지 않는 경우
- 60~74: 일부 핵심 요소는 잘 맞지만, 다른 선호 스타일과는 거리가 있거나 전체 무드가 완전히 맞지는 않는 경우
- 40~59: 한두 가지 요소만 유사하고, 상품의 전체적인 스타일 방향은 회원 선호와 다소 다른 경우
- 20~39: 색상/핏/소재 등 일부만 약하게 유사하고, 핵심 무드는 대부분 다른 경우
- 0~19: 회원 선호 스타일과 거의 관련이 없거나 반대되는 경우

추가 판단 원칙:
- 회원이 여러 스타일 태그를 선택한 경우, 하나의 태그와만 맞는다고 바로 높은 점수를 주지 않는다.
- product_style이 null이거나 판단 근거가 부족하면 상품 이미지를 보조적으로 참고하되, 확실하지 않으면 보수적으로 평가한다.

[Step 4] 근거 작성
- style_match_reasoning은 product_style의 디자인 요소와 회원 선호 스타일 태그의 연결점을 중심으로 작성한다.
- style_match_reasoning은 단순히 회원 스타일 태그명과 product_style의 단어가 같다는 이유만 쓰지 않는다.
- 반드시 product_style에 포함된 구체적인 디자인 요소를 근거로 작성한다.
  예: 색상, 소재감, 핏/실루엣, 기장, 디테일, 패턴, 전체 무드
- 회원 선호 스타일 태그와 어떤 디자인 요소가 연결되는지 설명한다.
- “빈티지라고 명시되어 있어서”, “캐주얼 무드라서”, “태그와 일치해서”처럼 단어 매칭만 설명하지 않는다.
- 점수가 높은 경우에도 왜 높은지 디자인 요소 중심으로 설명한다.
- 점수가 낮거나 중간인 경우, 어떤 요소는 맞고 어떤 요소는 다른지 간단히 설명한다.
- 1~2줄로 간단하고 명확하게 작성한다.
- (예: 부드러운 니트 소재감, 루즈한 실루엣, 캐주얼 무드가 회원의 캐주얼·편안한 스타일 선호와 잘 맞습니다.)

11. **style_match_percentage**: 100점 만점 기준, 0~100 사이의 실수 백분율 % (예: 85.0)
12. **style_match_reasoning**: 1~2줄 설명. 단어 매칭이 아니라 product_style의 구체적인 디자인 요소가 회원 선호 스타일과 어떻게 맞거나 다른지 설명.

## 최종 JSON 출력 형식 (키 순서 고정, JSON만, 마크다운 코드블록 없이)

{{
  "product_name": null,
  "original_price": null,
  "has_discount": 0,
  "discounted_price": null,
  "discount_rate": null,
  "review_count": null,
  "review_score": null,
  "marketing_keywords": [],
  "base_score": 0,
  "product_style": null,
  "style_match_percentage": 0,
  "style_match_reasoning": null
}}"""

# ─────────────────────────────────────────────
# prompt 실행 코드
# ─────────────────────────────────────────────

import json
import os
import base64
import csv
from pathlib import Path
from typing import Dict, Any, List
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


class AllInputVisionProcessor:
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.project_root = self.base_dir.parent.parent
        self.images_dir = self.project_root / "images"
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set in .env")

    def get_image_media_type(self, image_path: str) -> str:
        """이미지 파일 확장자에 따른 MIME type 반환"""
        ext = Path(image_path).suffix.lower()
        media_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp"
        }
        return media_types.get(ext, "image/jpeg")

    def load_image_as_base64(self, image_path: Path) -> str:
        """이미지를 base64로 인코딩"""
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        with open(image_path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")

    def get_all_images_for_image_id(self, image_id: str) -> List[Path]:
        """image_id에 해당하는 폴더의 모든 이미지 파일 반환 (알파벳순 정렬)"""
        image_folder = self.images_dir / image_id
        if not image_folder.exists():
            raise FileNotFoundError(f"Image folder not found: {image_folder}")

        image_files = []
        for ext in ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp"]:
            image_files.extend(image_folder.glob(ext))

        return sorted(image_files)

    def extract_with_gemini_from_paths(self, image_paths: List[Path], user_styles: str, user_type: str = None) -> Dict[str, Any]:
        """이미지 경로 리스트를 받아서 Gemini API를 사용해 분석

        1차 호출: EXTRACT_PROMPT로 이미지에서 정보 추출
        2차 호출: marketing_keywords가 있으면 SCORING_PROMPT_TEMPLATE로 personalized_score 계산
        """
        try:
            if not image_paths:
                raise FileNotFoundError(f"No images provided")

            # === 1차 호출: EXTRACT_PROMPT ===
            # user_styles를 프롬프트에 반영
            prompt = EXTRACT_PROMPT.format(user_styles=user_styles)

            # 프롬프트 + 모든 이미지를 parts에 추가
            parts = [{"text": prompt}]
            for image_path in image_paths:
                image_base64 = self.load_image_as_base64(Path(image_path))
                media_type = self.get_image_media_type(str(image_path))
                parts.append({
                    "inline_data": {
                        "mime_type": media_type,
                        "data": image_base64
                    }
                })

            # Gemini API 호출
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={self.api_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [
                    {
                        "parts": parts
                    }
                ]
            }

            response = requests.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()

            result_data = response.json()
            response_text = result_data["candidates"][0]["content"]["parts"][0]["text"].strip()

            # JSON 마크다운 코드블록 제거
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            # JSON 객체 추출 (첫 { 부터 마지막 } 까지)
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            if start_idx != -1 and end_idx != -1:
                response_text = response_text[start_idx:end_idx+1]

            result = json.loads(response_text)

            # === 2차 호출: SCORING_PROMPT_TEMPLATE (marketing_keywords가 있으면) ===
            marketing_keywords = result.get('marketing_keywords', [])
            base_score = result.get('base_score', 0)

            if marketing_keywords and user_type:
                try:
                    # user_fbti 정보 구성
                    user_fbti = f"유저 F-BTI 코드: {user_type}\n"
                    for axis in user_type:
                        if axis in FBTI_DESCRIPTIONS:
                            user_fbti += f"- {axis}: {FBTI_DESCRIPTIONS[axis]}\n"

                    # SCORING_PROMPT_TEMPLATE 포매팅
                    scoring_prompt = SCORING_PROMPT_TEMPLATE.format(
                        marketing_keywords=json.dumps(marketing_keywords, ensure_ascii=False),
                        base_score=base_score,
                        user_fbti=user_fbti
                    )

                    # 2차 API 호출
                    payload = {
                        "contents": [
                            {
                                "parts": [{"text": scoring_prompt}]
                            }
                        ]
                    }

                    response = requests.post(url, json=payload, headers=headers, timeout=60)
                    response.raise_for_status()

                    result_data = response.json()
                    response_text = result_data["candidates"][0]["content"]["parts"][0]["text"].strip()

                    # JSON 마크다운 코드블록 제거
                    if "```json" in response_text:
                        response_text = response_text.split("```json")[1].split("```")[0].strip()
                    elif "```" in response_text:
                        response_text = response_text.split("```")[1].split("```")[0].strip()

                    # JSON 객체 추출
                    start_idx = response_text.find('{')
                    end_idx = response_text.rfind('}')
                    if start_idx != -1 and end_idx != -1:
                        response_text = response_text[start_idx:end_idx+1]

                    scoring_result = json.loads(response_text)
                    result['personalized_score'] = scoring_result.get('personalized_score', base_score)

                except Exception as e:
                    print(f"  [WARNING] 2차 SCORING 호출 실패: {e}")
                    result['personalized_score'] = base_score
            else:
                # marketing_keywords가 없거나 user_type이 없으면 personalized_score = base_score
                result['personalized_score'] = base_score

            return result

        except Exception as e:
            print(f"[ERROR] Gemini extraction failed: {e}")
            return None

# ─────────────────────────────────────────────
# confirmed_sentences.py 정보 가공
# "이 상품 × 이 유저" 조합에 특화된 동적 맥락이다.
# ─────────────────────────────────────────────

from typing import Optional, List

__all__ = ['build_context_sentences']

# --- 독립 함수 ---

def get_marketing_context(marketing_keywords):
    """마케팅 키워드 목록을 문장화. ai_prompt.py의 marketing_keywords 기반."""
    if not marketing_keywords:
        return ""
    return f"상품명에 '{', '.join(marketing_keywords)}' 같은 마케팅 표현이 있습니다."

# --- 연관 피쳐 묶음 함수 ---

def get_social_proof_context(review_count, rating):
    """리뷰수 + 평점 묶음 — 사회적 증거 통합"""
    parts = []

    # 리뷰수
    if review_count is not None and review_count > 0:
        parts.append(f"리뷰 수는 {review_count}개입니다.")
    else:
        parts.append("리뷰가 없어 아직 검증이 되지 않은 상품입니다.")

    # 평점 (리뷰가 있을 때만 의미 있음)
    if rating is not None and review_count is not None and review_count > 0:
        if rating >= 4.7:
            rating_desc = "매우 높습니다"
        elif rating >= 4.3:
            rating_desc = "높은 편입니다"
        elif rating >= 3.5:
            rating_desc = "평균 수준입니다"
        else:
            rating_desc = "낮은 편입니다"
        parts.append(f"평점은 {rating}점으로 {rating_desc}.")

    return " ".join(parts)

def get_price_full_context(original_price, discounted_price, discount_rate, price_feeling):
    """가격 정보 + 유저 가격 체감 묶음"""

    # 가격 정보 파트
    if discount_rate is None or discount_rate == 0:
        price_part = f"{original_price:,}원으로 할인 없이 정가 판매 중입니다."
    elif discount_rate <= 20:
        price_part = (
            f"{original_price:,}원짜리 상품이 {discount_rate}% 할인되어 "
            f"{discounted_price:,}원에 판매 중입니다. 가벼운 할인 자극이 있습니다."
        )
    elif discount_rate <= 50:
        price_part = (
            f"{original_price:,}원짜리 상품이 {discount_rate}% 할인되어 "
            f"{discounted_price:,}원에 판매 중입니다. 충동 구매 자극이 강한 구간입니다."
        )
    elif discount_rate <= 70:
        price_part = (
            f"{original_price:,}원짜리 상품이 {discount_rate}% 할인되어 "
            f"{discounted_price:,}원에 판매 중입니다. "
            f"할인율이 높은 편으로, 원가 책정 방식을 확인해보는 게 좋을 수 있습니다."
        )
    else:
        price_part = (
            f"{original_price:,}원짜리 상품이 {discount_rate}% 할인되어 "
            f"{discounted_price:,}원에 판매 중입니다. "
            f"할인율이 매우 높아 원가가 부풀려졌을 가능성이 있습니다."
        )

    # 유저 체감 파트
    feeling_mapping = {
        "저렴한 것 같아요": "유저는 이 가격이 저렴하다고 느끼고 있습니다.",
        "이 정도면 괜찮아요": "유저는 이 가격이 적당하다고 느끼고 있습니다.",
        "좀 비싸긴 한데 못 살 정도는 아니에요": "유저는 이 가격이 다소 비싸다고 느끼고 있습니다.",
        "상품은 마음에 들지만 가격이 비싸요": "유저는 이 가격이 많이 비싸다고 느끼고 있습니다.",
    }
    feeling_part = feeling_mapping.get(price_feeling, None)

    if feeling_part:
        return f"{price_part} {feeling_part}"
    return price_part

def get_interest_discovery_context(interest, discovery):
    """관심 지속도 + 발견 경로 묶음 — 관심의 맥락 통합"""
    discovery_mapping = {
        "쇼핑 앱에서 카테고리 검색 후 찾아보다 발견했어요": "직접 검색해서 찾아낸",
        "유튜버/인플루언서가 입은 것을 봤어요": "인플루언서를 통해 접한",
        "쇼핑 앱에서 랭킹이나 유저 추천을 둘러보다 발견했어요": "쇼핑 앱 추천으로 발견한",
        "인스타/틱톡/X 같은 SNS 보다가 발견했어요": "SNS를 보다가 수동적으로 노출된",
        "브랜드 계정에 신상이 추가된 걸 봤어요": "팔로우 중인 브랜드 신상으로 알게 된",
    }
    interest_mapping = {
        "오늘 처음 봤어요": "오늘 처음 본 상태입니다.",
        "2~3일 됐어요": "2~3일 전부터 눈여겨보고 있는 상태입니다.",
        "1주일 정도 됐어요": "약 1주일 전부터 관심을 두고 있는 상태입니다.",
        "2주 이상 고민했어요": "2주 이상 오래 고민하고 있는 상태입니다.",
    }

    discovery_str = discovery_mapping.get(discovery, "발견한")
    interest_str = interest_mapping.get(interest, "관심을 두고 있는 상태입니다.")

    return f"{discovery_str} 상품으로, {interest_str}"

def get_style_context(style_context, style_tag):
    """유저 스타일 태그 + 상품 스타일 유사도 맥락을 문장으로 조합."""
    parts = []

    if style_tag:
        tags = [t.strip() for t in style_tag if t and str(t).strip()]
        if tags:
            parts.append(f"이 유저는 {', '.join(tags)} 스타일을 좋아하는 유저입니다.")

    if style_context and str(style_context).strip():
        parts.append(str(style_context).strip())

    return " ".join(parts) if parts else ""

# --- 연락 이유 (상품별로 매번 달라지므로 context_sentences에 포함) ---

def get_contact_reason_context(contact_reason):
    """공통 질문 중 '저한테 어떤 이유로 연락했어요?' 응답"""
    mapping = {
        "이미 마음은 정했는데 마지막으로 한 번만 봐줘요": "유저는 이미 마음을 거의 정한 상태로, 마지막 확인을 원하고 있습니다.",
        "그냥 이 옷 어떤가 궁금해서요": "유저는 가벼운 궁금증으로 찾아왔고, 아직 구매 의향이 뚜렷하지 않습니다.",
        "오래 고민했는데 결정이 안 나서요": "유저는 오래 고민했지만 결정을 못 내리고 있어, 판단 근거 정리를 필요로 합니다.",
    }
    return mapping.get(contact_reason, "연락 이유 정보가 없습니다.")

# ─────────────────────────────────────────────
# Confirmed Sentences 추출 함수
# ─────────────────────────────────────────────

def extract_confirmed_sentences(
    # 상품 기본 정보
    original_price: int,
    discounted_price: Optional[int],
    discount_rate: Optional[int],
    # 사회적 증거
    review_count: Optional[int] = None,
    review_score: Optional[float] = None,

    # 마케팅 시그널
    marketing_keywords: Optional[List[str]] = None,

    # 공통 질문 응답
    interest: Optional[str] = None,
    discovery: Optional[str] = None,
    price_feeling: Optional[str] = None,

    # style_similarity LLM 결과
    style_context: Optional[str] = None,
    style_tags: List[str] = None,

    # 점수 정보
    impulse_score: Optional[int] = None,
    match_score: Optional[int] = None,
) -> List[str]:
    """context_sentences만 반환 (PLAN.md 최종 JSON 형식)"""
    sentences = [x for x in [
        get_style_context(style_context, style_tags),
        get_interest_discovery_context(interest, discovery) if interest and discovery else None,
        get_price_full_context(original_price, discounted_price, discount_rate, price_feeling),
        get_social_proof_context(review_count, review_score),
        get_marketing_context(marketing_keywords),
        f"이 상품이 유저에게 주는 충동 점수는 {impulse_score}점이고 유저의 취향과 일치하는 점수는 {match_score}점입니다." if impulse_score is not None and match_score is not None else None,
    ] if x]
    return sentences

# ─────────────────────────────────────────────
# fbti_builder.py fbti 설명 매핑 정보
# ─────────────────────────────────────────────

# --- 유형 상세 설명 (16개) ---
AXIS_DETAILS= {
"D": {
"role":"구매 동기 참고",
"priority":"low",
"description":"필요성보다 감정적 끌림과 즉각적 매력을 더 크게 느끼는 편이다. 단, 상품 피쳐 판단에서는 보조 정보로만 사용한다."
    },
"N": {
"role":"구매 동기 참고",
"priority":"low",
"description":"감정적 끌림보다 실제 필요성, 활용 목적, 구매 이유의 명확성을 더 중요하게 본다. 단, 상품 피쳐 판단에서는 보조 정보로만 사용한다."
    },

"U": {
"role":"확신 방식",
"priority":"high",
"description":"타인의 검증을 통해 구매 확신을 얻는다. 리뷰 수, 평점, 찜 수, 구매량, 착용 후기, 랭킹처럼 많은 사람의 선택이나 평가를 중요한 신뢰 신호로 본다."
    },
"I": {
"role":"확신 방식",
"priority":"high",
"description":"외부 평가보다 자기 기준으로 상품을 분석해 확신을 얻는다. 소재, 핏, 디테일, 마감, 활용도, 기존 옷장과의 조합처럼 제품 자체 정보를 중요하게 본다."
    },

"T": {
"role":"스타일 방향",
"priority":"high",
"description":"현재 많이 보이는 스타일, 유행 키워드, 시즌 트렌드, 대중적 착용감에 끌린다. 상품이 트렌드 흐름 안에 있는지, 지금 입었을 때 어색하지 않은지가 중요하다."
    },
"M": {
"role":"스타일 방향",
"priority":"high",
"description":"흔한 유행보다 자기 취향이 드러나는 차별화된 스타일에 끌린다. 희소성, 독특한 디테일, 덜 알려진 브랜드, 남들과 겹치지 않는 무드를 중요하게 본다."
    },

"E": {
"role":"가격 해석 방식",
"priority":"medium",
"description":"할인율, 쿠폰, 무료배송, 정가 대비 이득감처럼 거래 조건이 만족도에 영향을 준다. 단, 가격은 U/I와 T/M 판단 이후의 보조 근거로 사용한다."
    },
"O": {
"role":"가격 해석 방식",
"priority":"medium",
"description":"할인 여부보다 물건 자체의 품질, 디자인 완성도, 활용성, 오래 입을 가치를 더 중요하게 본다. 가격은 제품 가치가 납득되는지 확인하는 보조 기준이다."
    }
}

# ─────────────────────────────────────────────
# user_type 조립
# ─────────────────────────────────────────────
def build_priority_rule(user_type: str) -> str:
    """
    user_type을 기반으로 priority_rule을 생성.
    """
    user_axes = list(user_type)

    axis_info = {}
    for axis in user_axes:
        if axis in AXIS_DETAILS:
            axis_info[axis] = {
                "priority": AXIS_DETAILS[axis]["priority"],
                "role": AXIS_DETAILS[axis]["role"]
            }

    high = [f"{ax}({info['role']})" for ax, info in axis_info.items() if info['priority'] == 'high']
    medium = [f"{ax}({info['role']})" for ax, info in axis_info.items() if info['priority'] == 'medium']
    low = [f"{ax}({info['role']})" for ax, info in axis_info.items() if info['priority'] == 'low']

    rule_parts = []

    if high:
        if len(high) > 1:
            high_str = "와 ".join(high)
            rule_parts.append(f"상품 판단 시 {high_str}를 동등한 최우선으로 보고")
        else:
            rule_parts.append(f"상품 판단 시 {high[0]}를 최우선으로 보고")

    if medium:
        medium_str = ", ".join(medium)
        rule_parts.append(f"{medium_str}는 보조 기준")

    if low:
        low_str = ", ".join(low)
        rule_parts.append(f"{low_str}는 대화 톤 참고용으로만 사용한다")

    return ", ".join(rule_parts) if rule_parts else ""


def build_fbti_summary(user_type: str) -> dict:
    """
    사용자 타입 코드를 axis_summary 형식으로 변환.
    축 순서: 2번째 -> 3번째 -> 4번째 -> 1번째

    Parameters
    ----------
    user_type : 4글자 S-BTI 코드 (예: "DUTE")

    Returns
    -------
    dict
        {
            "code": "DUTE",
            "axis_summary": [
                "[role/axis] description",
                ...
            ],
            "priority_rule": "rule description",
            "tensions": ["tension description", ...]
        }
    """
    user_axes = list(user_type)
    # 2->3->4->1 순서로 정렬
    sorted_axes = [user_axes[1], user_axes[2], user_axes[3], user_axes[0]]

    axis_summary = []
    for axis in sorted_axes:
        if axis in AXIS_DETAILS:
            axis_info = AXIS_DETAILS[axis]
            role = axis_info["role"]
            description = axis_info["description"]
            summary_line = f"[{role}/{axis}] {description}"
            axis_summary.append(summary_line)

    priority_rule = build_priority_rule(user_type)

    return {
        "code": user_type,
        "axis_summary": axis_summary,
        "priority_rule": priority_rule
    }


# ─────────────────────────────────────────────
# generate_input_json.py 최종 input JSON 조립
# ─────────────────────────────────────────────
import json
import sys
from pathlib import Path
from typing import Dict, List, Any

# shared 모듈 임포트
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from shared.sbti_types import parse_sbti_flags
from shared.scoring.impulse import compute_impulse_score
from shared.scoring.match import compute_match_score
from shared.survey_questions import PRICE_REASONABLE, INTEREST_PERSISTENCE, DISCOVERY_STABILITY


def build_product_info(
    product_name: str,
    original_price: int,
    discounted_price: int = None,
    discount_rate: int = None,
    product_style: str = None,
) -> List[str]:
    """상품 정보 리스트 생성"""
    info = [f"상품명: {product_name}"]

    # 가격
    if discount_rate and discount_rate > 0:
        info.append(f"가격: {original_price:,}원 → {discounted_price:,}원 ({discount_rate}% 할인)")
    else:
        info.append(f"가격: {original_price:,}원 (정가)")

    # 상품 스타일
    if product_style:
        info.append(f"상품 스타일: {product_style}")

    return info


# ─────────────────────────────────────────────
# 사용자의 정보를 받아서 처리
# ─────────────────────────────────────────────

def process_scenario(
    image_paths: List[Path],
    user_type: str,
    style_tags: List[str],
    price_feeling: str,
    interest: str,
    discovery: str,
    outputs_dir: Path,
    processor: AllInputVisionProcessor,
) -> Dict[str, Any]:
    """이미지 경로와 사용자 정보를 입력하여 prompt.json, output.json 생성"""

    # 이미지 경로 확인
    if not image_paths:
        print(f"[ERROR] 이미지 경로가 없습니다")
        return None

    print(f"\n[*] 이미지 분석 중...", flush=True)

    # 사용자 스타일을 프롬프트에 포함
    user_styles = "|".join(style_tags) if style_tags else ""

    # Gemini API로 이미지 분석
    result = processor.extract_with_gemini_from_paths(
        image_paths=image_paths,
        user_styles=user_styles,
        user_type=user_type
    )

    if not result:
        print(f"[ERROR] Vision 추출 실패")
        return None

    print(f"[OK] Vision 추출 완료")

    # 결과 디렉토리 생성
    output_scenario_dir = outputs_dir / "custom_scenario"
    output_scenario_dir.mkdir(parents=True, exist_ok=True)

    # prompt.json 저장
    prompt_json_path = output_scenario_dir / "prompt_v1.json"
    with open(prompt_json_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[OK] {prompt_json_path} 저장 완료")

    # 점수 계산
    print(f"[*] 점수 계산 중...", flush=True)

    # Impulse Score 계산
    flags = parse_sbti_flags(user_type)

    try:
        discount_rate = result.get('discount_rate', 0)
        review_count = result.get('review_count', 0)
        review_score = result.get('review_score')
        personalized_score = result.get('personalized_score', 0)

        impulse_score = compute_impulse_score(
            discount_rate=discount_rate or 0,
            review_count=review_count or 0,
            rating=review_score,
            personalized_score=personalized_score,
            is_D=flags['is_D'],
            is_N=flags['is_N'],
            is_U=flags['is_U'],
            is_I=flags['is_I'],
            is_T=flags['is_T'],
            is_M=flags['is_M'],
            is_E=flags['is_E'],
            is_O=flags['is_O'],
            platform="default",
        )
    except Exception as e:
        print(f"  [WARNING] Impulse Score 계산 실패: {e}")
        impulse_score = 0

    # Match Score 계산
    try:
        price_reasonable = PRICE_REASONABLE.get(price_feeling, 0)
        interest_persistence = INTEREST_PERSISTENCE.get(interest, 0)
        discovery_stability = DISCOVERY_STABILITY.get(discovery, 0)

        style_match_percentage = result.get('style_match_percentage', 0)

        try:
            style_match_percentage = float(style_match_percentage) if style_match_percentage is not None else 0
        except (ValueError, TypeError):
            style_match_percentage = 0

        # 0~100 범위 제한
        style_match_percentage = max(0, min(100, style_match_percentage))

        # 100점 만점 → 35점 만점 변환
        style_match_score = round(style_match_percentage * 0.35)

        # 0~35 범위 제한
        style_match_score = max(0, min(35, style_match_score))

        match_score = compute_match_score(
            style_match_score=style_match_score,
            price_reasonable=price_reasonable,
            interest_persistence=interest_persistence,
            discovery_stability=discovery_stability,
        )
    except Exception as e:
        print(f"  [WARNING] Match Score 계산 실패: {e}")
        match_score = 0

    print(f"[OK] 점수 계산 완료 (impulse: {impulse_score}, match: {match_score})")

    # context_sentences 생성
    print(f"[*] context_sentences 생성 중...", flush=True)

    context_sentences = extract_confirmed_sentences(
        original_price=result.get('original_price', 0),
        discounted_price=result.get('discounted_price'),
        discount_rate=result.get('discount_rate'),
        review_count=result.get('review_count'),
        review_score=result.get('review_score'),
        marketing_keywords=result.get('marketing_keywords', []),
        interest=interest,
        discovery=discovery,
        price_feeling=price_feeling,
        style_context=result.get('style_match_reasoning'),
        style_tags=style_tags,
        impulse_score=impulse_score,
        match_score=match_score,
    )

    print(f"[OK] context_sentences 생성 완료")

    # 최종 output.json 생성
    final_json = {
        "product_info": build_product_info(
            product_name=result.get('product_name', ''),
            original_price=result.get('original_price', 0),
            discounted_price=result.get('discounted_price'),
            discount_rate=result.get('discount_rate'),
            product_style=result.get('product_style'),
        ),
        "confirmed_sentences": context_sentences,
        "user_type": build_fbti_summary(user_type),
    }

    # output.json 저장
    output_json_path = output_scenario_dir / "output_v1.json"
    with open(output_json_path, 'w', encoding='utf-8') as f:
        json.dump(final_json, f, ensure_ascii=False, indent=2)

    print(f"[OK] {output_json_path} 저장 완료")

    return final_json


