"""S-BTI 16유형 정의 및 유틸리티."""

# 4개 심리 축
AXES = {
    "DN": ("D", "N"),  # 도파민 vs 필요
    "UI": ("U", "I"),  # 타인 검증 vs 자기 판단
    "TM": ("T", "M"),  # 동조 vs 차별화
    "EO": ("E", "O"),  # 거래 이득 vs 물건 가치
}

ALL_TYPES = [
    "DUTE", "DUTO", "DUME", "DUMO",
    "DITE", "DITO", "DIME", "DIMO",
    "NUTE", "NUTO", "NUME", "NUMO",
    "NITE", "NITO", "NIME", "NIMO",
]

TYPE_NAMES = {
    "DUTE": "신나는 돼지",
    "DUTO": "호기심 많은 원숭이",
    "DUME": "민첩한 미어캣",
    "DUMO": "깐깐한 고슴도치",
    "DITE": "고집 센 고양이",
    "DITO": "화려한 공작",
    "DIME": "엉뚱한 개구리",
    "DIMO": "까다로운 백조",
    "NUTE": "부지런한 햄스터",
    "NUTO": "우직한 곰",
    "NUME": "예리한 토끼",
    "NUMO": "느긋한 거북이",
    "NITE": "직진하는 치타",
    "NITO": "꼼꼼한 다람쥐",
    "NIME": "영리한 여우",
    "NIMO": "여유로운 카피바라",
}


def parse_sbti_flags(sbti_code: str) -> dict:
    """S-BTI 4글자 코드를 개별 축 플래그 dict로 변환.

    >>> parse_sbti_flags("DUTE")
    {'is_D': True, 'is_N': False, 'is_U': True, 'is_I': False,
     'is_T': True, 'is_M': False, 'is_E': True, 'is_O': False}
    """
    code = sbti_code.upper()
    return {
        "is_D": code[0] == "D",
        "is_N": code[0] == "N",
        "is_U": code[1] == "U",
        "is_I": code[1] == "I",
        "is_T": code[2] == "T",
        "is_M": code[2] == "M",
        "is_E": code[3] == "E",
        "is_O": code[3] == "O",
    }
