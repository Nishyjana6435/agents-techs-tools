"""Request dependencies: authentication, RBAC helpers and rate limiting."""

from __future__ import annotations

from functools import lru_cache

import jwt
from fastapi import Depends, Header, HTTPException, Request, status

from assistant.auth import Permission, UserContext, decode_token, is_allowed
from assistant.config import get_settings
from assistant.ratelimit import RateLimiter


@lru_cache
def get_rate_limiter() -> RateLimiter:
    s = get_settings()
    return RateLimiter(capacity=s.rate_limit_capacity, refill_per_second=s.rate_limit_refill_per_second)


@lru_cache
def get_login_limiter() -> RateLimiter:
    # Separate, stricter bucket per client IP to slow down credential guessing.
    return RateLimiter(capacity=10, refill_per_second=0.1)


async def get_current_user(authorization: str | None = Header(default=None)) -> UserContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        return decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from None
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None


def require_permission(permission: Permission):
    async def checker(user: UserContext = Depends(get_current_user)) -> UserContext:
        if not is_allowed(user.role, permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Role '{user.role.value}' lacks permission '{permission.value}'"
            )
        return user

    return checker


async def rate_limited(request: Request, user: UserContext = Depends(get_current_user)) -> UserContext:
    """Token-bucket check per user. On exhaustion returns 429 with Retry-After (graceful, explicit)."""
    decision = await get_rate_limiter().check(user.username)
    request.state.rate_remaining = decision.remaining
    if not decision.allowed:
        retry = max(1, int(decision.retry_after_seconds + 0.999))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": "Rate limit exceeded. Please wait before sending another message.",
                "retry_after_seconds": retry,
            },
            headers={"Retry-After": str(retry)},
        )
    return user
