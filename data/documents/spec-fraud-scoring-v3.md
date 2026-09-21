---
title: "Product Specification: Fraud Scoring v3"
department: fraud_risk
document_type: product_spec
access_level: confidential
created_date: 2025-05-20
---

# Product Specification: Fraud Scoring v3

Fraud Scoring v3 returns a risk score 0-1000 for each card authorisation within 80ms p95. The Card
Authorisation Service calls it asynchronously with a 150ms budget; if the score is unavailable the
authorisation proceeds with a conservative rules-only decision. Model features include merchant category,
velocity in the last 24 hours and device fingerprint. Scores above 850 trigger a step-up challenge.
