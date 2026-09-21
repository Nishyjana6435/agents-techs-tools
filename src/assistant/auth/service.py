"""User store + JWT session handling.

Passwords are PBKDF2-hashed even though users are hardcoded: it costs nothing and it means the
repo never contains plaintext credentials in code paths (only in this demo seed table, which is
documented in the README).
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import jwt

from assistant.auth.models import Role, User, UserContext
from assistant.config import get_settings

_SALT = b"meridian-poc-salt"


def _hash_password(password: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), _SALT, 50_000).hex()


# Demo users. Passwords are listed in README.md for the evaluator.
_USERS: dict[str, User] = {
    u.username: u
    for u in [
        User(
            username="viewer",
            display_name="Vera Viewer",
            role=Role.VIEWER,
            department="retail_banking",
            clearance="internal",
            password_hash=_hash_password("viewer123"),
        ),
        User(
            username="analyst",
            display_name="Aiden Analyst",
            role=Role.ANALYST,
            department="payments",
            clearance="confidential",
            password_hash=_hash_password("analyst123"),
        ),
        User(
            username="admin",
            display_name="Ada Admin",
            role=Role.ADMIN,
            department="platform_engineering",
            clearance="restricted",
            password_hash=_hash_password("admin123"),
        ),
    ]
}


def get_user(username: str) -> User | None:
    return _USERS.get(username)


def authenticate(username: str, password: str) -> UserContext | None:
    user = _USERS.get(username)
    if user is None:
        # Run the hash anyway so timing does not reveal whether the user exists.
        _hash_password(password)
        return None
    if not hmac.compare_digest(user.password_hash, _hash_password(password)):
        return None
    return UserContext(**user.model_dump(exclude={"password_hash"}))


def create_token(user: UserContext) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user.username,
        "name": user.display_name,
        "role": user.role.value,
        "dept": user.department,
        "clr": user.clearance,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.jwt_ttl_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> UserContext:
    """Raises ``jwt.PyJWTError`` on any problem; the API layer maps that to 401."""
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    return UserContext(
        username=payload["sub"],
        display_name=payload.get("name", payload["sub"]),
        role=Role(payload["role"]),
        department=payload.get("dept", "unknown"),
        clearance=payload.get("clr", "internal"),
    )
