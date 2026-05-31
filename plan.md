## Back-end README 작성 계획

이 문서는 `back-end/README.md`를 어떻게 구성할지에 대한 설계서입니다.  
프론트엔드 README와 톤을 맞추되, **운영/배포에 필요한 정보와 API 개요**를 충분히 담는 것을 목표로 합니다.

---

## 1. 프로젝트 개요 섹션

- **섹션 제목 예시**
  - `또바바 Back-end`
  - 또는 `또바바 Back-end (FastAPI 서비스)`
- **포함할 내용**
  - 서비스 한 줄 설명
    - 예: “심리 설문 + 상품 분석 기반 쇼핑 의사결정 도우미인 또바바의 백엔드 서버입니다.”
  - 핵심 키워드
    - FastAPI, Redis, Selenium, Google Generative AI(Gemini), Nginx, Docker, GPU 사용 등을 짧게 나열
  - 이 저장소가 담당하는 역할
    - 프론트와 통신하는 API 서버
    - 채팅/설문/상품 분석 로직 담당

---

## 2. 기술 스택 & 아키텍처 섹션

- **기술 스택 표 또는 리스트**
  - 언어/런타임: Python 3.12
  - 웹 프레임워크: FastAPI (`app/main.py`, `app/api_server.py`)
  - WSGI/ASGI 서버: Uvicorn (Dockerfile CMD 참고)
  - 데이터베이스: (실제 사용 DB 종류 명시 – 예: PostgreSQL, MySQL 등)  
    - `DATABASE_URL` 예시 포맷을 README에 적을 것
  - ORM: SQLAlchemy (`app/core/database.py`, 모델들)
  - 캐시/세션: Redis (`app/chat/repository.py` 및 `settings.REDIS_HOST/PORT`)
  - 브라우저 자동화: Selenium (`SELENIUM_URL = http://selenium:4444/wd/hub`)
  - LLM: Google Generative AI(Gemini) (`app/chat/agent.py` 및 관련 설정)
  - Reverse proxy & SSL: Nginx + Certbot (`docker-compose.yml`, `nginx/conf.d`)
  - 컨테이너 오케스트레이션: Docker Compose
  - GPU: NVIDIA GPU 사용(Compose `deploy.resources.reservations.devices`)
- **아키텍처 개요 그림/설명**
  - 텍스트 기반으로라도 다음 흐름을 설명
    - 클라이언트 → Nginx(80/443) → FastAPI 컨테이너(8001)  
      → DB/Redis/Selenium/LLM 와의 상호작용
  - 가능하다면 README에는 간단한 다이어그램(ASCII 또는 이미지 링크) 넣기

---

## 3. 디렉터리 구조 섹션

- **요약 구조 예시**
  - 최상단:
    - `Dockerfile`, `docker-compose.yml`, `.env`(예시만), `requirements.txt`
  - `app/`
    - `main.py`: 실제 배포용 FastAPI 앱 엔트리포인트 (lifespan, 라우터 등록, 헬스체크)
    - `api_server.py`: 샘플/개발용 간단 FastAPI 서버 (헬스 체크 + /api/users)
    - `web_server.py`: Flask 기반 간단 웹 페이지(테스트용)
    - `core/`
      - `config.py`: 환경 변수 로딩, `Settings` 정의
      - `database.py`: SQLAlchemy 엔진/세션, `Base`
      - `security.py`: JWT 발급/검증 유틸
    - `users/`: 유저 관리 (회원가입, 로그인, 프로필, 페르소나, 추구미, 옷장 통계 등)
    - `products/`: 상품 및 사용자-상품 관계, 크롤링/파서
    - `chat/`: 채팅 로직, 프롬프트, LLM 연동, 설문, 점수 계산 등
      - `after_chat/`: 채팅 이후 로직
      - `logic/`: 설문/점수/선호도 계산 모듈들
    - `dashboard/`: 홈/대시보드 관련 API
  - `nginx/`, `certbot/`, `redis_data/`: 인프라 관련 디렉터리 설명
- README에는 **중요 디렉터리 위주로만** 트리 구조를 요약해 적고, 너무 깊이 들어가지 않기

---

## 4. 실행 방법 섹션

### 4.1. 환경 준비 공통

