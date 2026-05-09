"""
app/chat/service.py

비즈니스 로직 담당.
- parse_and_save_product: 상품 파싱 + DB/Redis 저장 (Background Task, 동기)
- init_chat_session: finalize-survey에서 호출. JSON 조립 → Redis 세션 저장 → 첫 응답 생성
- handle_message: /{user_product_id}/messages/ 에서 호출. 세션 로드 → 프롬프트 조립 → 응답 생성
"""
import os
import json
import re
import traceback
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
import numpy as np

logger = logging.getLogger(__name__)

import joblib
import google.generativeai as genai
from sqlalchemy.orm import Session
from app.chat.enum import ChatStatus

def get_status_label(status: str, is_purchased: int | None) -> str:
    """ChatStatus에 따른 한글 라벨을 반환하는 공통 함수"""
    if is_purchased == 1:
        return "구매 완료"

    status_display_map = {
        ChatStatus.ANALYZING: "고민 중",
        ChatStatus.PENDING: "고민 중",
        ChatStatus.FINISHED: "고민 중",
        ChatStatus.PURCHASED: "구매 완료",
        ChatStatus.ABANDONED: "구매 포기"
    }
    return status_display_map.get(status, "고민 중")
from app.chat.models import Chat
from app.chat.schemas import ChatListItem   
from app.users.models import User
from app.products.models import Product, UserProduct
from app.products.parsers.item_parser import extract_features_from_url
from app.chat.logic.impulse_calculator import analyze_product_risk, RISK_LEVELS
from app.chat.logic.final_prefer import infer_all
from app.chat.logic.impulse_calculator import analyze_product_risk
from app.chat.logic.final_prefer import infer_all, reconstruct_profile, update_profile, load_prior_artifacts
from app.chat.logic.user_survey import determine_mode
from app.chat.logic.final_score import compute_final_score
from .constants import (
    IMPULSE_GUIDE_DATA, SURVEY_TEXT_MAPPING, DEFAULT_VALUES,
    SURVEY_SCORE_TABLE, PRIOR_TEXT, PERSONAL_POS_TEXT, PERSONAL_RISK_TEXT,
    STRATEGY_MATRIX
)
from . import repository
from . import agent

import redis

from app.core.config import settings

# ──────────────────────────────────────────────
# 동기 Redis 클라이언트 (Background Task에서 사용)
# ──────────────────────────────────────────────
redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=0,
    decode_responses=True,
)

# ML 모델 디렉토리
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRIOR_MODEL_DIR = os.path.join(BASE_DIR, "models", "artifacts_prior")
print(f"🚀 DEBUG: PRIOR_MODEL_DIR is -> {PRIOR_MODEL_DIR}")

# 챗봇 설명용 한글 매핑
FEATURE_KO = {
    'discount_rate': '할인율', 'review_count': '리뷰 수', 'review_score': '평점',
    'product_like': '찜 수', 'is_direct_shipping': '배송 정보', 'free_shipping': '무료 배송',
    'sim_trend_hype': '유행/대란 키워드', 'sim_temptation': '자극적 홍보 문구',
    'sim_fit_anxiety': '핏/체형 보정 문구', 'sim_quality_logic': '소재/퀄리티 강조',
    'sim_bundle': '1+1/묶음 할인', 'sim_confidence': 'MD추천/보증'
}


# ──────────────────────────────────────────────
# 1. 상품 파싱 + 저장 (Background Task — 동기)
# ──────────────────────────────────────────────
# 🚩 [추가] 유저 프로필 관리 헬퍼
def load_user_profile(user: User):
    """User DB의 mu_like, mu_regret 등 컬럼 정보를 가져와 final_prefer용 프로필로 복원"""
    # 0. 필요한 전적 아티팩트(scaler 등) 로드
    _, scaler_cont, meta, _ = load_prior_artifacts(PRIOR_MODEL_DIR)
    
    # 1. 아티팩트에서 mean/std Serise 형태로 준비 (final_prefer 가 요구하는 형식)
    import pandas as pd
    # index에서 delta_ 접두어를 떼어줌 (final_prefer의 PERSONAL_SCALE_COLS 매칭용)
    new_index = [c.replace("delta_", "") for c in meta["SCALE_COLS"]]
    s_mean = pd.Series(scaler_cont.mean_, index=new_index)
    s_std = pd.Series(np.sqrt(scaler_cont.var_) + 1e-9, index=new_index)

    return reconstruct_profile(
        mu_like_str=user.mu_like,
        mu_regret_str=user.mu_regret,
        n_pos=user.n_pos or 0,
        n_neg=user.n_neg or 0,
        scaler_mean=s_mean,
        scaler_std=s_std
    )

def save_user_profile(db: Session, user: User, profile: dict):
    """업데이트된 프로필 정보를 User DB 컬럼에 영구 저장"""
    # numpy array -> list -> json string 변환
    user.mu_like = json.dumps(profile["mu_like"].tolist())
    user.mu_regret = json.dumps(profile["mu_regret"].tolist())
    user.n_pos = int(profile["n_pos"])
    user.n_neg = int(profile["n_neg"])
    db.commit()

