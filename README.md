## 또바바 Back-end

심리 설문과 상품 분석을 바탕으로 **쇼핑 의사결정을 도와주는 서비스 “또바바”의 백엔드 서버**입니다.  
FastAPI 기반의 API 서버로, 사용자 관리, 상품 분석, 채팅(LLM), 대시보드 기능을 제공합니다.

---

## 주요 기능

- **유저 관리**
  - 이메일 기반 회원가입/로그인
  - JWT 기반 인증 (토큰을 `Authorization` 헤더로 전달)
  - 프로필(닉네임, 프로필 이미지) 수정
  - 페르소나(SBTI) 결과 저장/조회
  - 관심 쇼핑몰, 추구미, 옷장 통계 조회

- **상품 분석 & 크롤링**
  - 사용자가 전달한 상품 URL을 기반으로 상품 정보를 크롤링
  - Selenium 컨테이너를 통해 실제 브라우저 환경에서 페이지 분석

- **채팅(LLM) 기반 의사결정 보조**
  - 상품 분석 + 유저 심리 설문을 바탕으로 LLM(Gemini)에게 컨텍스트 전달
  - 채팅방 단위의 대화 세션 관리 (DB + Redis 캐시)
  - 분석 지연 시 폴링, 종료 메시지 등 채팅 라이프사이클 관리

- **대시보드/통계**
  - 유저의 구매/포기 기록을 바탕으로 통계 제공
  - 옷장 요약 정보(개수, 금액 등) 조회

---

## 기술 스택

- **언어/런타임**: Python 3.12
- **웹 프레임워크**: FastAPI (`app/main.py`)
- **ASGI 서버**: Uvicorn (Dockerfile CMD)
- **ORM/DB**:
  - SQLAlchemy (`app/core/database.py`)
  - RDB (예: PostgreSQL, MySQL 등, `DATABASE_URL` 설정에 따라 결정)
- **캐시/세션**: Redis
- **브라우저 자동화**: Selenium (`SELENIUM_URL`로 컨테이너와 통신)
- **LLM**: Google Generative AI (Gemini, `app/chat/agent.py` 등)
- **Reverse Proxy & SSL**: Nginx + Certbot
- **컨테이너/오케스트레이션**: Docker, Docker Compose
- **GPU 사용**: NVIDIA GPU (Docker Compose의 `deploy.resources` 설정)

---

## 디렉터리 구조 (요약)

```text
back-end/
├── app/
│   ├── main.py                # 실제 서비스용 FastAPI 앱 엔트리포인트
│   ├── api_server.py          # 샘플/개발용 간단 API 서버
│   ├── web_server.py          # Flask 기반 테스트 웹 서버
│   ├── core/
│   │   ├── config.py          # Settings, 환경 변수 로딩
│   │   ├── database.py        # SQLAlchemy 엔진/세션, Base
│   │   └── security.py        # JWT 발급/검증 유틸
│   ├── users/                 # 유저 관련 모델/스키마/라우터
│   ├── products/              # 상품/사용자-상품 관계, 파서/크롤링
│   ├── chat/                  # 채팅, 설문, 점수/선호도 계산, LLM 연동
│   │   ├── after_chat/        # 채팅 종료 이후 로직
│   │   └── logic/             # 설문/점수/선호도 계산 모듈
│   └── dashboard/             # 홈/대시보드 관련 API
├── nginx/                     # Nginx 설정 (`conf.d/default.conf` 등)
├── certbot/                   # SSL 인증서 관련 디렉터리
├── redis_data/                # Redis 데이터 볼륨
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## 빠른 시작 (Quick Start)

### 1. 사전 준비

- Docker & Docker Compose 설치
- NVIDIA GPU + 드라이버 (GPU 기능을 사용할 경우)
- 프로젝트 루트(`back-end/`)에 `.env` 파일 생성 (아래 “환경 변수” 참고)

### 2. Docker Compose로 실행

```bash
cd back-end
docker-compose up -d --build
```

- 첫 실행 시에는 `--build` 옵션으로 이미지를 새로 빌드하는 것을 권장합니다.
- 컨테이너가 모두 정상 기동되면:
  - API 서버는 Nginx 뒤에서 동작하며, 도메인 환경에서는 대략 `https://<your-domain>/api/...` 형태로 접근합니다.
  - 로컬에서 단순 테스트용으로는 `app` 컨테이너의 `8001` 포트에 직접 붙을 수 있습니다.

