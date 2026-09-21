---
title: "Enterprise Knowledge Assistant Architecture"
department: platform_engineering
document_type: architecture
access_level: internal
created_date: 2025-12-01
system: knowledge-assistant
---

# Enterprise Knowledge Assistant Architecture

## Overview
The knowledge assistant is a LangGraph-orchestrated multi-agent system exposed through FastAPI and a
Streamlit UI. Agents: Supervisor (intent + routing), Retrieval (hybrid RAG), Research (recursive
decomposition), Response (grounded answer), Validator (guardrails), Approval (human-in-the-loop).

## Retrieval
Hybrid search fuses dense embeddings stored in Pinecone with BM25 keyword scores using reciprocal rank
fusion. Documents are partitioned into Pinecone namespaces by department and carry metadata
(department, document_type, access_level, created_date) for filtering. Access-level filtering is applied
at query time based on the caller's clearance so restricted content never enters the prompt.

## Observability
Every conversation is traced end-to-end in LangSmith: graph node transitions, LLM calls, retrieval
operations and tool invocations.

## Security
Prompt-injection screening on inputs and retrieved chunks, RBAC enforced at the tool registry, token-bucket
rate limiting per user, and output guardrails for citations, secrets and brand compliance.