def clean_persona_code(user):
    """프로젝트 표준 순서(1:D/N, 2:S/A, 3:M/T)에 맞춰 페르소나 코드 정렬"""
    raw = getattr(user, 'persona_type', "DSM")
    if isinstance(raw, dict):
        raw = raw.get('persona_type', 'DSM')
    elif isinstance(raw, str) and raw.startswith('{'):
        try:
            data = json.loads(raw)
            raw = data['persona'].get('persona_type', 'DSM') if isinstance(data.get('persona'), dict) else data.get('persona_type', 'DSM')
        except:
            raw = "DSM"

    code = str(raw).replace("-", "").replace(" ", "").upper()
    axis1 = {'D', 'N'}
    axis2 = {'S', 'A'}
    axis3 = {'M', 'T'}
    res = ["", "", ""]

    for char in code:
        if char in axis1: res[0] = char
        elif char in axis2: res[1] = char
        elif char in axis3: res[2] = char

    if all(res):
        return "".join(res)
    return code


def parse_and_save_product(db: Session, url: str, user: User, user_product_id: int = None):
    """Background Task: URL 파싱 → 분석 → DB 저장 → Redis 캐시."""
    try:
        user_persona_code = clean_persona_code(user)
        model_ready_code = f"{user_persona_code[0]}-{user_persona_code[1]}-{user_persona_code[2]}"

        result = extract_features_from_url(url)
        if not result or result.get("product_name") == "Error":
            return None

        # 분석
        risk_res = analyze_product_risk(result, model_ready_code)
        
        # 🚩 [추가] DB에서 유저 프로필 로드하여 실시간 개인화 반영
        profile = load_user_profile(user)
        pref_out = infer_all(
            item_json=result, 
            persona_type=model_ready_code, 
            prior_dir=PRIOR_MODEL_DIR,
            profile=profile
        )
        impulse_score = int(risk_res.get('total_score', 0))
        total_pref_score = int(pref_out['total_score'])

        # DB 저장 (상품 조회/생성)
        product = db.query(Product).filter(Product.product_name == result['product_name']).first()
        if not product:
            product = Product(
                product_img=result.get('product_img', ''),
                product_name=result.get('product_name', 'Unknown'),
                platform=result.get('platform', 'Unknown'),
                category=result.get('category', '기타'),
                free_shipping=bool(result.get('free_shipping', 0)),
                price=int(result.get('discounted_price', 0)),
                discount_rate=float(result.get('discount_rate', 0)),
                is_direct_shipping=bool(result.get('is_direct_shipping', 0)),
                review_count=int(result.get('review_count', 0)),
                review_score=float(result.get('review_score', 0.0)),
                product_likes=str(result.get('product_likes', '0')),
                product_url=url,
                **{col: result.get(col, 0) for col in [
                    "sim_temptation", "sim_trend_hype", "sim_fit_anxiety",
                    "sim_quality_logic", "sim_bundle", "sim_confidence"
                ]}
            )
            db.add(product)
            db.flush()
        else:
            # 기존 상품이어도 URL은 최신 입력 링크로 갱신 (shop 아이콘 링크용)
            product.product_url = url

        # 유저-상품 매핑 갱신 (또는 저장)
        if user_product_id:
            user_prod = db.query(UserProduct).filter(UserProduct.user_product_id == user_product_id).first()
            if user_prod:
                user_prod.product_id = product.product_id
                user_prod.impulse_score = impulse_score
                user_prod.preference_score = total_pref_score
                user_prod.status = "PENDING"
                user_prod.is_purchased = 0
                db.commit()
            else:
                user_prod = UserProduct(
                    user_id=user.user_id, 
                    product_id=product.product_id,
                    user_type=user_persona_code, 
                    impulse_score=impulse_score,
                    status="PENDING",
                    preference_score=total_pref_score,
                    is_purchased=0
                )
                db.add(user_prod)
                db.commit()
        else:
            user_prod = UserProduct(
                user_id=user.user_id, 
                product_id=product.product_id,
                user_type=user_persona_code, 
                impulse_score=impulse_score,
                status="PENDING",
                preference_score=total_pref_score,
                is_purchased=0
            )
            db.add(user_prod)
            db.commit()


        # 실제 수치 데이터
        feature_values = {
            "discount_rate": result.get('discount_rate', 0),
            "review_score": result.get('review_score', 0),
            "review_count": result.get('review_count', 0),
            "product_likes": result.get('product_likes', 0),
            "price": result.get('discounted_price', 0),
            "free_shipping": result.get('free_shipping', 0)
        }

        # 캐시용 데이터 조립
        details = {
            "top_2_causes": risk_res.get('top_2_causes', []),
            "prior_reasons": pref_out.get('prior_reason_top2', []),
            "personal_reasons": pref_out.get('personal_reason_top2', []),
            "prior_score": pref_out.get('prior_score', 0),
            "personal_score": pref_out.get('personal_score', 0),
            "personal_reason_type": pref_out.get('personal_reason_type', 'neutral'),
            "feature_values": feature_values
        }

        # ✅ Redis 저장 (신규 키 네이밍: chat:{user_product_id}:item_json)
        user_product_id = user_prod.user_product_id
        cache_key = f"chat:{user_product_id}:item_json"
        redis_client.setex(cache_key, 86400, json.dumps(details, ensure_ascii=False))
        print(f"✅ [service] 상품 분석 캐시 저장: {cache_key}")

        # 터미널 리포트
        prompt_data = {
            "user_context": {"persona_type": user_persona_code, "target_style": getattr(user, 'chu_gu_me', '심플'), "n_effective": pref_out["n_effective"]},
            "analysis_result": {"total_prefer_score": total_pref_score, "impulse_score": impulse_score, "alpha_value": pref_out["alpha"]},
            "prior_analysis": {"score": pref_out["prior_score"], "top_reasons": [{"feature": r[0], "value": feature_values.get(r[0])} for r in pref_out["prior_reason_top2"]]},
            "personal_analysis": {
                "score": pref_out["personal_score"], 
                "reason_type": pref_out["personal_reason_type"], 
                "n_effective": pref_out["n_effective"],
                "top_reasons": [{"feature": r[0], "value": feature_values.get(r[0])} for r in pref_out["personal_reason_top2"]]
            },
            "impulse_block": {"score": impulse_score, "label": risk_res["risk_label"], "level": risk_res["risk_level"], "top_causes": [{"name": c["feature_name"], "value": feature_values.get(c["feature_key"]), "detail": c["detail"]} for c in risk_res["top_2_causes"]]}
        }
        print_analysis_report(user.user_id, user_persona_code, product.product_name, pref_out, risk_res, prompt_data)

        return prompt_data

    except Exception as e:
        db.rollback()
        print(f"❌ 에러 발생:\n{traceback.format_exc()}")
        return None