### 3. 헬스 체크

- FastAPI 앱에서 제공하는 헬스 체크 엔드포인트:
  - `GET /api/health`
  - DB와 Redis 연결 상태를 모두 확인합니다.
- Docker Compose의 `healthcheck` 설정도 이 엔드포인트를 기준으로 동작합니다.

### 4. 로컬 개발 모드로 실행 (옵션)

Docker 없이 개발 중에만 백엔드만 띄우고 싶다면:

```bash
cd back-end
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

- `.env` 파일이 `back-end/.env` 위치에 있어야 합니다.
- 프론트엔드에서 이 백엔드에 붙을 때는 `BASE_URL`을 `http://localhost:8001` 또는 프록시 주소로 맞춰 주세요.

---

## 환경 변수 (.env 예시)

아래는 예시이며, 실제 값은 운영 환경에 맞게 설정해야 합니다.

```env
APP_ENV=local

DATABASE_URL=postgresql+psycopg2://user:password@host:5432/dbname

SECRET_KEY=your-secret-key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

REDIS_HOST=redis
REDIS_PORT=6379

SELENIUM_URL=http://selenium:4444/wd/hub

GOOGLE_API_KEY=your-google-api-key
```

- `.env` 파일은 **절대로 Git에 커밋하지 마세요.**
- 운영 환경에서는 Secret Manager, 환경 변수 설정 등 별도의 보안 채널을 통해 관리하는 것을 권장합니다.

---

## 주요 API 요약

### 1. 공통/헬스 체크

- `GET /`  
  - 서비스 환영 메시지 반환.
- `GET /api/health`  
  - DB + Redis 연결 상태를 확인하는 헬스 체크 엔드포인트.

### 2. 유저 관리 (`app/users/router.py`)

- 인증 방식: `Authorization` 헤더에 JWT 토큰 전달
  - 예: `Authorization: <access_token>`
- 주요 엔드포인트
  - `POST /api/auth/signup` : 회원가입
  - `POST /api/auth/login` : 로그인 및 액세스 토큰 발급
  - `GET /api/profile` : 내 프로필 조회
  - `PATCH /api/setting/profile` : 닉네임/프로필 이미지 수정
  - `POST /api/setting/profile/persona` : 페르소나(SBTI) 결과 저장
  - `GET /api/profile/persona` : 나의 페르소나 조회
  - `POST /api/profile/shop` / `GET /api/profile/shop` : 관심 쇼핑몰 저장/조회
  - `POST /api/profile/chugume` / `GET /api/profile/chugume` : 추구미 저장/조회
  - `GET /api/profile/closet` : 옷장 통계 조회 (구매/포기 개수 및 금액)

### 3. 채팅 (`app/chat/router.py`)

채팅 플로우(간단 개요):

1. 사용자가 상품 URL을 보내면 분석 시작 및 설문 항목을 반환
2. 설문 응답을 제출하면 첫 분석 결과 및 초기 채팅 메시지 생성
3. 이후 일반 채팅처럼 메시지를 주고받으며, 대화 내용은 DB/Redis에 저장
4. 채팅 종료 시 상태를 `FINISHED`로 변경하고, 마지막 종료 메시지를 반환

