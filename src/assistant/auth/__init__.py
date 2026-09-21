"""Authentication and authorization.

Option A from the brief: hardcoded users and roles. Sessions are stateless JWTs so the API stays
horizontally scalable. The permission matrix (``rbac.py``) is the single place that decides which
role may call which tool; both the API layer and the tool registry consult it, so the agent can
never bypass it by "deciding" to call a tool: the registry refuses before the tool code runs.
"""
from assistant.auth.models import Role, User, UserContext
from assistant.auth.rbac import Permission, is_allowed, permissions_for
from assistant.auth.service import authenticate, create_token, decode_token, get_user

__all__ = [
    "Role",
    "User",
    "UserContext",
    "Permission",
    "is_allowed",
    "permissions_for",
    "authenticate",
    "create_token",
    "decode_token",
    "get_user",
]
