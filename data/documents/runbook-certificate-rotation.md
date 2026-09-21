---
title: "Runbook RB-SEC-003: Certificate Rotation for Payments Integrations"
department: security
document_type: runbook
access_level: internal
created_date: 2025-12-05
---

# Runbook RB-SEC-003: Certificate Rotation for Payments Integrations

All certificates must be registered in `cert-inventory`. Alerts fire at 30, 14 and 7 days before expiry.

## Rotation procedure
1. Generate the new certificate through Vault PKI.
2. **If the issuing CA changes, obtain written confirmation from the counter-party that the new issuer is
   trusted before deployment** (control added after INC-2025-1121).
3. Deploy the new certificate while retaining the previous one for 7 days.
4. Verify a successful handshake using `openssl s_client` against the counter-party endpoint.
5. Update the inventory record.

## Rollback
Re-point the service at the retained previous certificate and re-run the handshake test.