def print_analysis_report(user_id, persona, p_name, pref, risk, prompt_data):
    """터미널에 분석 결과와 프롬프트 주입 데이터를 통째로 출력"""
    print("\n" + "=" * 80)
    print(f" 🚀 [SYSTEM REPORT] USER: {user_id} | PERSONA: {persona}")
    print(f" 📦 PRODUCT: {p_name}")
    print("=" * 80)
    print(f" [SUMMARY]")
    print(f" - Risk Score  : {risk['total_score']}점 ({risk['risk_label']})")
    print(f" - Pref. Score : {pref['total_score']}점 (Alpha: {pref['alpha']:.2f})")
    print("-" * 80)
    print(f" 📝 [PROMPT INJECTION DATA]")
    print(json.dumps(prompt_data, indent=4, ensure_ascii=False))
    print("-" * 80)
# ──────────────────────────────────────────────
# 2. 세션 초기화 (finalize-survey에서 호출)
# ──────────────────────────────────────────────
async def init_chat_session(
    db: Session,
    user_id: int,
    product_id: int,
    user_product_id: int,
    user_answers: dict,
) -> str:
    """
    설문 완료 후 호출. 
    1) 분석 데이터 + 설문 결과로 완전체 JSON 조립
    2) Redis에 ctx_fixed 저장
    3) 첫 Gemini 호출 → 첫 응답 반환
    """
    # DB에 저장된 분석 결과 조회
    record = db.query(UserProduct).filter(
        UserProduct.user_product_id == user_product_id
    ).first()
    product = db.query(Product).filter(Product.product_id == product_id).first()
    user = db.query(User).filter(User.user_id == user_id).first()

    if not record or not product or not user:
        return FIRST_REPLY_ERROR_MSG

    # ── Redis에서 분석 상세 데이터 가져오기 ──
    cache_key = f"chat:{user_product_id}:item_json"
    cached_raw = redis_client.get(cache_key)

    if not cached_raw:
        print(f"⚠️ 캐시 만료됨: {cache_key}")
        details = {
            "top_2_causes": [],
            "prior_reasons": [],
            "personal_reasons": [],
            "prior_score": 0,
            "personal_score": 0,
            "feature_values": {}
        }
    else:
        details = json.loads(cached_raw)

    # 2. 모드 결정 및 가이드 데이터 추출
    mode = determine_mode([
        {"q_id": 1, "answer_id": user_answers.get('q1', 1)},
        {"q_id": 2, "answer_id": user_answers.get('q2', 1)},
        {"q_id": 3, "answer_id": user_answers.get('q3', 1)}
    ])
    
    impulse_score = record.impulse_score if record.impulse_score is not None else 0
    risk_stage_info = next((lvl for lvl in RISK_LEVELS if lvl[0] <= impulse_score <= lvl[1]), RISK_LEVELS[-1])
    level_num = risk_stage_info[3]
    
    strategy_info = STRATEGY_MATRIX.get(mode, STRATEGY_MATRIX["DECIDER"]).get(level_num, STRATEGY_MATRIX["DECIDER"][1])
    guide_info = IMPULSE_GUIDE_DATA.get(mode, IMPULSE_GUIDE_DATA["DECIDER"])

    persona_suffix = record.user_type[-1].lower()
    persona_prefix = "default_" if persona_suffix == 't' else "myway_"
    
    p_type = details.get('personal_reason_type', 'positive')
    personal_text_dict = PERSONAL_RISK_TEXT if p_type == 'risk' else PERSONAL_POS_TEXT

    # ── 완전체 JSON 조립 ──
    final_input_json = {
        "meta": {
            "trace_id": str(re.sub(r'[^a-zA-Z0-9]', '', str(datetime.now().timestamp()))),
            "timestamp": datetime.now().isoformat()
        },
        "user_context": {
            "user_id": user_id,
            "persona_type": record.user_type,
            "frequent_malls": json.loads(user.favorite_shops) if user.favorite_shops and user.favorite_shops.startswith("[") else ([user.favorite_shops] if user.favorite_shops else []),
            "target_style": getattr(user, 'chu_gu_me', '심플')
        },
        "product_context": {
            "name": product.product_name,
            "brand": product.brand if hasattr(product, 'brand') else product.platform,
            "mall": product.platform,
            "price": product.price,
            "category": product.category
        },
        "mode_block": {"current_mode": mode},
        "impulse_block": {
            "impulse_score": record.impulse_score,
            "impulse_reason_top2": [
                {
                    "feature_key": cause["feature_key"],
                    "value": details.get('feature_values', {}).get(cause["feature_key"]),
                    "guide": guide_info["features"].get(
                        f"review_count_{persona_suffix}" if cause["feature_key"] == "review_count" else cause["feature_key"],
                        "이 부분 주의깊게 봐!"
                    )
                } for cause in details.get('top_2_causes', [])
            ]
        },
        "preference_block": {
            "total_score": record.preference_score,
            "mixing": {"preference_priority": DEFAULT_VALUES["preference_priority"]},
            "prior_score": details.get('prior_score', 0),
            "prior_reason_top2": [
                {
                    "feature_key": r[0],
                    "value": details.get('feature_values', {}).get(r[0]),
                    "guide": PRIOR_TEXT.get(
                        f"{persona_prefix}review_count" if r[0] == "review_count" else r[0],
                        f"너랑 비슷한 유형은 '{FEATURE_KO.get(r[0], r[0])}' 조건이 만족스러우면 고민이 줄어드는 편이야."
                    )
                } for r in details.get('prior_reasons', [])
            ],
            "personal_score": details.get('personal_score', 0),
            "personal_reason_top2": [
                {
                    "feature_key": r[0],
                    "value": details.get('feature_values', {}).get(r[0]),
                    "guide": personal_text_dict.get(
                        f"{persona_prefix}{r[0]}" if r[0] in ["review_count", "product_likes"] else r[0],
                        f"이 옷의 '{FEATURE_KO.get(r[0], r[0])}' 조건은 네 평소 스타일이랑 조금 다를 수 있어."
                    )
                } for r in details.get('personal_reasons', [])
            ]
        },
        "conversation_block": {
            "cart_duration": SURVEY_TEXT_MAPPING["q1"].get(user_answers.get('q1'), "방금 전"),
            "contact_reason": SURVEY_TEXT_MAPPING["q2"].get(user_answers.get('q2'), "궁금해서"),
            "purchase_certainty": SURVEY_TEXT_MAPPING["q3"].get(user_answers.get('q3'), "확신 없음"),
            "key_appeal": SURVEY_TEXT_MAPPING["qc"].get(user_answers.get('qc'), "디자인")
        },
        "strategy_matrix": {
            "level": level_num,
            "label": strategy_info.get("label", ""),
            "goal": strategy_info.get("goal", ""),
            "rationale": strategy_info.get("rationale", ""),
            "stance": strategy_info.get("stance", ""),
            "tone": strategy_info.get("tone", ""),
            "strategy": strategy_info.get("strategy", "")
        }
    }

    # ── Redis에 ctx_fixed 저장 (비동기) ──
    await repository.save_ctx_fixed(user_product_id, final_input_json)
    logger.info(f"✅ [service] ctx_fixed 저장 완료: chat:{user_product_id}:ctx_fixed")

    # ── DB에도 백업 ──
    record.prompt_data = json.dumps(final_input_json, ensure_ascii=False)
    db.commit()

    # ── 로그 ──
    logger.debug(f"🚀 LLM INPUT JSON (Trace: {final_input_json['meta']['trace_id']}):\n{json.dumps(final_input_json, indent=4, ensure_ascii=False)}")

    # ── 첫 Gemini 호출 ──
    first_input = ""
    clean_text, next_step, is_held, decision_code = await agent.generate_response(
        json_data=final_input_json,
        current_step=1,
        current_turn=1,
        user_input=first_input,
        history=[],
    )

    # ── 히스토리에 첫 턴 저장 ──
    await repository.push_history(user_product_id, "user", first_input)
    await repository.push_history(user_product_id, "assistant", clean_text)

    if decision_code:
        logger.info(f"🎯 [DECISION CODE]: {decision_code}")

    return clean_text


