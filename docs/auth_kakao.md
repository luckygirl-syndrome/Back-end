# 카카오 소셜 로그인 구조

## 방식: ID Token 검증 (OIDC)

프론트에서 카카오 SDK로 로그인 → ID Token을 백엔드에 전달 → 백엔드가 검증 후 우리 JWT 발급

---

## 전체 흐름

```
1. 프론트: 카카오 로그인 버튼 탭
2. 프론트: 카카오 SDK가 ID Token 발급 (OIDC)
3. 프론트 → 백엔드: POST /api/auth/kakao { "id_token": "..." }
4. 백엔드: 카카오 공개키로 ID Token 검증
5. 백엔드: 토큰에서 이메일, 닉네임, 프로필 이미지 추출
6. 백엔드: DB에 유저 없으면 자동 가입, 있으면 로그인
7. 백엔드 → 프론트: 우리 서비스 JWT 액세스 토큰 반환
```

---

## 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/auth/kakao` | 카카오 ID Token 검증 후 JWT 발급 |

**요청 바디**
```json
{
  "id_token": "eyJhbGciOiJSUzI1NiIs..."
}
```

**응답**
```json
{
  "status": "success",
  "access_token": "<우리 서비스 JWT>",
  "token_type": "bearer",
  "is_new_user": true
}
```

- `is_new_user: true` → 첫 로그인 (온보딩 페이지로 이동)
- `is_new_user: false` → 기존 유저 (메인 페이지로 이동)

---

## DB 변경 사항

구글 로그인 때 이미 추가됨. 추가 변경 없음.

- `social_provider`: `"kakao"` 로 저장
- `social_id`: 카카오 고유 ID (`sub` 클레임)
- `password`: nullable

---

## 유저 시나리오

### 케이스 1: 처음 카카오 로그인 (신규 유저)
1. ID Token 검증
2. 이메일로 DB 조회 → 없음
3. `social_provider="kakao"`, `password=null` 로 자동 가입
4. JWT 발급 + `is_new_user: true` 반환

### 케이스 2: 기존 카카오 유저 재로그인
1. ID Token 검증
2. 이메일로 DB 조회 → 있고 `social_provider="kakao"`
3. JWT 발급 + `is_new_user: false` 반환

### 케이스 3: 같은 이메일로 일반/구글 가입 유저가 카카오 로그인 시도
1. ID Token 검증
2. 이메일로 DB 조회 → 있는데 `social_provider`가 다름
3. 400 에러: "이미 다른 방식으로 가입된 계정입니다."

---

## 필요한 패키지

```
python-jose[cryptography]==3.5.0  # 이미 설치됨
httpx==0.27.0                     # 카카오 공개키 fetch용
```

`requirements.txt` 에 `httpx` 추가 필요.

---

## 환경변수 추가

`.env` 에 추가:
```
KAKAO_REST_API_KEY=발급받은_REST_API_키
```

카카오 디벨로퍼스 → 내 애플리케이션 → 앱 키 → REST API 키

---

## 핵심 파일 (구현 위치)

| 파일 | 작업 내용 |
|------|-----------|
| `requirements.txt` | `httpx` 추가 |
| `app/core/config.py` | `KAKAO_REST_API_KEY` 환경변수 추가 |
| `app/users/schemas.py` | `KakaoLoginRequest` 스키마 추가 |
| `app/users/router.py` | `POST /api/auth/kakao` 엔드포인트 추가 |

---

## 보안 포인트

- 카카오 OIDC 공개키(`https://kauth.kakao.com/.well-known/jwks.json`)로 서명 검증
- `aud` 클레임이 우리 REST API 키와 일치하는지 검증
- HTTPS 환경에서만 운용

---

## 미구현 / 추후 고려

- [ ] 카카오 계정 연결 해제
- [ ] 리프레시 토큰
