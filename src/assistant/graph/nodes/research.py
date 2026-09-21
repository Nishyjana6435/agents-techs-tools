"""Research agent - Recursive Language Model (RLM) flow.

Instead of stuffing every matching document into one prompt, the agent:

1. **plan**      - the primary LLM writes a small Python program that builds a list of targeted
                   search steps (queries + metadata filters). The code runs in the restricted
                   executor; if it fails we fall back to a deterministic plan.
2. **explore**   - all search steps run concurrently through the RBAC'd knowledge_search tool;
                   results are de-duplicated and grouped into a document collection.
3. **batch**     - the collection is split into batches (documents stay whole within a batch).
4. **analyse**   - a worker LLM ("sub-agent") analyses each batch independently and concurrently,
                   citing global evidence ids. A failed batch is reported and skipped, never fatal.
5. **aggregate** - findings are merged recursively: groups of batch findings are aggregated, then
                   the aggregates are aggregated, until one synthesis remains (depth bounded by
                   ``rlm_max_depth``).
6. **quantify**  - when the role permits, the python_analysis tool computes structured stats
                   (incident counts per root-cause tag, per month) over the collection metadata.

Every step emits ``rlm`` activity events so the panel shows the recursion happening.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from assistant.config import get_settings
from assistant.graph.events import emit
from assistant.graph.prompts import RLM_AGGREGATE_SYSTEM, RLM_BATCH_SYSTEM, RLM_PLAN_SYSTEM, render_evidence
from assistant.graph.resilience import resilient
from assistant.graph.state import AssistantState, user_from_state
from assistant.llm import LLMError, get_llm, llm_call
from assistant.llm.provider import message_text
from assistant.tools import get_tool_registry
from assistant.tools.safe_python import UnsafeCodeError, run_plan_code

settings = get_settings()


def _fallback(state: AssistantState, exc: Exception) -> dict[str, Any]:
    return {
        "evidence": state.get("evidence", []),
        "research_findings": f"Research agent failed ({exc.__class__.__name__}); answering from any evidence gathered so far.",
        "degraded": True,
    }


# ----------------------------------------------------------------------------------- 1. plan
def _deterministic_plan(
    question: str, sub_questions: list[str], filters: dict[str, Any]
) -> list[dict[str, Any]]:
    steps = [{"query": q, **filters} for q in (sub_questions or [question])]
    if not any(s["query"] == question for s in steps):
        steps.append({"query": question, **filters})
    return steps[:6]


async def _plan(
    question: str, sub_questions: list[str], filters: dict[str, Any]
) -> tuple[list[dict[str, Any]], str, str]:
    """Return ``(plan, code, method)``."""
    try:
        prompt = f"question = {json.dumps(question)}\nsub_questions = {json.dumps(sub_questions)}\nfilters = {json.dumps(filters)}"
        msg = await llm_call(
            get_llm("primary"), [SystemMessage(content=RLM_PLAN_SYSTEM), HumanMessage(content=prompt)]
        )
        code = message_text(msg).strip()
        code = re.sub(r"^```(?:python)?\s*|\s*```$", "", code, flags=re.DOTALL)
        env = run_plan_code(code, {"question": question, "sub_questions": sub_questions, "filters": filters})
        plan = env.get("plan")
        if (
            not isinstance(plan, list)
            or not plan
            or not all(isinstance(s, dict) and s.get("query") for s in plan)
        ):
            raise ValueError("plan code did not produce a non-empty list of steps with queries")
        clean = []
        for s in plan[:6]:
            clean.append(
                {
                    k: s.get(k)
                    for k in ("query", "document_types", "department", "created_after", "created_before")
                    if s.get(k)
                }
            )
        return clean, code, "llm-python-plan"
    except (LLMError, UnsafeCodeError, ValueError, TypeError, KeyError) as exc:
        emit(
            "rlm",
            "research",
            f"plan generation failed ({exc.__class__.__name__}: {str(exc)[:120]}); using deterministic plan",
        )
        return _deterministic_plan(question, sub_questions, filters), "", "deterministic-fallback"


# ----------------------------------------------------------------------------------- 2. explore
async def _explore(plan: list[dict[str, Any]], user, registry) -> tuple[list[dict[str, Any]], list[str]]:
    async def one(step: dict[str, Any]):
        params = {**step, "top_k": 10}
        emit(
            "tool_call",
            "research",
            f"knowledge_search: {step['query'][:70]}",
            tool="knowledge_search",
            params=params,
        )
        return await registry.execute("knowledge_search", params, user)

    results = await asyncio.gather(*(one(s) for s in plan), return_exceptions=True)
    seen: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    for step, res in zip(plan, results, strict=True):
        if isinstance(res, Exception) or not res.ok:
            notes.append(
                f"search failed for '{step['query'][:40]}': {res if isinstance(res, Exception) else res.error}"
            )
            continue
        for q in res.output.get("quarantined", []):
            emit("security", "research", f"quarantined chunk with injection patterns: {q}")
        for ev in res.output["evidence"]:
            seen.setdefault(ev["chunk_id"], ev)
    # Order the collection chronologically then by document so batches are coherent.
    collection = sorted(seen.values(), key=lambda e: (e.get("created_date") or "", e["doc_id"], e["section"]))
    cap = settings.rlm_batch_size * settings.rlm_max_batches
    if len(collection) > cap:
        notes.append(f"collection truncated from {len(collection)} to {cap} chunks")
        collection = collection[:cap]
    return collection, notes


# ----------------------------------------------------------------------------------- 3. batch
def _batches(collection: list[dict[str, Any]]) -> list[list[int]]:
    """Return batches as lists of 1-based global evidence ids, keeping a document's chunks together."""
    by_doc: dict[str, list[int]] = {}
    for idx, ev in enumerate(collection, start=1):
        by_doc.setdefault(ev["doc_id"], []).append(idx)
    batches: list[list[int]] = []
    current: list[int] = []
    for ids in by_doc.values():
        if current and len(current) + len(ids) > settings.rlm_batch_size:
            batches.append(current)
            current = []
        current.extend(ids)
    if current:
        batches.append(current)
    return batches[: settings.rlm_max_batches]