# ──────────────────────────────────────────────
# 3. 채팅 메시지 처리 (/{user_product_id}/messages/)
# ──────────────────────────────────────────────
async def handle_message(
    db: Session,
    user_id: int,
    user_product_id: int,
    user_input: str,
) -> dict:
    """
    유저 메시지 수신 → 세션 로드 → 프롬프트 조립 → Gemini 호출 → 히스토리 저장.
    DB/Redis(chat_messages)에도 저장해 GET /room 시 전체 로그가 유지되도록 함.
    Returns: {"message": str, "is_exit": bool, "decision_code": str|None}
    """
    # ── 세션 데이터 로드 (MGET) ──
    _, ctx_fixed = await repository.get_session_data(user_product_id)

    if not ctx_fixed:
        # Redis 만료 시 DB에서 복구
        record = db.query(UserProduct).filter(
            UserProduct.user_product_id == user_product_id
        ).first()
        if record and record.prompt_data:
            ctx_fixed = json.loads(record.prompt_data)
            await repository.save_ctx_fixed(user_product_id, ctx_fixed)
            logger.info(f"🔄 [service] DB에서 ctx_fixed 복구: chat:{user_product_id}")
        else:
            return {
                "message": "세션 데이터를 찾을 수 없어. 처음부터 다시 시작해줄래? 🧐",
                "is_exit": False,
                "decision_code": None,
            }

    # ── 히스토리 로드 ──
    history = await repository.get_history(user_product_id)

    # ── 현재 step/turn 계산 ──
    user_msgs = [m for m in history if m.get("role") == "user"]
    current_turn = len(user_msgs) + 1  # 이번 턴은 +1

    # step은 기본 1로 시작. 히스토리에 [STEP_MOVED:2] 같은 이벤트가 있다면 별도 트래킹 필요
    # MVP에서는 ctx_fixed에 저장하거나, 마지막 assistant 메시지 분석으로 판별
    current_step = 1  # 기본값

    # ── Gemini 호출 ──
    clean_text, next_step, is_held, decision_code = await agent.generate_response(
        json_data=ctx_fixed,
        current_step=current_step,
        current_turn=current_turn,
        user_input=user_input,
        history=history,
    )

    # ── 히스토리 저장 (비동기) ──
    await repository.push_history(user_product_id, "user", user_input)
    await repository.push_history(user_product_id, "assistant", clean_text)

    # ── GET /room에서 읽는 chat_messages Redis + DB에도 저장 (채팅 로그 유지) ──
    save_chat_message(db, user_id, user_product_id, "user", user_input)
    save_chat_message(db, user_id, user_product_id, "assistant", clean_text)

    is_exit = (next_step == "EXIT") or (next_step == 2)
    if not is_exit and clean_text and "또바바의 쇼핑 진단" in clean_text:
        is_exit = True
        logger.info("🏁 [service] LLM이 [EXIT] 없이 쇼핑 진단 문구 반환 → exit 처리")
    if decision_code:
        logger.info(f"🎯 [DECISION CODE]: {decision_code}")

    final_score_val = None
    if is_exit and decision_code:
        try:
            impulse_score = ctx_fixed.get("impulse_block", {}).get("impulse_score", 0)
            preference_score = ctx_fixed.get("preference_block", {}).get("total_score", 0)
            mode = ctx_fixed.get("mode_block", {}).get("current_mode", "BRAKE")
            
            final_score_val = compute_final_score(
                impulse_score=impulse_score, 
                preference_score=preference_score, 
                attitude_code=decision_code, 
                mode=mode
            )
            
            # Update DB with the final score
            record = db.query(UserProduct).filter(
                UserProduct.user_product_id == user_product_id
            ).first()
            if record:
                record.final_score = final_score_val
                db.commit()
                
            logger.info(f"🏁 [service] 대화 종료 감지 (user_product_id={user_product_id}), final_score={final_score_val}")
        except Exception as e:
            logger.error(f"Error calculating final_score: {e}")

    return {
        "user_product_id": user_product_id,
        "message": clean_text,
        "is_exit": is_exit,
        "final_score": final_score_val
    }


