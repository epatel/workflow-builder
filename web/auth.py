"""Password hashing, JWT session cookies, role gating."""
import os
from datetime import datetime, timedelta, timezone

from fastapi import Request, HTTPException, Depends
from jose import jwt, JWTError
from passlib.context import CryptContext

from db import connect

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"
SESSION_DAYS = 7
COOKIE = "session"

# admin > editor > user, as an int ladder. ponytail: not a permissions matrix.
ROLE_RANK = {"user": 0, "editor": 1, "admin": 2}

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(p: str) -> str:
    return pwd.hash(p)


def verify_password(p: str, h: str) -> bool:
    return pwd.verify(p, h)


def make_session(user_id: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    return jwt.encode({"sub": str(user_id), "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)


def _user_from_request(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        uid = int(jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    finally:
        conn.close()
    if row is None or row["status"] != "active":
        return None
    return row


def current_user(request: Request):
    """Dependency: 401 if not logged in (or blocked)."""
    user = _user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def optional_user(request: Request):
    """Dependency: returns the user row or None."""
    return _user_from_request(request)


def require_role(minimum: str):
    """Dependency factory: user must rank >= minimum."""
    def dep(user=Depends(current_user)):
        if ROLE_RANK[user["role"]] < ROLE_RANK[minimum]:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return dep
