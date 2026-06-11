# 전화번호 인증 & 아이디 찾기 구현 계획

## 목표
1. 회원가입 시 전화번호 SMS 인증 추가
2. 아이디 찾기 (전화번호 → 이메일 마스킹 반환)

---

## 구현 순서

### Step 1. CoolSMS SDK 연동
- `requirements.txt` → `coolsms-python-sdk` 추가
- `app/core/config.py` → `COOLSMS_API_KEY`, `COOLSMS_API_SECRET`, `COOLSMS_FROM` 추가
- `app/core/sms.py` 생성 → `send_verification_code(to, code)` 함수

### Step 2. DB — phone 컬럼 추가
- `app/users/models.py` → `User` 모델에 `phone = Column(String(20), nullable=True, unique=True)` 추가
- Alembic 없이 `ALTER TABLE` 직접 실행 (현행 방식 유지)

### Step 3. 전화번호 인증 엔드포인트 (회원가입용)
| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| POST | `/auth/phone/request` | `{ phone }` | `{ result: null }` |
| POST | `/auth/phone/verify` | `{ phone, code }` | `{ result: null }` |

- Redis 키: `phone_verify:code:{phone}` (TTL 180초)
- 인증 완료 마킹: `phone_verify:verified:{phone}` (TTL 600초)
- 이미 가입된 번호면 400 반환

### Step 4. 회원가입 수정
- `schemas.UserCreate` → `phone: str` 필드 추가
- `/auth/signup` → 인증 완료 여부(`phone_verify:verified:{phone}`) 확인 후 가입 처리, phone 저장

### Step 5. 아이디 찾기 엔드포인트
| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| POST | `/auth/find-email/request` | `{ phone }` | `{ result: null }` |
| POST | `/auth/find-email/verify` | `{ phone, code }` | `{ result: { maskedEmail } }` |

- 미가입 번호도 200 반환 (보안)
- 이메일 마스킹 예시: `tt****@gmail.com`

---

## Redis 키 정리
| 키 | 값 | TTL |
|---|---|---|
| `phone_verify:code:{phone}` | 6자리 코드 | 180초 |
| `phone_verify:verified:{phone}` | `"1"` | 600초 |
| `find_email:code:{phone}` | 6자리 코드 | 180초 |

---

## .env에 추가할 항목
```
COOLSMS_API_KEY=
COOLSMS_API_SECRET=
COOLSMS_FROM=01012345678   # 발신번호 (CoolSMS 등록 필요)
```

## 진행 상태
- [x] Step 1. CoolSMS SDK 연동
- [x] Step 2. DB phone 컬럼 추가
- [x] Step 3. 전화번호 인증 엔드포인트
- [x] Step 4. 회원가입 수정
- [x] Step 5. 아이디 찾기 엔드포인트
