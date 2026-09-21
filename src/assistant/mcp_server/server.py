"""MCP server exposing dummy enterprise data.

Runs two ways:
* **In-process** (default for the POC): the agent's MCP client connects directly to the
  ``MCPServer`` object over an in-memory transport. Zero network, still real MCP protocol.
* **Standalone** (``python -m assistant.mcp_server.server``): serves Streamable HTTP on
  ``http://localhost:8100/mcp`` for the Docker Compose topology; set ``MCP_SERVER_URL`` in the API.

Tools are intentionally read-only lookups; the "admin" style action lives in the agent's own
tool registry where RBAC + human approval can gate it.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from assistant.mcp_server.data import EMPLOYEES, INCIDENTS, SERVICES

server = MCPServer(
    name="meridian-enterprise-data",
    instructions="Read-only enterprise directory, service catalog and incident records for Meridian Commercial Bank.",
)


@server.tool()
def lookup_employee(query: str) -> list[dict]:
    """Find employees by name, title, department or id (case-insensitive substring match)."""
    q = query.lower().strip()
    return [e for e in EMPLOYEES if q in " ".join(str(v) for v in e.values() if v).lower()][:10]


@server.tool()
def get_service(name: str) -> dict:
    """Get a service catalog entry by service name (e.g. 'paycore-gateway'). Includes owner and runbook."""
    n = name.lower().strip().replace(" ", "-")
    for s in SERVICES:
        if s["name"] == n or n in s["display_name"].lower():
            owner = next((e for e in EMPLOYEES if e["id"] == s["owner"]), None)
            return {
                **s,
                "owner_name": owner["name"] if owner else None,
                "owner_email": owner["email"] if owner else None,
            }
    return {"error": f"service '{name}' not found", "known_services": [s["name"] for s in SERVICES]}


@server.tool()
def list_services(department: str | None = None, tier: str | None = None) -> list[dict]:
    """List services, optionally filtered by department (e.g. 'payments') or tier ('tier-0'..'tier-3')."""
    out = SERVICES
    if department:
        out = [s for s in out if s["department"] == department.lower()]
    if tier:
        out = [s for s in out if s["tier"] == tier.lower()]
    return [
        {k: s[k] for k in ("name", "display_name", "tier", "department", "owner", "runbook")} for s in out
    ]


@server.tool()
def search_incidents(
    service: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    since: str | None = None,
) -> list[dict]:
    """Search incident records. Filters: service name, severity (SEV-1/2/3), status (open/closed), since (YYYY-MM-DD)."""
    out = INCIDENTS
    if service:
        out = [i for i in out if i["service"] == service.lower().strip()]
    if severity:
        out = [i for i in out if i["severity"].upper() == severity.upper().strip()]
    if status:
        out = [i for i in out if i["status"] == status.lower().strip()]
    if since:
        out = [i for i in out if i["opened"] >= since]
    return out


@server.tool()
def who_is_on_call(department: str | None = None) -> list[dict]:
    """List employees currently marked on-call, optionally for one department."""
    out = [e for e in EMPLOYEES if e["on_call"]]
    if department:
        out = [e for e in out if e["department"] == department.lower()]
    return [{k: e[k] for k in ("id", "name", "title", "department", "email")} for e in out]


if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.environ.get("MCP_PORT", "8100"))
    uvicorn.run(server.streamable_http_app(host="0.0.0.0"), host="0.0.0.0", port=port)
