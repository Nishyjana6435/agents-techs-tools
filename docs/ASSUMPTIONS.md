# Assumptions and trade-offs

| Decision | Alternative considered | Why this choice |
|---|---|---|
| Hardcoded users + JWT (Option A) | Keycloak | Zero infrastructure for the POC; JWT keeps the API stateless; RBAC matrix is data and identical either way. |
| Anthropic Claude (Opus 5 primary, Haiku 4.5 worker) | OpenAI, Gemini | Strong grounded synthesis and instruction following; two-tier split keeps RLM affordable. OpenAI supported behind a flag. |
| Offline mock provider | Require keys | The whole system (RBAC, guardrails, RLM control flow, UI) is testable in CI without secrets; degrades gracefully to it. |
| Pinecone with in-memory fallback | Pinecone only | Same code path; fallback implements the same filter language so tests are faithful. |
| BM25 local, not Pinecone sparse vectors | Pinecone hybrid index | Works identically offline; identifier-aware tokenisation; contained swap later. |
| Namespaces per department | Single namespace | Demonstrates namespace fan-out and lets tenants/departments be isolated or deleted independently. |
| RRF fusion | Score interpolation | Robust to score-scale differences; one intuitive knob. |
| LLM listwise reranker | Cross-encoder model | No model download; provider-agnostic; visible in traces; lexical fallback. |
| Regex injection detection + structural framing + capability limits | LLM classifier only | Deterministic and explainable first layer; the classifier can be added in the `guard` node. |
| Checkpointer + JSON long-term memory | Vector memory store | Simple, inspectable, sufficient for the requirement; interfaces map to Redis/Postgres. |
| Subprocess sandbox for Python | Container/microVM | Real timeout kill and process isolation with zero infra; documented as POC-grade. |
| In-process MCP by default, HTTP in Compose | HTTP only | Zero-config local dev while still using real MCP protocol; Compose shows the networked topology. |
| Streamlit re-render of the activity log as markdown | Custom component | Simplest thing that updates live; UI beauty is explicitly not a goal. |

## Known limitations
* Voyage's free tier allows only a few embedding requests per minute; the coalescing embedder, query cache and backoff keep the demo working, but a paid tier (or OpenAI embeddings) is needed for real traffic.
* The hashed local embedder is a stand-in; configure Voyage or OpenAI for real semantic recall.
* `InMemorySaver` and in-process rate limiting are single-replica; use Postgres/Redis for horizontal scale.
* Regex-based injection detection has false negatives; treat it as one layer of several.
* The mock LLM produces structurally correct but shallow answers; it exists to exercise the platform.
