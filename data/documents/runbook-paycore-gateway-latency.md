---
title: "Runbook RB-PAY-001: PayCore Gateway High Latency"
department: payments
document_type: runbook
access_level: internal
created_date: 2025-06-20
---

# Runbook RB-PAY-001: PayCore Gateway High Latency

**Trigger:** Alert `PAY-LATENCY-P95` (p95 > 800ms for 5 minutes)

## Diagnosis steps
1. Open the PayCore dashboard and identify which downstream (acquirer, instant network, fraud) is slow.
2. Check circuit breaker state: `paycore-cli cb status`. Breakers should be CLOSED in normal operation.
3. Check connection pool utilisation: `paycore-cli pool stats`. Above 80% utilisation indicates exhaustion.

## Mitigation
- If a downstream is slow and its breaker has not tripped, trip it manually: `paycore-cli cb open <dependency>`.
- If pool utilisation is above 80%, scale the gateway horizontally: `kubectl scale deploy paycore --replicas=+2`.
- Never increase timeouts as a mitigation; that worsens pool exhaustion (see INC-2025-0112).

## Escalation
Page the Payments Platform lead if latency persists for more than 30 minutes.
