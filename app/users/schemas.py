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
    persona_type: str  # "DUTE" 등 4글자 코드
    d_vs_n: AxisScore
    u_vs_i: AxisScore
    t_vs_m: AxisScore
    e_vs_o: AxisScore

class PersonaRead(BaseModel):
    persona: Optional[FbtiFinalResult] = None

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

class ChugumeType(str, Enum):
    MORI = "모리걸"
    DEMURE = "드뮤어"
    GIRLCORE = "걸코어"
    SPORTY = "스포티 글램"

class ChugumeUpdate(BaseModel):
    chugume_type: ChugumeType

# 6. 나의 옷장 통계 (마이페이지)
class ClosetStatsRead(BaseModel):
    bought_count: int
    bought_price: int
    dropped_count: int
    dropped_price: int