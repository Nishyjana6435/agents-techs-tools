from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from assistant.config import get_settings
from assistant.logging import get_logger

log = get_logger(__name__)

TOPIC_KEYWORDS = {
    "payments": ("payment", "transfer", "card", "acquirer", "ledger", "settlement"),
    "incidents": ("incident", "outage", "root cause", "sev-", "postmortem", "post-incident"),
    "security": ("certificate", "tls", "security", "access", "injection", "credential"),
    "runbooks": ("runbook", "how do i", "procedure", "steps"),
    "policy": ("policy", "allowed", "permitted", "classification", "compliance"),
    "architecture": ("architecture", "design", "component", "diagram"),
}


class LongTermMemory:
    """Per-user durable profile. File-backed JSON; one file per user."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _path(self, username: str) -> Path:
        safe = re.sub(r"[^a-z0-9_-]", "_", username.lower())
        return self.root / f"{safe}.json"

    def _load(self, username: str) -> dict[str, Any]:
        path = self._path(username)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                log.warning("long_term_memory_corrupt", user=username)
        return {"username": username, "topics": {}, "departments": {}, "recent_questions": [], "feedback": [], "preferences": {}, "turns": 0}

    async def get(self, username: str) -> dict[str, Any]:
        async with self._lock:
            return self._load(username)

    async def record_turn(self, username: str, question: str, departments: list[str], answer_style: str | None = None) -> dict[str, Any]:
        """Update the profile after a turn. Returns the list of memory updates for the activity panel."""
        async with self._lock:
            profile = self._load(username)
            updates: list[str] = []
            q = question.lower()
            for topic, keywords in TOPIC_KEYWORDS.items():
                if any(k in q for k in keywords):
                    profile["topics"][topic] = profile["topics"].get(topic, 0) + 1
                    updates.append(f"topic interest: {topic} (+1 -> {profile['topics'][topic]})")
            for dept in departments:
                if dept:
                    profile["departments"][dept] = profile["departments"].get(dept, 0) + 1
            profile["recent_questions"] = ([question[:200]] + profile["recent_questions"])[:10]
            if answer_style:
                profile["preferences"]["answer_style"] = answer_style
                updates.append(f"preference: answer_style={answer_style}")
            profile["turns"] += 1
            profile["last_seen"] = datetime.now(UTC).isoformat(timespec="seconds")
            self._path(username).write_text(json.dumps(profile, indent=2))
            return {"profile": profile, "updates": updates or ["recent_questions updated"]}

    async def record_feedback(self, username: str, thread_id: str, run_id: str | None, score: int, comment: str = "") -> None:
        async with self._lock:
            profile = self._load(username)
            profile["feedback"] = ([{"thread_id": thread_id, "run_id": run_id, "score": score, "comment": comment[:300], "at": datetime.now(UTC).isoformat(timespec="seconds")}] + profile["feedback"])[:50]
            self._path(username).write_text(json.dumps(profile, indent=2))

    @staticmethod
    def render(profile: dict[str, Any]) -> str:
        """Compact natural-language rendering injected into prompts."""
        if not profile or profile.get("turns", 0) == 0:
            return "No prior history with this user."
        topics = sorted(profile.get("topics", {}).items(), key=lambda kv: -kv[1])[:3]
        depts = sorted(profile.get("departments", {}).items(), key=lambda kv: -kv[1])[:3]
        parts = [f"{profile.get('turns', 0)} previous turns."]
        if topics:
            parts.append("Frequent topics: " + ", ".join(f"{t} ({n})" for t, n in topics) + ".")
        if depts:
            parts.append("Departments queried: " + ", ".join(d for d, _ in depts) + ".")
        if profile.get("preferences", {}).get("answer_style"):
            parts.append(f"Prefers {profile['preferences']['answer_style']} answers.")
        recent = profile.get("recent_questions", [])[:3]
        if recent:
            parts.append("Recent questions: " + " | ".join(recent))
        return " ".join(parts)


@lru_cache
def get_long_term_memory() -> LongTermMemory:
    return LongTermMemory(Path(get_settings().index_cache_dir).parent / "memory")