- 주요 엔드포인트
  - `POST /api/chat/start` : 상품 분석 시작 + 설문 항목 반환
  - `POST /api/chat/finalize-survey/{user_product_id}` : 설문 완료 + 첫 응답 생성
  - `GET /api/chat/list` : 내 채팅방 목록 조회
  - `GET /api/chat/room/{user_product_id}` : 특정 채팅방 상세 정보 및 메시지 목록
  - `POST /api/chat/{user_product_id}/messages/` : 채팅 메시지 전송(한 턴)
  - `POST /api/chat/exit/{user_product_id}` : 채팅 종료
  - `POST /api/chat/room/{user_product_id}/refresh-first-reply` : 분석 지연 시 첫 응답 재생성

### 4. 대시보드 (`app/dashboard/home_router.py`)

- 유저의 활동/상품 관련 정보를 요약해 주는 홈/대시보드 API를 제공합니다.  
  (구체적인 필드는 코드/Swagger 문서를 참고하세요.)

> 전체 스펙은 FastAPI의 자동 문서화 페이지에서 확인할 수 있습니다.  
> - `http://<host>:8001/docs` (Swagger UI)  
> - `http://<host>:8001/redoc`

---

## 데이터베이스 개요

- 이 서비스는 SQLAlchemy ORM을 사용하여 RDB와 연동합니다.
- 주요 개념(테이블)
  - **User**: 사용자 계정, 프로필, 페르소나, 추구미 등
  - **Product**: 개별 상품 정보
  - **UserProduct**: 사용자-상품 관계, 구매/포기 상태, 가격 등
  - **Chat 관련 엔티티**: 상품별 채팅방, 메시지 기록 등을 저장
- 마이그레이션 도구(Alembic 등)를 사용 중이라면, 향후 `README`에 명령어와 흐름을 추가로 문서화할 수 있습니다.

---

## 배포 & 운영

- **Nginx + Certbot**
  - Nginx 컨테이너가 `80`/`443` 포트를 열고, FastAPI 백엔드(`app` 서비스, 포트 `8001`)로 리버스 프록시합니다.
  - Certbot 컨테이너는 SSL 인증서를 발급/갱신하며, Nginx는 해당 인증서를 사용합니다.
  - 실제 도메인/경로 설정은 `nginx/conf.d/default.conf`에서 조정합니다.

- **환경 구분**
  - `APP_ENV` 값을 이용해 local / staging / production 등을 구분할 수 있습니다.
  - 환경별로 `.env` 값(DATABASE_URL, GOOGLE_API_KEY 등)을 분리해서 관리하는 것을 권장합니다.

---

## 개발자 가이드

- **레이어 구조**
  - FastAPI 라우터(`router.py`)는 **HTTP 요청/응답 정의만 담당**하고,
  - 실제 비즈니스 로직은 `service.py`, 데이터 접근은 `repository.py`/ORM 모델에 두는 패턴을 사용합니다.
- **인증/보안**
  - 로그인 성공 시 발급된 JWT 액세스 토큰을 이후 요청의 `Authorization` 헤더에 포함해야 합니다.
  - 민감 정보(비밀번호, API 키 등)는 코드에 하드코딩하지 않고 `.env` 또는 Secret Manager로 관리합니다.
- **로깅/에러 처리**
  - `app/main.py`에 글로벌 예외 핸들러가 정의되어 있어, 처리되지 않은 예외를 로그로 남기고 500 응답을 반환합니다.
  - 추가적인 APM/에러 모니터링(Sentry 등)을 붙이고 싶다면 이 레이어에서 확장할 수 있습니다.

---

## 향후 개선 아이디어

- [ ] 주요 엔드포인트에 대한 요청/응답 예시(JSON) 추가
- [ ] 테스트 코드 및 `pytest` 실행 방법 문서화
- [ ] OpenAPI 문서(`/docs`, `/redoc`) 캡처 이미지 또는 GIF 추가
- [ ] Sentry, Cloud Logging 등 모니터링/알람 도구 연동 및 문서화
- [ ] DB 마이그레이션(Alembic 등) 사용 시 가이드 추가