# ----------------------------------------------------------------------------------- 4. analyse
async def _analyse_batch(
    batch_no: int,
    ids: list[int],
    collection: list[dict[str, Any]],
    question: str,
    sub_questions: list[str],
    sem: asyncio.Semaphore,
) -> tuple[int, str | None]:
    async with sem:
        evidence = [collection[i - 1] for i in ids]
        docs = sorted({e["title"] for e in evidence})
        emit(
            "rlm",
            "research",
            f"sub-agent batch {batch_no}: analysing {len(ids)} chunks from {len(docs)} document(s)",
            batch=batch_no,
            documents=docs,
            evidence_ids=ids,
        )
        prompt = (
            f"Main question: {question}\nSub-questions: {json.dumps(sub_questions)}\n\n"
            f"{render_evidence(evidence, max_chars_each=1500, ids=ids)}"
        )
        try:
            msg = await llm_call(
                get_llm("worker"), [SystemMessage(content=RLM_BATCH_SYSTEM), HumanMessage(content=prompt)]
            )
            text = message_text(msg).strip()
            emit(
                "rlm",
                "research",
                f"batch {batch_no} findings ready ({len(text)} chars)",
                batch=batch_no,
                preview=text[:300],
            )
            return batch_no, text
        except LLMError as exc:
            emit("error", "research", f"batch {batch_no} failed and was skipped: {exc}")
            return batch_no, None


# ----------------------------------------------------------------------------------- 5. aggregate
async def _aggregate(findings: list[str], question: str, depth: int = 1) -> str:
    """Recursively merge findings in groups of 3 until one synthesis remains."""
    if len(findings) == 1:
        return findings[0]
    group_size = 3
    if len(findings) <= group_size or depth >= settings.rlm_max_depth:
        groups = [findings]
    else:
        groups = [findings[i : i + group_size] for i in range(0, len(findings), group_size)]
    emit(
        "rlm",
        "research",
        f"aggregation depth {depth}: merging {len(findings)} finding sets in {len(groups)} group(s)",
        depth=depth,
        groups=len(groups),
    )

    async def merge(group: list[str]) -> str:
        prompt = f"Question: {question}\n\n" + "\n\n---\n\n".join(
            f"Findings set {i + 1}:\n{g}" for i, g in enumerate(group)
        )
        try:
            msg = await llm_call(
                get_llm("primary"),
                [SystemMessage(content=RLM_AGGREGATE_SYSTEM), HumanMessage(content=prompt)],
            )
            return message_text(msg).strip()
        except LLMError as exc:
            emit("error", "research", f"aggregation call failed ({exc}); concatenating findings instead")
            return "\n\n".join(group)

    merged = await asyncio.gather(*(merge(g) for g in groups))
    if len(merged) == 1:
        return merged[0]
    return await _aggregate(list(merged), question, depth + 1)


