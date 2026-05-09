import redis
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.config import settings
from app.core.observability import init_sentry
from app.core.database import engine, Base, get_db

init_sentry(dsn=settings.SENTRY_DSN, env=settings.ENV)
from app.users.router import router as user_router
from app.users import models
from app.products import router as products_router
from app.chat import router as chat_router
from app.chat import repository as chat_repository
from app.chat import agent as chat_agent
from app.dashboard import home_router
from app.chat.after_chat.router import router as after_chat_router
from fastapi.middleware.cors import CORSMiddleware


# 서버 시작 시 테이블 생성
Base.metadata.create_all(bind=engine)


# ──────────────────────────────────────────────
# Lifespan: 서버 시작/종료 이벤트
# ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup:
    1. 비동기 Redis Connection Pool 초기화
    2. Gemini agent 초기화 (API 키 설정 + system_instruction L1 캐시)
    
    Shutdown:
    1. Redis pool 해제
    """
    # ── Startup ──
    await chat_repository.init_redis_pool()
    chat_agent.init_agent()
    print("🚀 [lifespan] 서버 초기화 완료")

    yield

    # ── Shutdown ──
    await chat_repository.close_redis_pool()
    print("🔌 [lifespan] 서버 종료 정리 완료")


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

origins = [
    "https://ttobaba-d8859.web.app/",
]

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(user_router)
app.include_router(products_router.router)
app.include_router(chat_router.router)
app.include_router(after_chat_router)
app.include_router(home_router.router)

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