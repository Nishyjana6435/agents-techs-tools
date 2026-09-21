# Interview notes: challenges, open questions and how I resolved them

Use these as talking points when asked "what was hard" or "what questions did you have".

## Challenges I hit while building

1. **Library churn.** The MCP Python SDK moved to 2.x during the project window: `FastMCP` was renamed `MCPServer`,
   result fields became snake_case (`structured_content`, `is_error`) and the client accepts a server object directly.
   I verified the real API in a REPL before writing code and built a small bridge so the rest of the system never touches
   the SDK directly. Same discipline for LangGraph 1.x (`InMemorySaver`, `get_stream_writer`, `interrupt`/`Command`).

2. **No credentials on the build machine.** I could not call Claude, Pinecone or LangSmith while building. Rather than
   stub tests, I made every external dependency pluggable with an offline fallback (deterministic mock LLM keyed on a
   `# task:` line in each prompt, hashed local embeddings, in-memory vector store implementing Pinecone's filter language).
   The whole graph, RBAC, guardrails and UI are exercised by 34 tests with zero keys. This became the graceful-degradation
   story as well.

3. **Streaming only the final answer.** LangGraph's `messages` stream emits tokens from *every* LLM call, including the
   supervisor's JSON and the reranker. I tag the response agent's call and filter on node name + tag in the API, so the
   user sees only the answer stream while the panel shows the rest as activity events.

4. **Keeping citations stable across recursive sub-agents.** If each RLM batch numbered its own evidence, `[3]` would
   mean different things in different batches and the final answer's citations would be wrong. I assign global evidence ids
   before batching and pass them into each sub-agent prompt, so citations survive aggregation and the validator can verify them.

5. **Comparing dense and sparse scores.** Cosine similarities and BM25 scores live on different scales. Interpolating them
   needs per-corpus tuning; reciprocal rank fusion only needs ranks and has one intuitive knob, so I used weighted RRF.

6. **Injection detection false positives.** My first rule set quarantined the assistant's own architecture document because
   "output guardrails for ... secrets" matched a "reveal secrets" pattern. I tightened rules to require a determiner
   ("your/the system prompt"), added negative test cases, and kept the quarantine threshold explicit. Lesson: pattern
   detectors need a benign-corpus regression suite.

7. **Human-in-the-loop plumbing.** `interrupt()` only works with a checkpointer and a stable `thread_id`, and the resume must
   come through the same thread with `Command(resume=...)`. I also had to guard against resuming a thread with no pending
   interrupt (409) and against one user resuming another user's thread (403).

8. **Serialisation in checkpoints.** Storing a Python enum (`Role`) inside graph state produced deserialisation warnings from
   the checkpointer. Fixed by always writing the user context in JSON mode so state is plain data.

9. **Python 3.9 system interpreter.** Modern LangChain needs 3.10+. I used `uv` to pin 3.12 per project without touching the
   system Python, and the same lockfile drives the Docker image.

10. **Making the agent unable to bypass RBAC.** It is not enough to hide tools in the prompt. I enforce at three points: the
    prompt only lists permitted tools, the supervisor node strips any proposed tool the role lacks (and emits a security event),
    and the registry re-checks at execution and requires an approval token for admin tools that only the approval node can supply.

11. **Rate limiting the right thing.** Counting invalid requests against the bucket is intentional: an attacker spamming
    malformed messages should still be throttled. Login has its own per-IP bucket.

## Questions I had in the middle, and the assumption I made

| Question | Decision |
|---|---|
| Which LLM? | Claude Opus 5 primary for routing/synthesis, Haiku 4.5 for sub-agents and reranking; OpenAI supported behind a flag. |
| Anthropic has no embedding model - what to use? | Voyage AI (Anthropic's recommended partner), OpenAI as alternative, hashed local fallback for offline. |
| What should a Pinecone namespace represent? | Department: it matches how access and ownership work in a bank and lets the supervisor narrow a query to one namespace or fan out. |
| Sparse vectors inside Pinecone or local BM25? | Local BM25 so hybrid behaves identically offline and identifiers tokenise the way I want; documented as a contained swap. |
| Option A (hardcoded users) or Keycloak? | Option A with JWT; the RBAC matrix and enforcement points are identical either way, and the POC needs no extra infrastructure. |
| How much to sandbox the Python tool? | AST allow-list plus a separate `python -I` process with hard kill. Called out as POC-grade; production would use a container/microVM. |
| What counts as "administrative" and needs approval? | Anything with side effects on the organisation (escalating an incident, reindexing). Read-only MCP lookups do not. |
| Should memory summaries use the LLM? | No: deterministic summaries are free, instant and cannot hallucinate; the supervisor only needs enough to resolve references. |
| Block or just flag suspicious inputs? | Weighted score: block at 0.7, flag and continue at 0.4; documents are quarantined at the flag threshold because they are never worth the risk. |
| How many rewrite attempts before giving up? | Two, then deliver a sanitised answer with a disclaimer. Unbounded loops are a cost and latency risk. |
| Where does the reranker come from? | Listwise grading by the worker LLM with a lexical fallback, so it is provider-agnostic and visible in the trace. |
| Should the viewer get a research answer at all? | Yes, but without the analytics tool; the panel says so explicitly. Degrade the capability, don't refuse the question. |

## What I would do next with more time
LLM-based injection classifier as a second opinion; Postgres checkpointer and Redis rate limiter for multi-replica;
Pinecone native sparse vectors; an evaluation set in LangSmith scored automatically on citation precision; container
sandbox for Python; Keycloak for SSO.
