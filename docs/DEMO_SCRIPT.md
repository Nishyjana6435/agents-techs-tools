# Demo script (45 minutes)

Preparation: `.env` with `ANTHROPIC_API_KEY`, `PINECONE_API_KEY`, `LANGSMITH_API_KEY` (and optionally
`VOYAGE_API_KEY`). `make api` and `make ui`. Open the LangSmith project side by side.

| Min | Segment | What to show |
|---|---|---|
| 0-3 | Architecture | `docs/architecture.png`; explain nodes and streams. |
| 3-8 | Login as **viewer** | Sidebar: role, permissions, tool list with 🚫 on MCP/analysis. `/health` shows providers. |
| 8-13 | Retrieval | "What is the procedure for certificate rotation?" Watch guard -> memory -> supervisor rationale -> retrieval details (dense/sparse/RRF/rerank, namespaces, filters) -> streamed answer -> validation -> memory update. Open Sources: attribution + why. Open the LangSmith trace. |
| 13-16 | Memory | Follow-up: "Which incident led to that counter-party confirmation step?" - history and long-term profile visible in memory_load event. |
| 16-19 | RBAC at agent level | Viewer: "Who owns paycore-gateway and who is on call?" - supervisor proposes MCP tools, RBAC strips them (security events), falls back to retrieval and says the tools need a higher role. Restricted doc question ("PROJECT HARBOUR") returns nothing for viewer. |
| 19-27 | Login as **analyst** - RLM | "Summarize all outage reports related to payment failures during the last year and identify recurring root causes." Show the Python plan code, concurrent sub-agent batches, recursive aggregation depth 1 -> 2, python_analysis counts, final synthesis with citations. LangSmith: nested spans for each sub-agent. |
| 27-30 | MCP tools | Same ownership question as analyst: `mcp.get_service`, `mcp.who_is_on_call` run; answer combines them. |
| 30-34 | Security | Injection attempt blocked at guard; then ask about "observability vendor demo" and show the quarantined chunk event. |
| 34-38 | Login as **admin** - HITL | "Escalate INC-2025-0419 to the reliability review because the root cause is recurring." Graph pauses; Approve; tool runs; `/admin/audit` shows the record. Repeat with Reject. |
| 38-41 | Failure handling | Set an invalid `ANTHROPIC_API_KEY` or `LLM_TIMEOUT_SECONDS=0.001` and ask a question: supervisor fallback, response fallback, `degraded` flag, errors listed; rate limit: spam 25 messages -> 429 with Retry-After. |
| 41-45 | Assumptions & trade-offs | Walk `docs/ASSUMPTIONS.md`; feedback thumbs -> LangSmith feedback. |
