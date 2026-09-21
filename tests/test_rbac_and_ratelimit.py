from assistant.auth import (
    Permission,
    Role,
    authenticate,
    create_token,
    decode_token,
    is_allowed,
    permissions_for,
)
from assistant.ratelimit import RateLimiter, TokenBucket
from assistant.tools import get_tool_registry


def test_permission_matrix():
    assert is_allowed(Role.VIEWER, Permission.KNOWLEDGE_SEARCH)
    assert not is_allowed(Role.VIEWER, Permission.MCP_TOOLS)
    assert not is_allowed(Role.VIEWER, Permission.ADMIN_TOOLS)
    assert is_allowed(Role.ANALYST, Permission.MCP_TOOLS)
    assert is_allowed(Role.ANALYST, Permission.PYTHON_ANALYSIS)
    assert not is_allowed(Role.ANALYST, Permission.ADMIN_TOOLS)
    assert permissions_for(Role.ADMIN) == frozenset(Permission)


def test_auth_roundtrip():
    user = authenticate("analyst", "analyst123")
    assert user and user.role == Role.ANALYST
    assert authenticate("analyst", "nope") is None
    assert authenticate("ghost", "x") is None
    decoded = decode_token(create_token(user))
    assert decoded.username == "analyst" and decoded.clearance == "confidential"


def test_clearance_ordering(viewer, admin):
    assert viewer.can_read_level("internal") and not viewer.can_read_level("confidential")
    assert admin.can_read_level("restricted")
    assert viewer.readable_levels == ["public", "internal"]


async def test_registry_enforces_rbac_and_approval(viewer, analyst, admin):
    reg = await get_tool_registry()
    denied = await reg.execute("mcp.get_service", {"name": "paycore-gateway"}, viewer)
    assert denied.denied and not denied.ok
    ok = await reg.execute("mcp.get_service", {"name": "paycore-gateway"}, analyst)
    assert ok.ok and ok.output["owner_name"] == "Priya Raman"
    needs = await reg.execute("escalate_incident", {"incident_id": "INC-2025-0419", "note": "test"}, admin)
    assert needs.needs_approval and not needs.ok
    analyst_admin = await reg.execute(
        "escalate_incident", {"incident_id": "INC-2025-0419", "note": "test"}, analyst, approved=True
    )
    assert analyst_admin.denied
    unknown = await reg.execute("does_not_exist", {}, admin)
    assert not unknown.ok


async def test_python_tool_sandbox(analyst):
    reg = await get_tool_registry()
    good = await reg.execute("python_analysis", {"code": "result = sum(data)", "data": [1, 2, 3]}, analyst)
    assert good.ok and good.output["result"] == 6
    bad = await reg.execute("python_analysis", {"code": "import os\nresult = os.getcwd()"}, analyst)
    assert not bad.ok and "not allowed" in bad.error


def test_token_bucket_semantics():
    bucket = TokenBucket(capacity=3, refill_per_second=1.0)
    now = 100.0
    assert all(bucket.try_consume(now=now).allowed for _ in range(3))
    blocked = bucket.try_consume(now=now)
    assert not blocked.allowed and 0 < blocked.retry_after_seconds <= 1.0
    assert bucket.try_consume(now=now + 1.05).allowed  # one token refilled


async def test_rate_limiter_per_user():
    limiter = RateLimiter(capacity=2, refill_per_second=0.0)
    assert (await limiter.check("a")).allowed and (await limiter.check("a")).allowed
    assert not (await limiter.check("a")).allowed
    assert (await limiter.check("b")).allowed  # independent bucket
