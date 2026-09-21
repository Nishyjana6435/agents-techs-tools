"""Tool layer.

``registry`` is the *only* way the graph executes a tool. It enforces, in order:

1. RBAC       - the caller's role must hold the tool's ``Permission`` (see ``auth/rbac.py``)
2. Approval   - tools marked ``requires_approval`` are only executed when the graph passes an
                approval token obtained from the human-in-the-loop node
3. Validation - parameters are validated against the tool's pydantic schema
4. Timeout    - every tool runs under ``asyncio.wait_for``
5. Exceptions - anything raised becomes a structured ``ToolResult`` error, never a crash

The LLM never sees a tool it is not allowed to use (``available_for(user)`` filters the list that is
rendered into prompts) *and* the registry re-checks at execution time. Two independent controls.
"""
from assistant.tools.registry import ToolRegistry, ToolResult, ToolSpec, get_tool_registry

__all__ = ["ToolRegistry", "ToolResult", "ToolSpec", "get_tool_registry"]