# ──────────────────────────────────────────────
# 설문 텍스트 변환 헬퍼 (기존 유지)
# ──────────────────────────────────────────────
def get_q1_text(val: Optional[int]):
    mapping = {1: "방금 담았어 / 1시간 이내", 2: "1~2일 이내", 3: "일주일 이내", 4: "일주일 이상", 5: "한달 이상"}
    return mapping.get(val, "방금 전")


def get_q2_text(val: Optional[int]):
    mapping = {1: "사도 되는지 확인받고 싶어서", 2: "그냥 이 옷 어떤가 궁금해서", 3: "오래 고민했는데 결정이 안 나서", 4: "사고나서 후회할까봐 걱정돼서"}
    return mapping.get(val, "궁금해서")


def get_q3_text(val: Optional[int]):
    mapping = {1: "사고싶긴 한데 비슷한 옷들이 많아서 고민이 돼", 2: "장바구니 옷 중에 이게 제일 마음에 들어", 3: "이미 코디까지 다 생각해 둬서 사면 잘 입을 것 같아", 4: "다 좋은데.. 혹시 더 나은 게 있을까봐 불안해"}
    return mapping.get(val, "확신 없음")


def get_qc_text(val: Optional[int]):
    mapping = {
        1: "가성비",
        2: "시즌오프 세일 / 품절 임박",
        3: "요즘 유행템 / 연예인 착용",
        4: "소재 및 퀄리티",
        5: "MD/인플루언서 픽",
        6: "모델의 착용 핏",
        7: "빠른 배송 필요"
    }
    return mapping.get(val, "디자인")