- **사전 요구사항 명시**
  - Docker & Docker Compose
  - NVIDIA 드라이버 및 `--gpus all`(혹은 Compose의 GPU 설정)이 가능한 환경
  - `.env` 파일 준비 (아래 환경 변수 섹션 참조)
- **requirements.txt 기반 로컬 실행도 필요하다면 옵션으로 설명**
  - Python 3.12 + `pip install -r requirements.txt`

### 4.2. Docker Compose로 실행

- **기본 실행 명령어**
  - `docker-compose up -d --build`
  - 첫 배포 시 `--build` 옵션을 써야 하는 이유 간단히 설명
- **접속 방법**
  - API: `https://<도메인 또는 IP>/api/...` (Nginx 프록시 뒤에 있을 경우)
  - 개발용: Nginx 없이 `http://localhost:8001` 로 직접 접속하는 방법도 함께 안내
- **헬스체크 설명**
  - `GET /api/health`가 DB + Redis를 모두 점검한다는 점 (`app/main.py`)
  - Compose의 healthcheck 설정과 연동되는 부분 간단 설명

### 4.3. 로컬 개발용 실행 (옵션)

- `uvicorn app.main:app --reload --port 8001` 형태 명령 예시
- `.env`를 루트에 두어야 하는 점(`Settings`에서 `.env`를 로드함)을 강조
- 프론트엔드와 로컬로 어떻게 붙여 쓸지(예: CORS, BASE_URL) 간략한 언급

---

## 5. 환경 변수 & 설정 섹션

- **.env 예시 블록 추가 계획**
  - `DATABASE_URL=...`
  - `SECRET_KEY=...`
  - `ALGORITHM=HS256` (예시)
  - `ACCESS_TOKEN_EXPIRE_MINUTES=60` (예시)
  - `REDIS_HOST=redis` (Compose 기준)
  - `REDIS_PORT=6379`
  - `GOOGLE_API_KEY=...` (실제 키 이름에 맞게)
  - `APP_ENV=local` / `prod` 등
  - `SELENIUM_URL=http://selenium:4444/wd/hub`
- **보안 주의사항 문구**
  - `.env`는 절대 커밋하지 말 것
  - 실제 운영 키는 Secret Manager 혹은 서버 환경변수로 관리 권장
- **DB SSL 설정 요약**
  - `Settings.db_engine_kwargs`에서 `ssl` 옵션과 `certifi`를 사용하는 부분을 간단히 설명
  - 클라우드 DB(예: Cloud SQL, RDS 등)를 쓴다면 왜 SSL 설정이 필요한지 서술

---

## 6. 주요 API 엔드포인트 요약 섹션

- **1) 공통/헬스체크**
  - `GET /` : 간단한 환영 메시지
  - `GET /api/health` : DB + Redis 상태 확인

- **2) 유저 관리 (`app/users/router.py`)**
  - 인증 방식: `Authorization` 헤더에 JWT 토큰 (APIKeyHeader 사용)
  - 주요 엔드포인트 목록 + 한 줄 설명
    - `POST /api/auth/signup` : 회원가입
    - `POST /api/auth/login` : 로그인, JWT 발급
    - `GET /api/profile` : 내 프로필 조회
    - `PATCH /api/setting/profile` : 닉네임/프로필 이미지 수정
    - `POST /api/setting/profile/persona` : SBTI/페르소나 결과 저장
    - `GET /api/profile/persona` : 나의 페르소나 조회
    - `POST /api/profile/shop` / `GET /api/profile/shop` : 관심 쇼핑몰 저장/조회
    - `POST /api/profile/chugume` / `GET /api/profile/chugume` : 추구미 저장/조회
    - `GET /api/profile/closet` : 옷장 통계 조회 (구매/포기 개수, 금액 등)

- **3) 채팅 (`app/chat/router.py`)**
  - 채팅 흐름 개요를 README에 짧게 그림/플로우로 설명
    - 상품 URL 전달 → 설문 시작 → 설문 완료/분석 결과 → 채팅 이어가기 → 종료
  - 주요 엔드포인트
    - `POST /api/chat/start` : 상품 URL 기반 분석 시작 + 설문 항목 제공 (BackgroundTasks로 크롤링)
    - `POST /api/chat/finalize-survey/{user_product_id}` : 설문 완료 + 첫 응답 생성
    - `GET /api/chat/list` : 내 채팅방 목록 조회
    - `GET /api/chat/room/{user_product_id}` : 특정 채팅방/상품의 상세 & 대화 내역
    - `POST /api/chat/{user_product_id}/messages/` : 채팅 한 턴(메시지 전송)
    - `POST /api/chat/exit/{user_product_id}` : 채팅 종료
    - `POST /api/chat/room/{user_product_id}/refresh-first-reply` : 첫 리플라이 재생성(지연 처리용)

