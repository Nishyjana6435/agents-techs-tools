from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from assistant.auth.models import UserContext
from assistant.auth.rbac import Permission, is_allowed
from assistant.config import get_settings
from assistant.logging import get_logger
from assistant.security.validation import ValidationError, validate_tool_params

log = get_logger(__name__)

Handler = Callable[[BaseModel, UserContext], Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    permission: Permission
    params_schema: type[BaseModel]
    handler: Handler
    requires_approval: bool = False
    timeout_seconds: float | None = None
    category: str = "general"

    def render(self) -> str:
        """Compact description rendered into agent prompts."""
        fields = ", ".join(
            f"{name}: {info.annotation.__name__ if hasattr(info.annotation, '__name__') else info.annotation}"
            for name, info in self.params_schema.model_fields.items()
        )
        flag = " [requires human approval]" if self.requires_approval else ""
        return f"- {self.name}({fields}): {self.description}{flag}"


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: Any = None
    error: str | None = None
    denied: bool = False
    needs_approval: bool = False
    duration_ms: int = 0
    params: dict[str, Any] = field(default_factory=dict)

    def to_state(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "denied": self.denied,
            "needs_approval": self.needs_approval,
            "duration_ms": self.duration_ms,
            "params": self.params,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self.audit_log: list[dict[str, Any]] = []

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def available_for(self, user: UserContext) -> list[ToolSpec]:
        return [t for t in self._tools.values() if is_allowed(user.role, t.permission)]

    def render_for(self, user: UserContext) -> str:
        tools = self.available_for(user)
        return "\n".join(t.render() for t in tools) if tools else "(no tools available for this role)"

    def is_allowed(self, user: UserContext, name: str) -> bool:
        spec = self._tools.get(name)
        return bool(spec) and is_allowed(user.role, spec.permission)

    async def execute(self, name: str, params: dict[str, Any], user: UserContext, *, approved: bool = False) -> ToolResult:
        started = time.perf_counter()
        spec = self._tools.get(name)
        result: ToolResult
        if spec is None:
            result = ToolResult(tool=name, ok=False, error=f"Unknown tool '{name}'", params=params)
        elif not is_allowed(user.role, spec.permission):
            log.warning("tool_denied", tool=name, user=user.username, role=user.role.value, needs=spec.permission.value)
            result = ToolResult(
                tool=name,
                ok=False,
                denied=True,
                error=f"Role '{user.role.value}' is not permitted to use '{name}' (requires {spec.permission.value}).",
                params=params,
            )
        elif spec.requires_approval and not approved:
            result = ToolResult(tool=name, ok=False, needs_approval=True, error="Human approval required before execution.", params=params)
        else:
            result = await self._run(spec, params, user)
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        self.audit_log.append(
            {"ts": time.time(), "user": user.username, "role": user.role.value, "tool": name, "ok": result.ok, "denied": result.denied, "error": result.error}
        )
        return result

    async def _run(self, spec: ToolSpec, params: dict[str, Any], user: UserContext) -> ToolResult:
        try:
            validated = validate_tool_params(spec.params_schema, params)
        except ValidationError as exc:
            return ToolResult(tool=spec.name, ok=False, error=str(exc), params=params)
        timeout = spec.timeout_seconds or get_settings().tool_timeout_seconds
        try:
            output = await asyncio.wait_for(spec.handler(validated, user), timeout=timeout)
            return ToolResult(tool=spec.name, ok=True, output=output, params=validated.model_dump())
        except TimeoutError:
            log.error("tool_timeout", tool=spec.name, timeout=timeout)
            return ToolResult(tool=spec.name, ok=False, error=f"Tool '{spec.name}' timed out after {timeout:.0f}s", params=params)
        except ValueError as exc:
            # Expected rejections (sandbox policy, bad identifiers) - no traceback needed.
            log.warning("tool_rejected", tool=spec.name, error=str(exc)[:200])
            return ToolResult(tool=spec.name, ok=False, error=f"Tool '{spec.name}' rejected the request: {exc}", params=params)
        except Exception as exc:  # noqa: BLE001 - tools are untrusted code paths; contain everything
            log.exception("tool_failed", tool=spec.name)
            return ToolResult(tool=spec.name, ok=False, error=f"Tool '{spec.name}' failed: {exc.__class__.__name__}: {exc}", params=params)


_registry: ToolRegistry | None = None
_lock = asyncio.Lock()


async def get_tool_registry() -> ToolRegistry:
    """Build the registry once, discovering MCP tools dynamically."""
    global _registry
    async with _lock:
        if _registry is None:
            from assistant.tools.builtin import register_builtin_tools
            from assistant.tools.mcp_bridge import register_mcp_tools

            reg = ToolRegistry()
            register_builtin_tools(reg)
            await register_mcp_tools(reg)
            _registry = reg
    return _registry
