import secrets
from datetime import datetime, timedelta
from typing import Optional

from jose import jwt, JWTError
import bcrypt

from app.core.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str):
    try:
        if token.startswith("Bearer "):
            token = token.replace("Bearer ", "")
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


def create_refresh_token(user_id: int) -> str:
    from app.core.redis_client import redis_client
    token = secrets.token_urlsafe(32)
    ttl = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
    redis_client.setex(f"refresh_token:{token}", ttl, str(user_id))
    return token


def verify_refresh_token(token: str) -> Optional[int]:
    from app.core.redis_client import redis_client
    value = redis_client.get(f"refresh_token:{token}")
    if not value:
        return None
    return int(value)


def revoke_refresh_token(token: str) -> None:
    from app.core.redis_client import redis_client
    redis_client.delete(f"refresh_token:{token}")