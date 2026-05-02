import os
import pathlib
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# 현재 파일 위치 기준으로 .env 파일 경로 찾기
# ✅ pathlib.Path로 이름을 명확하게 써줍니다.
env_path = pathlib.Path(__file__).parent.parent.parent / ".env"

# ✅ override=True를 써서 시스템 환경변수를 무조건 덮어씌웁니다.
load_dotenv(dotenv_path=env_path, override=True)


class Settings(BaseSettings):
    PROJECT_NAME: str = "또바바"
    ENV: str = os.getenv("APP_ENV", "local")
    DATABASE_URL: str = os.getenv("DATABASE_URL")
    # .env 파일의 변수명과 일치하게 선언해줍니다.
    # 이렇게 선언만 해두면 Pydantic이 .env에서 값을 자동으로 찾아 넣어줍니다.
    SECRET_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    @property
    def db_engine_kwargs(self):
        return {
            "pool_pre_ping": True,
            "pool_recycle": 3600,
        }

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