# ----------------------------------------------------------------------------------- 6. quantify
async def _quantify(collection: list[dict[str, Any]], user, registry) -> str:
    docs: dict[str, dict[str, Any]] = {}
    for ev in collection:
        docs.setdefault(
            ev["doc_id"],
            {
                "doc_id": ev["doc_id"],
                "title": ev["title"],
                "date": ev.get("created_date"),
                "type": ev.get("document_type"),
                "tags": [],
            },
        )
    # tags live in chunk metadata for incident docs; the knowledge index keeps them on the chunk.
    from assistant.retrieval import get_knowledge_index

    index = await get_knowledge_index()
    for c in index.chunks:
        if c.doc_id in docs and c.metadata.get("tags"):
            docs[c.doc_id]["tags"] = [t.strip() for t in str(c.metadata["tags"]).split(",")]
    data = list(docs.values())
    if not registry.is_allowed(user, "python_analysis"):
        # Still give a deterministic count in code, but be explicit that the tool was not used.
        counts = Counter(t for d in data for t in d["tags"])
        emit(
            "security",
            "research",
            f"python_analysis not available to role '{user.role.value}'; using built-in counts instead",
        )
        return "Document counts (built-in, analytics tool not permitted for this role): " + json.dumps(
            dict(counts.most_common(6))
        )
    code = (
        "from collections import Counter\n"
        "tags = Counter(t for d in data for t in d.get('tags', []))\n"
        "months = Counter((d.get('date') or '')[:7] for d in data if d.get('type') == 'incident')\n"
        "result = {'documents': len(data), 'incidents': sum(1 for d in data if d.get('type') == 'incident'),\n"
        "          'root_cause_tags': dict(tags.most_common(8)), 'incidents_per_month': dict(sorted(months.items()))}\n"
    )
    emit(
        "tool_call",
        "research",
        "python_analysis: root-cause tag and monthly counts",
        tool="python_analysis",
        params={"purpose": "root cause frequency", "rows": len(data)},
    )
    res = await registry.execute(
        "python_analysis",
        {"code": code, "data": data, "purpose": "root cause tag frequency over research collection"},
        user,
    )
    emit(
        "tool_result",
        "research",
        f"python_analysis -> {'ok' if res.ok else res.error}",
        tool="python_analysis",
        ok=res.ok,
        output=res.output if res.ok else None,
    )
    return (
        "Quantitative summary (python_analysis): " + json.dumps(res.output.get("result"))
        if res.ok
        else f"Quantitative analysis unavailable: {res.error}"
    )


# =============================================================================== node
@resilient("research", _fallback)
async def research_node(state: AssistantState) -> dict[str, Any]:
    user = user_from_state(state)
    registry = await get_tool_registry()
    question, sub_questions, filters = (
        state["question"],
        state.get("sub_questions", []),
        dict(state.get("filters") or {}),
    )
    trace: list[dict[str, Any]] = []

    plan, code, method = await _plan(question, sub_questions, filters)
    emit(
        "rlm",
        "research",
        f"search plan ready via {method}: {len(plan)} step(s)",
        plan=plan,
        code=code,
        method=method,
    )
    trace.append({"step": "plan", "method": method, "steps": plan, "code": code})

    collection, notes = await _explore(plan, user, registry)
    if not collection:
        # Adaptive research: LLM-inferred filters (especially dates) are often too strict. Relax in two steps,
        # exactly like the retrieval agent does, before concluding that nothing matches.
        for label, strip in (
            ("date filters", ("created_after", "created_before")),
            ("all filters", ("created_after", "created_before", "department", "document_types")),
        ):
            relaxed = [{k: v for k, v in step.items() if k not in strip} for step in plan]
            emit("rlm", "research", f"no documents matched; retrying plan without {label}", plan=relaxed)
            collection, more = await _explore(relaxed, user, registry)
            notes += [f"relaxed {label} after empty result", *more]
            if collection:
                plan = relaxed
                break
    docs = sorted({e["doc_id"] for e in collection})
    emit(
        "rlm",
        "research",
        f"explored collection: {len(collection)} chunks across {len(docs)} documents",
        documents=docs,
        notes=notes,
    )
    trace.append({"step": "explore", "chunks": len(collection), "documents": docs, "notes": notes})
    if not collection:
        return {
            "evidence": [],
            "research_findings": "No documents matched the research plan.",
            "rlm_trace": trace,
            "degraded": True,
        }

    batches = _batches(collection)
    emit(
        "rlm",
        "research",
        f"split into {len(batches)} batch(es) of up to {settings.rlm_batch_size} chunks",
        batches=batches,
    )
    trace.append({"step": "batch", "batches": batches})

    sem = asyncio.Semaphore(4)
    results = await asyncio.gather(
        *(
            _analyse_batch(i + 1, ids, collection, question, sub_questions, sem)
            for i, ids in enumerate(batches)
        )
    )
    findings = [text for _, text in results if text]
    failed = [n for n, text in results if not text]
    trace.append({"step": "analyse", "batches_ok": len(findings), "batches_failed": failed})
    if not findings:
        return {
            "evidence": collection,
            "research_findings": "All research sub-agents failed; falling back to raw evidence.",
            "rlm_trace": trace,
            "degraded": True,
        }

    synthesis = await _aggregate(findings, question)
    quant = await _quantify(collection, user, registry)
    research_findings = f"{synthesis}\n\n{quant}"
    trace.append({"step": "aggregate", "chars": len(synthesis), "quant": quant[:200]})
    emit(
        "rlm",
        "research",
        "research synthesis complete",
        chars=len(research_findings),
        batches=len(findings),
        failed_batches=failed,
    )
    return {
        "evidence": collection,
        "research_findings": research_findings,
        "rlm_trace": trace,
        "degraded": bool(failed or notes),
    }
