# Memory design

## Requirements
Keep user context, previous questions and relevant history across turns; survive the session; explain
the decisions.

## Layers

### 1. Working memory - LangGraph checkpointer
Every conversation has a `thread_id`. The graph is compiled with a checkpointer (`InMemorySaver` in the
POC; `AsyncSqliteSaver`/`AsyncPostgresSaver` are drop-in for production). After each node the full
`AssistantState` is persisted, so the next turn starts with the complete message history, last evidence and
route. The same mechanism makes human-in-the-loop possible: the graph pauses at `interrupt()`, the API returns,
and a later `POST /chat/resume` continues from the checkpoint.

Why: the checkpointer is the idiomatic LangGraph store, it is per-thread by construction (no cross-talk between
users), it is visible in LangSmith, and swapping the backend is one line.

### 2. Rolling summary
When a thread exceeds `MAX_HISTORY_TURNS` messages, `memory_load` compacts older turns into
`conversation_summary` deterministically (question + first line of each answer). Prompts always receive
`<history_summary>` + the recent `<history>` window.

Why deterministic rather than LLM-generated: it is free, instant, cannot fail and cannot hallucinate; the
supervisor only needs enough to resolve references like "the second incident you mentioned".

### 3. Long-term memory - per-user profile
`memory/long_term.py` keeps one JSON file per user with topic interest counts, departments queried, recent
questions, answer-style preference, turn count, and feedback. `memory_load` renders it as a short sentence
("3 previous turns. Frequent topics: incidents (2), payments (1). Prefers concise answers. Recent questions: ...")
injected into the supervisor and response prompts. `memory_update` writes after every answer and emits
`memory` events so the panel shows exactly what was learned.

Why file-backed JSON: durable across restarts and sessions, trivially inspectable, and the interface
(`get/record_turn/record_feedback/render`) maps directly onto a Redis hash or Postgres row later.

### 4. Feedback loop
Thumbs up/down from the UI are stored in the profile and forwarded to LangSmith as `user_score` feedback on
the exact `run_id`, so answer quality can be evaluated per trace.

## What is deliberately *not* remembered
Raw evidence text is not copied into long-term memory (it may be above a future reader's clearance); only
topics and question text are kept. Threads are owned by their creator; the API refuses cross-user access.
