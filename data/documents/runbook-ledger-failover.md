---
title: "Runbook RB-PAY-002: Payments Ledger Database Failover"
department: payments
document_type: runbook
access_level: confidential
created_date: 2025-10-15
---

# Runbook RB-PAY-002: Payments Ledger Database Failover

## Planned failover
1. Confirm replication lag is below 5 seconds: `patronictl list`. Abort if not.
2. Announce in #payments-ops and create a change record.
3. Run `patronictl switchover --master ledger-a --candidate ledger-b`.
4. Verify services reconnect within 60 seconds. Since October 2025 pools validate connections and JVM DNS TTL
   is 30 seconds, so a restart should not be needed.

## Unplanned failover
1. Patroni will promote automatically. Confirm with `patronictl list`.
2. Watch the reconciliation dashboard for duplicate debits (should be zero because of idempotency keys).
3. If services still fail writes after 2 minutes, perform a rolling restart.

## Post-failover
Trigger an ad-hoc reconciliation run and file a post-incident review if customer impact exceeded 5 minutes.
