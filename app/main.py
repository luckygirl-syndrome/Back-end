import os
import redis
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.config import settings
from app.core.observability import init_sentry
from app.core.database import engine, Base, get_db

init_sentry(dsn=settings.SENTRY_DSN, env=settings.ENV)
from app.users.router import router as user_router
from app.users.auth_router import router as auth_router
from app.users.profile_router import router as profile_router
from app.users.setting_router import router as setting_router
from app.users import models
from app.products import router as products_router
from app.dashboard import home_router
from app.chat.router import router as chat_router
from app.notifications.router import router as notifications_router
from app.notifications.scheduler import start_scheduler, scheduler
from app.notifications import models as notification_models


# 서버 시작 시 테이블 생성
Base.metadata.create_all(bind=engine)

# create_all은 기존 테이블에 새 컬럼을 추가하지 않음 — 신규 컬럼 수동 반영 (idempotent)
with engine.connect() as _conn:
    _conn.execute(text("ALTER TABLE user_product ADD COLUMN IF NOT EXISTS scored_at TIMESTAMP"))
    _conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    scheduler.shutdown()


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# 라우터 등록
app.include_router(user_router)
app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(setting_router)
app.include_router(products_router.router)
app.include_router(chat_router)
app.include_router(home_router.router)
app.include_router(notifications_router)

import logging
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled Exception at {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"message": "Internal Server Error"},
    )

@app.get("/")
def root():
    return {"message": f"Welcome to {settings.PROJECT_NAME} API"}


@app.get("/privacy")
def privacy_policy():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "static", "legal", "privacy.html"),
        media_type="text/html",
    )


# 헬스체크 API (DB + Redis)
@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection error: {str(e)}")

    try:
        r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0)
        r.ping()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Redis connection error: {str(e)}")

    return {"status": "ok", "db": "connected", "redis": "connected"}