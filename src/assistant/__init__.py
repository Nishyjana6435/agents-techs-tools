"""Enterprise AI Assistant for Meridian Commercial Bank (fictional).

Package layout (each subpackage has its own docstring explaining design decisions):

- ``config``     : environment-driven settings, single source of truth for knobs
- ``logging``    : structured JSON logging via structlog
- ``auth``       : users, roles, JWT sessions and the RBAC permission matrix
- ``ratelimit``  : per-user token-bucket limiter
- ``security``   : prompt-injection detection, input validation, output guardrails
- ``retrieval``  : chunking, embeddings, BM25, Pinecone / in-memory store, hybrid fusion, reranking
- ``tools``      : tool registry with RBAC enforcement, knowledge search, python analysis, MCP bridge
- ``mcp_server`` : a tiny MCP server exposing dummy enterprise data
- ``memory``     : short-term (checkpointer) and long-term (user profile) memory
- ``graph``      : the LangGraph multi-agent orchestration (supervisor, retrieval, research/RLM, response, validator, HITL)
- ``api``        : FastAPI application with SSE streaming
"""