import re

def parse_llm_response(text: str):
    """
    LLM 응답에서 [STEP_XXX: N] 등 대괄호로 된 모든 태그를 분리하고 본문만 남김
    """
    # 1. 숫자 단계 추출 (STEP_MOVED나 STEP_HELD 뒤의 숫자 찾기)
    step_match = re.search(r"\[STEP_(?:MOVED|HELD):\s*(\d+)\]", text)
    next_step = int(step_match.group(1)) if step_match else 1 # 기본값 1
    
    # 2. 보류 여부 추출
    is_held = "[STEP_HELD" in text or "[IS_HELD: TRUE]" in text
    
    # 3. 🔥 대괄호([])로 감싸진 모든 시스템 태그 삭제
    # 이 정규식은 [내용] 형태를 찾아서 통째로 지워줘.
    clean_msg = re.sub(r"\[.*?\]", "", text)
    clean_msg = clean_msg.strip()
    
    return clean_msg, next_step, is_held

def get_time_display(dt: datetime) -> str:
    if not dt: return "알 수 없음"
    now = datetime.now()
    diff = now - dt
    if diff.days < 0: return "오늘"  # 서버/클라이언트 시차로 미래 시각이면 오늘로
    if diff.days == 0: return "오늘"
    if diff.days == 1: return "어제"
    return f"{diff.days}일 전"

def get_user_chat_list(db: Session, user_id: int):
    # 1. 각 product_id별로 가장 최신(updated_at 기준)의 user_product_id를 찾는 서브쿼리
    from sqlalchemy import func
    
    subquery = (
        db.query(
            UserProduct.product_id,
            func.max(UserProduct.updated_at).label("max_updated_at")
        )
        .filter(UserProduct.user_id == user_id)
        .group_by(UserProduct.product_id)
        .subquery()
    )

    # 2. 서브쿼리와 조인하여 각 상품별 최신 채팅방 레코드만 가져오기
    results = (
        db.query(UserProduct, Product)
        .join(Product, UserProduct.product_id == Product.product_id)
        .join(
            subquery,
            (UserProduct.product_id == subquery.c.product_id) &
            (UserProduct.updated_at == subquery.c.max_updated_at)
        )
        .filter(UserProduct.user_id == user_id)
        .order_by(UserProduct.updated_at.desc(), UserProduct.user_product_id.desc())
        .all()
    )

    if not results:
        return {"latest_chat": None, "all_chats": []}

    chat_items = []
    for user_prod, prod in results:
        item = ChatListItem(
            user_product_id=user_prod.user_product_id,
            product_name=prod.product_name,
            product_img=prod.product_img,
            price=prod.price,
            last_chat_time=get_time_display(user_prod.updated_at),
            status_label=get_status_label(user_prod.status, user_prod.is_purchased),
            is_purchased=user_prod.is_purchased
        )
        chat_items.append(item)

    return {
        "latest_chat": chat_items[0], 
        "all_chats": chat_items      
    }

def save_chat_message(db: Session, user_id: int, user_product_id: int, role: str, content: str):
    """DB와 Redis 양쪽에 채팅 메시지를 저장함"""
    # 1. DB 저장
    new_chat = Chat(
        user_id=user_id,
        user_product_id=user_product_id,
        role=role,
        content=content,
        created_at=datetime.now()
    )
    db.add(new_chat)
    db.commit()
    db.refresh(new_chat)

    # 2. Redis 저장 (리스트 형태: chat_messages:{user_product_id})
    cache_key = f"chat_messages:{user_product_id}"
    msg_data = {
        "role": role,
        "content": content,
        # ChatRoomDetailResponse.ChatMessageResponse 에서 `message` 필드를 요구하므로
        # 대화 기록에서는 content와 동일하게 채워준다.
        "message": content,
        "created_at": new_chat.created_at.isoformat(),
    }
    
    # 리스트 끝에 추가하고, 1시간(3600초) 만료 설정
    redis_client.rpush(cache_key, json.dumps(msg_data, ensure_ascii=False))
    redis_client.expire(cache_key, 3600)
    
    return new_chat