- **4) 대시보드 (`app/dashboard/home_router.py`)**
  - 어떤 통계를 제공하는지 간략히 요약

- **5) 기타**
  - `app/products/*` : 상품 크롤링/분석 API가 있다면, 주요 엔드포인트를 README에서 요약
  - 필요한 경우, 응답 예시(JSON)를 1~2개만 넣기

---

## 7. 데이터베이스 & 모델 섹션

- **내용 계획**
  - 사용하는 RDBMS 종류 및 버전
  - 주요 테이블 개념 설명
    - `users` (User)
    - `products` (Product)
    - `user_products` (UserProduct: 사용자-상품 관계, 상태/가격 등)
    - `chats` 등 채팅 관련 테이블 (실제 모델 구조에 맞게 정리)
  - 마이그레이션 도구 사용 여부 명시
    - Alembic 등을 사용하고 있다면 명시하고, 기본 명령 예시 적기
    - 아직 없다면 `TODO: 마이그레이션 도구 추가 예정` 정도로 기재

---

## 8. 배포 & 운영 섹션

- **Nginx + Certbot 구성 요약**
  - Nginx가 80/443을 열고, 백엔드 `app` 서비스(8001)로 프록시하는 구조
  - Certbot 컨테이너가 SSL 인증서 자동 갱신 담당
  - `nginx/conf.d/default.conf`에서 도메인/백엔드 업스트림 수정하는 포인트만 README에 강조
- **환경별 배포 전략**
  - 로컬: 단순히 `docker-compose up` (SSL 없이 80/8001 등으로 접근)
  - 스테이징/프로덕션: 도메인 + SSL, GPU 리소스 세팅, `.env` 차이 설명
- **로그 & 모니터링**
  - 최소한 FastAPI의 글로벌 예외 핸들러(`app/main.py`)가 있는 점을 안내
  - 추가로 붙일 수 있는 APM/로깅 스택은 “향후 개선점”으로 적어둘 수 있음

---

## 9. 개발 가이드 & 주의사항 섹션

- **개발자가 알아야 할 포인트**
  - LLM(Gemini) 연동 시 필요한 환경 변수/키 위치
  - Redis를 사용한 채팅 세션/캐시 전략 개요
  - Selenium을 이용한 상품 페이지 크롤링 동작 개요
  - 민감 정보(토큰/키)는 코드에 하드코딩하지 말 것
- **코드 스타일/컨벤션**
  - FastAPI 라우터는 `router.py`에서 선언, 비즈니스 로직은 `service.py`로 분리하는 패턴을 README에 명시
  - 한글 응답/에러메시지를 사용하는 스타일 유지

---

## 10. 향후 개선/To-do 섹션

- README에 다음과 같은 체크리스트를 포함하도록 계획
  - [ ] OpenAPI 문서 링크 추가 (`/docs`, `/redoc`)
  - [ ] Swagger 스크린샷 또는 GIF 추가
  - [ ] 주요 엔드포인트에 대한 예시 요청/응답 추가
  - [ ] 테스트 실행 방법 (`pytest` 등) 문서화
  - [ ] Sentry/Cloud Logging 등 에러 모니터링 도구 연동 시 문서화

---

## 11. 실제 README 작성 시 톤 & 스타일

- **톤**
  - 프론트엔드 README와 비슷하게, **친근하지만 정보는 충분히 구체적으로**.
  - 주석처럼 설명을 길게 붙이기보다, 섹션/리스트 위주로 깔끔하게 정리.
- **형식**
  - 상단에 프로젝트 로고/이름, 짧은 소개
  - 바로 아래에 “빠른 시작(Quick Start)” 섹션을 배치해, 개발자가 1~2 단락만 읽고도 `docker-compose up`까지 갈 수 있게 구성
  - 그 아래에 자세한 섹션들(아키텍처, API, DB, 배포, 개발 가이드)을 배치

