"""LangGraph orchestration.

Nodes (each in ``nodes/``), in the order a typical request flows:

    guard -> memory_load -> supervisor -> {retrieval | research | tools | direct}
          -> response -> validator -> (rewrite -> validator)* -> memory_update -> END
    tools -> approval (interrupt, admin tools only) -> tools

* ``guard``        : input validation + prompt-injection screening (blocks or flags)
* ``memory_load``  : loads long-term profile + compacts long histories into a rolling summary
* ``supervisor``   : intent understanding, task decomposition, routing, metadata filter inference
* ``retrieval``    : single-shot hybrid RAG
* ``research``     : Recursive Language Model flow (plan -> batch -> analyse -> aggregate)
* ``tools``        : executes tool calls chosen by the supervisor through the RBAC registry
* ``approval``     : human-in-the-loop interrupt for admin tools
* ``response``     : grounded answer with citations (streamed)
* ``validator``    : output guardrails; loops to ``rewrite`` on failure (bounded)
* ``memory_update``: long-term memory write + activity summary

Every node emits ``ActivityEvent``s through LangGraph's custom stream so the UI shows what the
agent is doing in real time, and every node is a LangSmith span.
"""

from assistant.graph.builder import build_graph, get_graph

__all__ = ["build_graph", "get_graph"]
