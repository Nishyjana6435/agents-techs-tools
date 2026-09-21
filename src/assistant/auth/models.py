from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Role(StrEnum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    ADMIN = "admin"


# Document access levels are ordered: a user with clearance N can read levels <= N.
ACCESS_LEVELS: dict[str, int] = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


class User(BaseModel):
    username: str
    display_name: str
    role: Role
    department: str
    clearance: str = "internal"  # max document access_level this user may read
    password_hash: str = Field(repr=False)


class UserContext(BaseModel):
    """What flows through the request, the graph state and every tool call.

    Deliberately small and serialisable: it is stored inside LangGraph state so that tools
    executed deep inside the graph can still enforce RBAC and document-level access.
    """

    username: str
    display_name: str
    role: Role
    department: str
    clearance: str = "internal"

    def can_read_level(self, access_level: str | None) -> bool:
        level = ACCESS_LEVELS.get((access_level or "internal").lower(), ACCESS_LEVELS["internal"])
        return level <= ACCESS_LEVELS.get(self.clearance.lower(), 1)

    @property
    def readable_levels(self) -> list[str]:
        mine = ACCESS_LEVELS.get(self.clearance.lower(), 1)
        return [name for name, rank in ACCESS_LEVELS.items() if rank <= mine]
