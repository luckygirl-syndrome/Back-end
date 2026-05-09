# 로컬 개발 환경 세팅

## 1. `.env` 파일 생성

프로젝트 루트에 `.env` 파일 생성:

```
APP_ENV=local
POSTGRES_USER=ttobaba
POSTGRES_PASSWORD=비밀번호
POSTGRES_DB=ttobaba
DATABASE_URL=postgresql+psycopg://ttobaba:비밀번호@127.0.0.1:5432/ttobaba
SECRET_KEY=아무거나
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
GOOGLE_CLIENT_ID=발급받은_클라이언트_ID
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
SENTRY_DSN=
POSTHOG_PROJECT_TOKEN=
POSTHOG_HOST=https://us.i.posthog.com
```

> `GOOGLE_CLIENT_ID` 는 팀 노션 또는 팀원에게 문의

## 2. 패키지 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. DB + Redis 실행

```bash
docker-compose up postgres redis -d
```

## 4. DB 마이그레이션

```bash
psql -U ttobaba -d ttobaba -f scripts/migrate_add_social_login.sql
```

## 5. 서버 실행

```bash
uvicorn app.main:app --reload --port 8001
```

## 6. API 문서 확인

```
http://127.0.0.1:8001/docs
```
