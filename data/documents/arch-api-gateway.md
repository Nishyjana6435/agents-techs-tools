---
title: "API Gateway and Edge Security Architecture"
department: platform_engineering
document_type: architecture
access_level: internal
created_date: 2025-03-15
---

# API Gateway and Edge Security Architecture

The API gateway (Kong) fronts all customer-facing APIs. It enforces mutual TLS for partner integrations,
OAuth2 for customer apps, and per-client rate limiting using a token bucket algorithm with a default of
600 requests per minute and a burst of 100. WAF rules are managed by the Security Engineering team.

## Traffic routing
- `/v1/payments/*` -> PayCore Gateway
- `/v1/accounts/*` -> Accounts Service
- `/v1/identity/*` -> Identity Provider

## Observability
All gateway logs ship to the central logging platform with a 400-day retention for regulatory purposes.
