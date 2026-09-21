"""Bridge between the tool registry and the MCP server.

At startup we connect to the MCP server, list its tools and register each one as ``mcp.<name>``
guarded by ``Permission.MCP_TOOLS``. Parameter validation uses a pydantic model generated from
the MCP tool's JSON schema, so MCP tools get the same validation path as built-ins.

Connection mode is decided by ``Settings.mcp_server_url``: ``None`` means in-process
(the ``MCPServer`` object is passed straight to the client), a URL means Streamable HTTP.
Every call opens a fresh session with a timeout; if the server is down the tool returns a
structured error and the graph carries on without it.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field, create_model

from assistant.auth.models import UserContext
from assistant.auth.rbac import Permission
from assistant.config import get_settings
from assistant.logging import get_logger
from assistant.tools.registry import ToolRegistry, ToolSpec

log = get_logger(__name__)

_JSON_TYPES: dict[str, type] = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}


def _model_from_schema(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    props = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    fields: dict[str, Any] = {}
    for prop, spec in props.items():
        json_type = spec.get("type")
        if isinstance(json_type, list):  # e.g. ["string", "null"]
            json_type = next((t for t in json_type if t != "null"), "string")
        if json_type is None and "anyOf" in spec:
            json_type = next((o.get("type") for o in spec["anyOf"] if o.get("type") not in (None, "null")), "string")
        py = _JSON_TYPES.get(json_type, str)
        desc = spec.get("description", "")
        if prop in required:
            fields[prop] = (py, Field(description=desc))
        else:
            fields[prop] = (py | None, Field(default=None, description=desc))
    return create_model(f"Mcp_{name}_Params", **fields)  # type: ignore[call-overload]


def _server_target():
    settings = get_settings()
    if settings.mcp_server_url:
        return settings.mcp_server_url
    from assistant.mcp_server.server import server

    return server


async def list_mcp_tools() -> list[Any]:
    from mcp.client.client import Client

    async with Client(_server_target()) as client:
        return (await client.list_tools()).tools


async def call_mcp_tool(name: str, arguments: dict[str, Any]) -> Any:
    from mcp.client.client import Client

    async with Client(_server_target()) as client:
        result = await client.call_tool(name, arguments)
    if result.is_error:
        text = " ".join(getattr(c, "text", "") for c in result.content)
        raise RuntimeError(f"MCP tool error: {text[:300]}")
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    texts = [getattr(c, "text", str(c)) for c in result.content]
    # MCP servers commonly return JSON as text; parse it so tools get structured data back.
    parsed = []
    for t in texts:
        try:
            parsed.append(json.loads(t))
        except (json.JSONDecodeError, TypeError):
            parsed.append(t)
    return parsed[0] if len(parsed) == 1 else parsed


def _make_handler(tool_name: str):
    async def handler(params: BaseModel, user: UserContext) -> Any:
        args = {k: v for k, v in params.model_dump().items() if v is not None}
        return await call_mcp_tool(tool_name, args)

    return handler


async def register_mcp_tools(reg: ToolRegistry) -> int:
    settings = get_settings()
    try:
        tools = await asyncio.wait_for(list_mcp_tools(), timeout=settings.tool_timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        log.error("mcp_discovery_failed", error=str(exc)[:200], mode="http" if settings.mcp_server_url else "in-process")
        return 0
    for t in tools:
        reg.register(
            ToolSpec(
                name=f"mcp.{t.name}",
                description=(t.description or "").strip().splitlines()[0] if t.description else f"MCP tool {t.name}",
                permission=Permission.MCP_TOOLS,
                params_schema=_model_from_schema(t.name, t.inputSchema if hasattr(t, "inputSchema") else t.input_schema),
                handler=_make_handler(t.name),
                category="mcp",
            )
        )
    log.info("mcp_tools_registered", count=len(tools), tools=[t.name for t in tools])
    return len(tools)
