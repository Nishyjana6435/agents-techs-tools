"""Date helpers shared by the chunker and the filter model."""

from __future__ import annotations

import re


def date_to_int(value: str) -> int:
    """'2025-04-19' -> 20250419; unknown/blank -> 0 (sorts before every real date)."""
    digits = re.sub(r"\D", "", str(value or ""))[:8]
    return int(digits) if len(digits) == 8 else 0
