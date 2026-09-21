---
title: "Product Specification: Instant Transfers v2"
department: payments
document_type: product_spec
access_level: internal
created_date: 2025-08-01
---

# Product Specification: Instant Transfers v2

## Goals
- Sub-5-second end-to-end transfer completion for 99% of domestic transfers.
- Transparent status ("sent", "pending - network delay", "completed") in the mobile app.

## Requirements
- Limits: GBP 25,000 per transaction, GBP 50,000 per day for retail customers.
- When the network circuit breaker is open, transfers are queued and the customer sees a pending status with
  an expected completion window (requirement derived from INC-2025-0815).
- Retry profile: exponential backoff starting at 2 minutes, max 6 attempts.

## Out of scope
International transfers (covered by SWIFT gpi spec).