def get_chat_messages(db: Session, user_product_id: int, user_id: int):
    # 1. 상단에 보여줄 상품 정보 가져오기 (이건 DB에서 가져옴)
    result = (
        db.query(UserProduct, Product)
        .join(Product, UserProduct.product_id == Product.product_id)
        .filter(UserProduct.user_product_id == user_product_id)
        .filter(UserProduct.user_id == user_id)
        .first()
    )

    if not result:
        return None

    user_prod, prod = result

    # 2. Redis에서 메시지 목록 먼저 확인
    cache_key = f"chat_messages:{user_product_id}"
    cached_msgs = redis_client.lrange(cache_key, 0, -1)

    if cached_msgs:
        print(f"✅ Redis에서 채팅 메시지 {len(cached_msgs)}개를 불러왔어! (Key: {cache_key})")
        # 과거 캐시 데이터에는 `message` 키가 없을 수 있으므로 content로 채워준다.
        messages = []
        for m in cached_msgs:
            obj = json.loads(m)
            if "message" not in obj:
                obj["message"] = obj.get("content", "")
            messages.append(obj)
    else:
        # 3. Redis에 없으면 DB에서 가져오기
        print(f"⚠️ Redis에 메시지가 없어 DB에서 가져오는 중... (Key: {cache_key})")
        db_messages = (
            db.query(Chat)
            .filter(Chat.user_product_id == user_product_id)
            .order_by(Chat.created_at.asc())
            .all()
        )
        
        messages = []
        for m in db_messages:
            msg_dict = {
                "role": m.role,
                "content": m.content,
                "message": m.content,
                "created_at": m.created_at.isoformat(),
            }
            messages.append(msg_dict)
            # 가져온 김에 Redis에도 채워두기
            redis_client.rpush(cache_key, json.dumps(msg_dict, ensure_ascii=False))
        
        if messages:
            redis_client.expire(cache_key, 3600)

    messages = _deduplicate_first_reply_block(messages)

    platform = getattr(prod, "platform", None) or ""
    product_url = getattr(prod, "product_url", None) or ""
    return {
        "user_product_id": user_prod.user_product_id,
        "product_name": prod.product_name or "",
        "product_img": prod.product_img,
        "price": prod.price,
        "platform": platform,
        "product_url": product_url,
        "status_label": get_status_label(user_prod.status, user_prod.is_purchased),
        "status": user_prod.status or "",  # ANALYZING, PENDING, FINISHED 등 (종료 배너 표시용)
        "final_score": getattr(user_prod, "final_score", None),
        "messages": messages
    }

def finish_chat(db: Session, user_product_id: int, user_id: int):
    """채팅방을 종료 상태(FINISHED)로 변경"""
    record = (
        db.query(UserProduct)
        .filter(UserProduct.user_product_id == user_product_id)
        .filter(UserProduct.user_id == user_id)
        .first()
    )
    
    if not record:
        return False
        
    record.status = ChatStatus.FINISHED
    db.commit()
    return True

def create_initial_user_product(db: Session, user_id: int, user_persona_code: str):
    """채팅 시작 시 초기 UserProduct 레코드 생성"""
    user_prod = UserProduct(
        user_id=user_id,
        product_id=0,
        user_type=user_persona_code,
        status="PENDING"
    )
    db.add(user_prod)
    db.commit()
    db.refresh(user_prod)
    return user_prod

# 앱이 "첫 리플라이 재생성" 폴링 시 비교하는 에러 문구 (init_chat_session이 크롤링 미완 시 반환)
FIRST_REPLY_ERROR_MSG = "데이터를 불러오는 데 실패했어. 다시 시도해줄래? 🧐"

SURVEY_MESSAGE_COUNT = 8  # 설문 질문 4 + 답 4


def _deduplicate_first_reply_block(messages: list) -> list:
    """설문(8) 다음 연속된 assistant 메시지는 마지막 1개만 남기고 제거. 실패 재시도 중복 표시 방지."""
    if len(messages) <= SURVEY_MESSAGE_COUNT:
        return messages
    rest = messages[SURVEY_MESSAGE_COUNT:]
    i = 0
    while i < len(rest) and rest[i].get("role") == "assistant":
        i += 1
    if i <= 1:
        return messages
    last_only = rest[i - 1]
    return messages[:SURVEY_MESSAGE_COUNT] + [last_only] + rest[i:]


def save_survey_answers_redis(user_product_id: int, user_answers: dict):
    """설문 답변을 Redis에 저장. refresh_first_reply 폴링 시 사용 (1시간 만료)."""
    key = f"chat:{user_product_id}:survey_answers"
    redis_client.setex(key, 3600, json.dumps(user_answers, ensure_ascii=False))


def room_has_chat_messages(db: Session, user_product_id: int, user_id: int) -> bool:
    """이 방에 이미 채팅 메시지(설문 저장 포함)가 있는지. 있으면 True(재호출=갱신만)."""
    return (
        db.query(Chat)
        .filter(
            Chat.user_product_id == user_product_id,
            Chat.user_id == user_id,
        )
        .limit(1)
        .first()
        is not None
    )


