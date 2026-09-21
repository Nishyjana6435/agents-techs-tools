"""Output guardrails run by the ``validator`` node after the response agent drafts an answer.

Checks
------
* **Hallucinated citations** - every ``[n]`` marker in the answer must map to an evidence item that
  was actually retrieved in this turn. Unknown markers fail validation and trigger a rewrite.
* **Secret / PII leakage** - API-key-looking strings, card numbers (Luhn-checked) and IBANs are
  redacted. For a bank this is non-negotiable.
* **Brand & compliance** - the assistant speaks for Meridian Commercial Bank: no personalised
  investment advice, no disparaging competitors, no guaranteeing returns, no profanity.
* **Invalid response** - empty answers, raw prompt-leak markers, or answers that assert facts with
  zero evidence when evidence was required.

The guard returns a ``GuardReport`` with machine-readable issues so the activity panel can show
*why* a response was rewritten, and a ``cleaned_text`` with redactions applied.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CITATION_RE = re.compile(r"\[(\d{1,2})\]")
API_KEY_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|pcsk_[A-Za-z0-9_-]{16,}|lsv2_[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{10,})\b")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
PROMPT_LEAK_RE = re.compile(r"(BEGIN SYSTEM PROMPT|<system>|You are the Meridian Knowledge Assistant, an internal)", re.IGNORECASE)

BRAND_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("personal_investment_advice", re.compile(r"\b(you should|I recommend you|I advise you to)\b.{0,40}\b(buy|sell|invest in|short|hold)\b.{0,40}\b(stock|shares|crypto|bitcoin|bond|fund)s?\b", re.I)),
    ("guaranteed_returns", re.compile(r"\b(guaranteed|risk-free|can't lose|cannot lose)\b.{0,30}\b(return|profit|gain)s?\b", re.I)),
    ("competitor_disparagement", re.compile(r"\b(hsbc|barclays|citi|jpmorgan|chase|wells fargo|santander|lloyds)\b.{0,60}\b(scam|fraud|terrible|worst|incompetent|steal)", re.I)),
    ("profanity", re.compile(r"\b(fuck|shit|bitch|asshole)\b", re.I)),
]


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


@dataclass
class GuardReport:
    passed: bool
    issues: list[str] = field(default_factory=list)
    redactions: int = 0
    invalid_citations: list[int] = field(default_factory=list)
    cleaned_text: str = ""

    def summary(self) -> str:
        return "passed" if self.passed else "; ".join(self.issues)


def check_response(text: str, allowed_citation_ids: set[int], evidence_required: bool) -> GuardReport:
    issues: list[str] = []
    redactions = 0
    cleaned = text or ""

    if not cleaned.strip():
        return GuardReport(passed=False, issues=["empty response"], cleaned_text="")

    # 1. Citations must point at real evidence.
    cited = {int(m) for m in CITATION_RE.findall(cleaned)}
    invalid = sorted(cited - allowed_citation_ids)
    if invalid:
        issues.append(f"hallucinated citations: {invalid}")
    if evidence_required and allowed_citation_ids and not cited:
        issues.append("answer cites no evidence although sources were retrieved")

    # 2. Secrets and payment data.
    cleaned, n = API_KEY_RE.subn("[REDACTED-SECRET]", cleaned)
    redactions += n

    def _card(m: re.Match[str]) -> str:
        nonlocal redactions
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            redactions += 1
            return "[REDACTED-CARD]"
        return m.group(0)

    cleaned = CARD_RE.sub(_card, cleaned)
    cleaned, n = IBAN_RE.subn("[REDACTED-IBAN]", cleaned)
    redactions += n
    if redactions:
        issues.append(f"redacted {redactions} sensitive token(s)")

    # 3. Brand & compliance.
    for name, pattern in BRAND_RULES:
        if pattern.search(cleaned):
            issues.append(f"brand rule violated: {name}")

    # 4. Prompt leakage.
    if PROMPT_LEAK_RE.search(cleaned):
        issues.append("possible system prompt leakage")

    # Redactions alone do not fail validation (we fixed them); everything else does.
    hard_failures = [i for i in issues if not i.startswith("redacted")]
    return GuardReport(
        passed=not hard_failures,
        issues=issues,
        redactions=redactions,
        invalid_citations=invalid,
        cleaned_text=cleaned,
    )
