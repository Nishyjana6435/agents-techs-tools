---
title: "Payments Platform Architecture Overview"
department: payments
document_type: architecture
access_level: internal
created_date: 2025-05-10
system: payments
---

# Payments Platform Architecture Overview

## Purpose
Describes the target-state architecture of Meridian's payments estate as of Q2 2025.

## Components
- **PayCore Gateway** - edge service terminating card and transfer requests; talks to acquirers and the
  instant payment network. Implements circuit breakers, retries with jitter and per-dependency bulkheads.
- **Card Authorisation Service** - decisions card authorisations in under 150ms p95. Calls Fraud Scoring
  asynchronously since June 2025 (see INC-2025-0603).
- **Transfer Orchestrator** - saga coordinator for account-to-account transfers. Every ledger write carries an
  idempotency key derived from the transfer id and step.
- **Payments Ledger** - PostgreSQL 16 with synchronous streaming replication across two availability zones.
  Failover is automated via Patroni; pre-checks abort failover when replication lag exceeds 5 seconds.
- **Settlement Batch Service** - produces clearing files nightly at 00:30 UTC over the SFTP Bridge.

## Resilience principles
1. Never hold a database connection across a network call.
2. Every retry must be idempotent.
3. Every certificate is registered in the certificate inventory with automated rotation.
4. Circuit breakers are on by default; disabling one requires a change record.

## Data flows
Card requests: Acquirer -> PayCore Gateway -> Card Authorisation Service -> Ledger.
Transfers: Mobile/Web -> API Gateway -> Transfer Orchestrator -> Ledger -> Instant Payment Network.

## Known gaps
- Secondary network routing (PAY-2450) not yet delivered.
- Near-real-time reconciliation (PAY-2310) in progress.