def replace_last_assistant_message(db: Session, user_product_id: int, user_id: int, new_content: str) -> bool:
    """마지막 assistant 메시지가 에러 문구일 때만 새 내용으로 교체. 중복 에러 메시지는 삭제하고 성공한 1개만 유지."""
    # 설문(질문 4 + 답 4) 다음의 "첫 리플라이" 구간에서 에러 메시지 중복 제거, 마지막 1개만 성공 문구로 유지
    all_assistant_after_survey = (
        db.query(Chat)
        .filter(
            Chat.user_product_id == user_product_id,
            Chat.user_id == user_id,
            Chat.role == "assistant",
        )
        .order_by(Chat.chat_id.asc())
        .all()
    )
    # assistant만 보면 앞 4개는 설문 질문, 그 다음부터가 첫 리플라이 후보
    first_reply_candidates = all_assistant_after_survey[4:]
    if not first_reply_candidates:
        return False
    # 에러 문구인 것들 중 마지막 하나만 남기고 나머지 삭제 후, 그 하나를 new_content로 교체
    error_ids = [c.chat_id for c in first_reply_candidates if c.content == FIRST_REPLY_ERROR_MSG]
    if not error_ids:
        # 전부 에러가 아니면 마지막 것만 교체 (이미 성공이 있을 수 있음)
        last_one = first_reply_candidates[-1]
        if last_one.content == FIRST_REPLY_ERROR_MSG:
            last_one.content = new_content
            db.commit()
            cache_key = f"chat_messages:{user_product_id}"
            redis_client.delete(cache_key)
            return True
        return False
    # 마지막 에러 메시지 1개만 남기고 나머지 에러 메시지 삭제
    to_keep = error_ids[-1]
    for cid in error_ids[:-1]:
        db.query(Chat).filter(Chat.chat_id == cid).delete(synchronize_session=False)
    last_assistant = db.query(Chat).filter(Chat.chat_id == to_keep).first()
    if last_assistant:
        last_assistant.content = new_content
    db.commit()
    cache_key = f"chat_messages:{user_product_id}"
    redis_client.delete(cache_key)
    return True


async def refresh_first_reply(
    db: Session, user_id: int, user_product_id: int
) -> Tuple[bool, Optional[str]]:
    """
    분석이 나중에 완료됐을 때 첫 리플라이를 다시 생성해 DB/캐시를 갱신.
    Redis에 저장된 설문 답변으로 init_chat_session을 다시 호출하고,
    에러가 아니면 마지막 assistant 메시지를 실제 분석으로 교체.
    반환: (updated, reply) — updated=True면 reply에 새 첫 리플라이.
    """
    key = f"chat:{user_product_id}:survey_answers"
    raw = redis_client.get(key)
    if not raw:
        return False, None
    try:
        user_answers = json.loads(raw)
    except Exception:
        return False, None
    user_prod = (
        db.query(UserProduct)
        .filter(UserProduct.user_product_id == user_product_id, UserProduct.user_id == user_id)
        .first()
    )
    if not user_prod:
        return False, None
    first_response = await init_chat_session(
        db=db,
        user_id=user_id,
        product_id=user_prod.product_id,
        user_product_id=user_product_id,
        user_answers=user_answers,
    )
    if first_response == FIRST_REPLY_ERROR_MSG or not first_response:
        return False, None
    ok = replace_last_assistant_message(db, user_product_id, user_id, first_response)
    return ok, first_response


def finalize_chat_survey(db: Session, user_id: int, user_product_id: int, user_answers: dict, first_response: str):
    """설문 완료 시 질문/답변 및 AI 첫 응답 저장. 이미 설문이 있으면(레이스 등) 첫 리플라이만 추가/갱신."""
    existing_count = (
        db.query(Chat)
        .filter(
            Chat.user_product_id == user_product_id,
            Chat.user_id == user_id,
        )
        .count()
    )
    if existing_count >= SURVEY_MESSAGE_COUNT:
        # 설문은 이미 저장됨. 첫 리플라이만 추가 또는 마지막 assistant로 교체
        if existing_count == SURVEY_MESSAGE_COUNT:
            save_chat_message(db, user_id, user_product_id, "assistant", first_response)
        else:
            replace_last_assistant_message(db, user_product_id, user_id, first_response)
        return True

    # 1. 설문 질문과 답변 저장
    survey_pairs = [
        ("이거 장바구니/찜에 담은 지 얼마나 됐어?", get_q1_text(user_answers.get('q1'))),
        ("나한테 왜 연락한 거야?", get_q2_text(user_answers.get('q2'))),
        ("이 옷, 이미 거의 사기로 마음 정한 상태야? 아니면 아직 확신이 부족해?", get_q3_text(user_answers.get('q3'))),
        ("이 옷의 어떤 점이 네 마음을 뺏었어?", get_qc_text(user_answers.get('qc')))
    ]

    for question, answer in survey_pairs:
        save_chat_message(db, user_id, user_product_id, "assistant", question)
        save_chat_message(db, user_id, user_product_id, "user", answer)

    # 2. AI의 첫 분석 답변 저장
    save_chat_message(db, user_id, user_product_id, "assistant", first_response)

    return True
