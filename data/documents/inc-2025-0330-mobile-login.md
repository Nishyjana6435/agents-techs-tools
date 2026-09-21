---
title: "Mobile App Login Outage After Identity Provider Upgrade"
department: digital_channels
document_type: incident
access_level: internal
created_date: 2025-03-30
severity: SEV-1
tags: identity, upgrade
---

# Mobile App Login Outage After Identity Provider Upgrade

**Severity:** SEV-1  **Duration:** 1h 30m  **Affected:** Mobile banking app, Identity Provider (Keycloak)

## Summary
An upgrade of the identity provider changed the default token signature algorithm from RS256 to
ES256. The mobile app's embedded JWKS cache did not include the new key type, so all logins
failed until the app fetched a fresh JWKS.

## Root cause
Breaking configuration default in the identity provider upgrade, not covered by the regression suite.

## Action items
- Pin signature algorithm explicitly in realm configuration
- Add login smoke test to the upgrade pipeline
