"""Prompt-injection detection.

Approach (defence in depth, documented in docs/SECURITY.md):

* **Pattern layer (this module)** - fast, deterministic regex heuristics grouped by attack class.
  Deterministic checks are explainable ("matched rule X") and cheap enough to run on every user
  message and every retrieved chunk.
* **Structural layer** - retrieved documents are wrapped in ``<document>`` tags and the system
  prompt tells the model that document content is *data*, never instructions. Chunks that trip
  the detector are quarantined (excluded from context) and surfaced in the activity panel.
* **Capability layer** - even if an injection succeeds in steering the model, tool execution is
  gated by RBAC in the registry and admin tools require human approval, so the blast radius of a
  successful injection is bounded.

Scoring: each matched rule contributes a weight; ``risk`` is the clipped sum. Callers decide the
threshold (the supervisor blocks at >= 0.7 and warns in the trace at >= 0.4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

AttackClass = Literal["instruction_override", "data_exfiltration", "tool_abuse", "role_play_jailbreak"]


@dataclass(frozen=True)
class Rule:
    name: str
    attack_class: AttackClass
    pattern: re.Pattern[str]
    weight: float


def _r(name: str, cls: AttackClass, pattern: str, weight: float) -> Rule:
    return Rule(name, cls, re.compile(pattern, re.IGNORECASE | re.DOTALL), weight)


RULES: list[Rule] = [
    # --- Instruction override ---------------------------------------------------------------
    _r("ignore_previous", "instruction_override", r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier|all)\b.{0,30}\b(instructions?|prompts?|rules?)", 0.7),
    _r("new_instructions", "instruction_override", r"\b(your|the) (new|real|actual|true) (instructions?|task|role|goal|objective)\b", 0.6),
    _r("system_prompt_marker", "instruction_override", r"(^|\n)\s*(system|assistant)\s*:", 0.4),
    _r("fake_delimiters", "instruction_override", r"(<\s*/?\s*(system|instructions?|admin)\s*>|\[\s*(system|inst)\s*\]|###\s*(system|instruction))", 0.5),
    _r("override_verbs", "instruction_override", r"\b(override|bypass|disable|turn off)\b.{0,30}\b(safety|guardrails?|filters?|restrictions?|policy|policies|rbac|permissions?)\b", 0.8),
    # --- Role-play / jailbreak framing -------------------------------------------------------------
    _r("dan_style", "role_play_jailbreak", r"\b(you are now|from now on you are|pretend (to be|you are)|act as)\b.{0,60}\b(unrestricted|no (rules|limits|restrictions)|jailbroken|developer mode|dan)\b", 0.7),
    _r("hypothetical_unlock", "role_play_jailbreak", r"\b(hypothetically|in a fictional world|for a story)\b.{0,80}\b(reveal|leak|bypass|ignore)\b", 0.4),
    # --- Data exfiltration --------------------------------------------------------------------------
    _r("reveal_system_prompt", "data_exfiltration", r"\b(reveal|show|print|repeat|dump|output|display)\b.{0,40}\b(system prompt|hidden prompt|initial prompt|your instructions|configuration|api key|secret|credentials?|password)", 0.8),
    _r("exfil_to_url", "data_exfiltration", r"\b(send|post|upload|transmit|forward|email)\b.{0,60}\b(to|at)\b.{0,10}(https?://|\S+@\S+\.\S+|webhook)", 0.8),
    _r("markdown_image_exfil", "data_exfiltration", r"!\[[^\]]*\]\(https?://[^)]*\?[^)]*\)", 0.7),
    _r("dump_all_docs", "data_exfiltration", r"\b(list|dump|export|print)\b.{0,20}\b(all|every)\b.{0,20}\b(documents?|records?|customers?|employees?|passwords?|accounts?)\b.{0,30}\b(verbatim|in full|raw|complete)", 0.5),
    # --- Tool abuse -----------------------------------------------------------------------------------
    _r("escalate_privileges", "tool_abuse", r"\b(grant|give|elevate|escalate|make)\b.{0,30}\b(me|my|user)\b.{0,30}\b(admin|administrator|root|superuser|all permissions)\b", 0.8),
    _r("call_tool_as_role", "tool_abuse", r"\b(call|invoke|run|execute|use)\b.{0,40}\b(tool|function)\b.{0,60}\b(as|with) (admin|administrator|elevated|root)\b", 0.8),
    _r("dangerous_python", "tool_abuse", r"\b(os\.system|subprocess|__import__|open\(|eval\(|exec\(|shutil\.rmtree|socket\.|requests\.(get|post))", 0.6),
    _r("delete_everything", "tool_abuse", r"\b(delete|drop|wipe|erase|truncate)\b.{0,30}\b(all|every|entire|whole)\b.{0,30}\b(records?|tables?|index|database|documents?|namespace)", 0.6),
]


@dataclass
class InjectionVerdict:
    risk: float
    matched_rules: list[str] = field(default_factory=list)
    attack_classes: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.risk >= 0.7

    @property
    def suspicious(self) -> bool:
        return self.risk >= 0.4

    def explain(self) -> str:
        if not self.matched_rules:
            return "no injection patterns matched"
        return f"risk={self.risk:.2f}; rules={', '.join(self.matched_rules)}; classes={', '.join(self.attack_classes)}"


def scan_prompt_injection(text: str) -> InjectionVerdict:
    matched: list[Rule] = [rule for rule in RULES if rule.pattern.search(text or "")]
    risk = min(1.0, sum(r.weight for r in matched))
    return InjectionVerdict(
        risk=round(risk, 2),
        matched_rules=[r.name for r in matched],
        attack_classes=sorted({r.attack_class for r in matched}),
    )


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")


def sanitize_retrieved_text(text: str) -> str:
    """Neutralise the most common indirect-injection vectors inside document text.

    We do not rewrite meaning; we only strip control/invisible characters (used to hide
    instructions) and defang fake role markers so a chunk can't impersonate the system.
    """
    text = _CONTROL_CHARS.sub("", text)
    text = _INVISIBLE.sub("", text)
    text = re.sub(r"(?im)^\s*(system|assistant|user)\s*:", lambda m: m.group(0).replace(":", " -"), text)
    text = text.replace("</document>", "&lt;/document&gt;")
    return text
