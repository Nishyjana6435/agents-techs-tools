"""Role Based Access Control matrix.

Roles required by the brief and what they may do:

* viewer  : chat + knowledge search. No analytics, no MCP, no admin tools.
* analyst : viewer + python analysis + MCP enterprise data tools.
* admin   : everything, including administrative tools (which additionally require a
            human-in-the-loop approval step in the graph).

The matrix is data, not code paths, so it is trivially auditable and unit-testable.
"""

from __future__ import annotations

from enum import StrEnum

from assistant.auth.models import Role


class Permission(StrEnum):
    CHAT = "chat"
    KNOWLEDGE_SEARCH = "knowledge_search"
    PYTHON_ANALYSIS = "python_analysis"
    MCP_TOOLS = "mcp_tools"
    ADMIN_TOOLS = "admin_tools"
    VIEW_TRACES = "view_traces"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.CHAT, Permission.KNOWLEDGE_SEARCH}),
    Role.ANALYST: frozenset(
        {Permission.CHAT, Permission.KNOWLEDGE_SEARCH, Permission.PYTHON_ANALYSIS, Permission.MCP_TOOLS}
    ),
    Role.ADMIN: frozenset(Permission),
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


def is_allowed(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
