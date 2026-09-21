from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from assistant.api.deps import get_current_user, get_login_limiter
from assistant.auth import UserContext, authenticate, create_token, permissions_for
from assistant.logging import get_logger

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger(__name__)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    token: str
    user: UserContext
    permissions: list[str]


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    ip = request.client.host if request.client else "unknown"
    decision = await get_login_limiter().check(f"login:{ip}")
    if not decision.allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts", headers={"Retry-After": str(int(decision.retry_after_seconds) + 1)})
    user = authenticate(body.username, body.password)
    if user is None:
        log.warning("login_failed", username=body.username, ip=ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    log.info("login_ok", username=user.username, role=user.role.value)
    return LoginResponse(token=create_token(user), user=user, permissions=sorted(p.value for p in permissions_for(user.role)))


@router.get("/me")
async def me(user: UserContext = Depends(get_current_user)) -> dict:
    return {"user": user, "permissions": sorted(p.value for p in permissions_for(user.role))}
