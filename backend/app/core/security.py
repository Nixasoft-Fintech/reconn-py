import hashlib
import jwt

from datetime import datetime, timedelta, timezone
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)

    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }

    return jwt.encode(
        payload,
        settings.ACCESS_TOKEN_SECRET_KEY,
        algorithm="HS256",
    )


def create_refresh_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)

    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
    }

    return jwt.encode(
        payload,
        settings.REFRESH_TOKEN_SECRET_KEY,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.ACCESS_TOKEN_SECRET_KEY,
        algorithms=["HS256"],
    )


def decode_refresh_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.REFRESH_TOKEN_SECRET_KEY,
        algorithms=["HS256"],
    )
