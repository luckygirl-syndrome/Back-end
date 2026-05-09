import base64
import json
import logging
import os
import re
import traceback

import google.generativeai as genai

logger = logging.getLogger(__name__)

genai.configure(api_key=os.environ.get("GOOGLE_API_KEY", ""))

SIM_COLS = [
    "sim_quality_logic", "sim_trend_hype", "sim_temptation",
    "sim_fit_anxiety", "sim_bundle", "sim_confidence"
]

EXTRACT_PROMPT = """이 이미지는 쇼핑몰 상품 페이지의 스크린샷입니다.
아래 JSON 형식으로 정보를 추출해주세요. 숫자는 단위 없이 숫자만, 없는 정보는 기본값으로 채워주세요.

{
  "platform": "musinsa 또는 zigzag 또는 ably 또는 unknown",
  "product_name": "상품명",
  "brand": "브랜드명 또는 스토어명",
  "category": "카테고리",
  "discounted_price": 0,
  "discount_rate": 0,
  "review_score": 0.0,
  "review_count": 0,
  "product_likes": 0,
  "free_shipping": 0,
  "is_direct_shipping": 0,
  "sim_quality_logic": 0,
  "sim_trend_hype": 0,
  "sim_temptation": 0,
  "sim_fit_anxiety": 0,
  "sim_bundle": 0,
  "sim_confidence": 0
}

필드 설명:
- discounted_price: 할인된 최종 가격 (숫자만, 예: 29900)
- discount_rate: 할인율 (숫자만, 예: 30)
- review_score: 별점 (예: 4.8)
- free_shipping: 무료배송이면 1
- is_direct_shipping: 빠른배송/오늘출발/도착보장/직진배송이면 1
- sim_quality_logic: 소재/퀄리티/원단 강조 문구가 있으면 1
- sim_trend_hype: 유행/대란/품절대란 키워드가 있으면 1
- sim_temptation: 자극적 홍보 문구(한정/마감/지금만)가 있으면 1
- sim_fit_anxiety: 핏/체형보정/슬림/키커보이는 문구가 있으면 1
- sim_bundle: 1+1/묶음할인/세트 할인이 있으면 1
- sim_confidence: MD추천/베스트/보증/인증 문구가 있으면 1

JSON만 반환하고 다른 텍스트는 포함하지 마세요."""


def extract_features_from_image(image_bytes: bytes) -> dict:
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")

        image_part = {
            "mime_type": "image/png",
            "data": base64.b64encode(image_bytes).decode()
        }

        response = model.generate_content([EXTRACT_PROMPT, image_part])
        text = response.text.strip()

        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if not json_match:
            return {"product_name": "Error", "details": "Gemini 응답 파싱 실패"}

        result = json.loads(json_match.group())

        return {
            "product_name": str(result.get("product_name", "Unknown")),
            "name": str(result.get("product_name", "Unknown")),
            "brand": str(result.get("brand", "Unknown")),
            "category": str(result.get("category", "Unknown")),
            "platform": str(result.get("platform", "unknown")),
            "product_img": "",
            "discounted_price": int(result.get("discounted_price") or 0),
            "discount_rate": int(result.get("discount_rate") or 0),
            "review_score": float(result.get("review_score") or 0.0),
            "review_count": int(result.get("review_count") or 0),
            "product_likes": int(result.get("product_likes") or 0),
            "free_shipping": int(result.get("free_shipping") or 0),
            "is_direct_shipping": int(result.get("is_direct_shipping") or 0),
            **{col: int(result.get(col) or 0) for col in SIM_COLS}
        }

    except Exception as e:
        logger.error(f"extract_features_from_image 에러: {e}\n{traceback.format_exc()}")
        return {"product_name": "Error", "details": str(e)}
