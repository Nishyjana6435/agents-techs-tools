---
title: "Data Platform Architecture"
department: data_platform
document_type: architecture
access_level: internal
created_date: 2025-07-01
---

# Data Platform Architecture

The analytics warehouse (Snowflake) is loaded nightly from operational databases via change-data-capture
into a bronze/silver/gold medallion layout. Payment events are the largest source (about 40 million rows per
day). Schema changes in producers must be registered in the schema registry, a control introduced after
INC-2025-0912.

Data classification follows the Information Classification Policy: PII columns are tokenised in silver and
only accessible to roles with the `pii_reader` grant.
