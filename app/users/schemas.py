from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
from enum import Enum

# 1. 회원가입/로그인용
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    nickname: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        return v

class UserLogin(BaseModel):
    email: EmailStr
    password: str

# 2. 기본 프로필 조회용
class ProfileData(BaseModel):
    nickname: str
    profile_img: str
    fbti_name: str

class ProfileRead(BaseModel):
    profile_data: ProfileData

# 3. FBTI/페르소나 데이터 구조
class AxisScore(BaseModel):
    result: str    # "D" 또는 "N" 등
    score: int     # 0~3

class FbtiFinalResult(BaseModel):
    fbti_type: str  # "DUTE" 등 4글자 코드
    d_vs_n: AxisScore
    u_vs_i: AxisScore
    t_vs_m: AxisScore
    e_vs_o: AxisScore

class PersonaRead(BaseModel):
    fbti: Optional[FbtiFinalResult] = None

# 3. 소셜 로그인용
class GoogleLoginRequest(BaseModel):
    id_token: str

class KakaoLoginRequest(BaseModel):
    id_token: str

class AppleLoginRequest(BaseModel):
    id_token: str

# 4. 닉네임 수정용
class NicknameUpdate(BaseModel):
    nickname: str
    
# 5. 쇼핑몰 및 추구미 (언니가 저장해달라고 했던 핵심 기능!)
class ShopName(str, Enum):
    MUSINSA = "무신사"
    ABLY = "에이블리"
    ZIGZAG = "지그재그"

class UserShopsUpdate(BaseModel):
    favorite_shops: List[ShopName]

class StyleType(str, Enum):
    SIMPLE_BASIC = "심플베이직"
    ROCK_CHIC = "락시크"
    HIP = "힙"
    FEMININE = "페미닌"
    LOVELY = "러블리"
    MORI = "모리걸"
    VINTAGE = "빈티지"
    STREET = "스트릿"
    CASUAL = "캐주얼"
    SEXY_GLAM = "섹시글램"
    SPORTY = "스포티"
    MINIMAL = "미니멀"

class AgeGroup(str, Enum):
    TEEN = "10대"
    TWENTY_EARLY = "20~24"
    TWENTY_LATE = "25~29"
    THIRTY_PLUS = "30대 이상"

class RegretFrequency(str, Enum):
    NONE = "없어요"
    ONE_TO_TWO = "1~2번 정도"
    THREE_PLUS = "3번 이상"

class RegretReason(str, Enum):
    DUPLICATE = "비슷한 옷이 이미 있었어요"
    NO_OCCASION = "막상 입을 일이 없었어요"
    IMPULSE = "할인이나 한정 특가에 급하게 결제했어요"
    DIFFERENT_FROM_PHOTO = "사진이랑 실물이 너무 달랐어요"
    LOW_QUALITY = "소재나 퀄리티가 기대 이하였어요"
    BAD_FIT = "핏이 예상과 달랐어요"
    DOESNT_SUIT = "나한테 어울리지 않았어요"
    NO_MATCHING = "코디할 다른 옷이 없어서 결국 못 입었어요"

class StyleUpdate(BaseModel):
    style: List[StyleType]

class OnboardingCreate(BaseModel):
    age_group: AgeGroup
    style: List[StyleType]
    regret_frequency: RegretFrequency
    regret_reasons: Optional[List[str]] = None
    regret_reason_custom: Optional[str] = None

# 6. 나의 옷장 통계 (마이페이지)
class ClosetStatsRead(BaseModel):
    bought_count: int
    bought_price: int
    dropped_count: int
    dropped_price: int

# 7. 문의하기
class InquiryCreate(BaseModel):
    content: str
    reply_email: Optional[EmailStr] = None

# 8. 소셜 연동/해제
class SocialLinkRequest(BaseModel):
    provider: str
    id_token: str

class SocialUnlinkRequest(BaseModel):
    provider: str

# 9. 비밀번호
class PasswordVerify(BaseModel):
    current_password: str

class PasswordChange(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        return v

# 10. 비밀번호 재설정 (이메일 코드 방식)
class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetVerify(BaseModel):
    email: EmailStr
    code: str

class PasswordResetConfirm(BaseModel):
    resetToken: str
    newPassword: str

    @field_validator("newPassword")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        return v