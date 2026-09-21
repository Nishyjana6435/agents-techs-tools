---
title: "Data Warehouse Nightly Load Delayed"
department: data_platform
document_type: incident
access_level: internal
created_date: 2025-09-12
severity: SEV-3
tags: schema-change, data
---

# Data Warehouse Nightly Load Delayed

**Severity:** SEV-3  **Duration:** 5h

## Summary
The nightly load into the analytics warehouse ran 5 hours late because a schema change in the
Payments Ledger added a NOT NULL column without a default, breaking the ingestion job.

## Root cause
Uncoordinated schema change; no contract test between producer and consumer.

## Action items
- Introduce schema registry checks in the ledger CI pipeline
