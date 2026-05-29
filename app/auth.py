import os

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SESSION_COOKIE = "iqol_session"
_MAX_AGE = 7 * 24 * 3600  # 7 days


def _serializer() -> URLSafeTimedSerializer:
    secret = os.getenv("SESSION_SECRET", "dev-secret-change-me")
    return URLSafeTimedSerializer(secret)


def _is_secure() -> bool:
    return bool(os.getenv("RAILWAY_ENVIRONMENT"))


def create_session_cookie(user_id: int) -> str:
    return _serializer().dumps(user_id, salt="iqol-session")


def read_session_cookie(token: str) -> int | None:
    try:
        return _serializer().loads(token, salt="iqol-session", max_age=_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


async def current_user(request: Request) -> dict:
    token = request.cookies.get(_SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user_id = read_session_cookie(token)
    if user_id is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    from .db import get_user_by_id
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return dict(user)
