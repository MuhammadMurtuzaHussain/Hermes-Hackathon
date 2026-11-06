"""Incident records and learned runbooks.

After an incident is resolved, Night Shift turns what worked into a Hermes
skill (a SKILL.md). Next time an alert with the same signature fires, the
runbook is recalled and the agent can go straight to the known fix.

Pure Python, no Hermes imports, so it can be tested in isolation.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "was", "for", "with", "at"}


def tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOPWORDS and len(w) > 2}


def slug(text: str, limit: int = 48) -> str:
    s = "-".join(_WORD.findall((text or "").lower()))
    return s[:limit].strip("-") or "incident"


@dataclass
class Incident:
    alertname: str
    service: str
    summary: str = ""
    id: str = field(default_factory=lambda: "INC-" + uuid.uuid4().hex[:6].upper())
    opened_at: float = field(default_factory=time.time)
    status: str = "open"  # open | fix_proposed | fixing | resolved
    evidence: list[str] = field(default_factory=list)
    proposed_fix: dict | None = None
    resolved_at: float | None = None
    runbook: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Incident":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Runbook:
    skill_name: str
    alertname: str
    service: str
    root_cause: str
    fix_commands: list[str]
    verification: str
    symptoms: str
    learned_from: str
    created_at: float = field(default_factory=time.time)
    times_used: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Runbook":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def skill_name_for(alertname: str, service: str) -> str:
    return f"runbook-{slug(service, 20)}-{slug(alertname, 36)}"[:64]


def score(runbook: Runbook, alertname: str, service: str, symptoms: str) -> float:
    """Similarity between an incoming alert and a learned runbook (0..1)."""
    s = 0.0
    if runbook.alertname.lower() == (alertname or "").lower():
        s += 0.5
    if runbook.service.lower() == (service or "").lower():
        s += 0.2
    a, b = tokens(runbook.symptoms + " " + runbook.root_cause), tokens(symptoms)
    if a and b:
        s += 0.3 * len(a & b) / len(a | b)
    return round(s, 3)


def best_match(runbooks: list[Runbook], alertname: str, service: str, symptoms: str = "",
               threshold: float = 0.5) -> tuple[Runbook, float] | None:
    ranked = sorted(((r, score(r, alertname, service, symptoms)) for r in runbooks),
                    key=lambda x: x[1], reverse=True)
    if ranked and ranked[0][1] >= threshold:
        return ranked[0]
    return None


def render_skill(rb: Runbook, incident: Incident) -> str:
    """Render a learned runbook as a Hermes SKILL.md."""
    minutes = ""
    if incident.resolved_at:
        minutes = f" in {max(1, round((incident.resolved_at - incident.opened_at) / 60))} min"
    fix = "\n".join(f"   ```bash\n   {c}\n   ```" for c in rb.fix_commands) or "   (no commands recorded)"
    return f"""---
name: {rb.skill_name}
description: >-
  Learned runbook for the {rb.alertname} alert on {rb.service}. Use when this alert
  fires or the symptoms below appear. Written by Night Shift from {rb.learned_from}.
metadata:
  night_shift:
    alertname: {rb.alertname}
    service: {rb.service}
    learned_from: {rb.learned_from}
---

# Runbook: {rb.alertname} on {rb.service}

Learned automatically from incident {rb.learned_from}, resolved{minutes}.

## Symptoms
{rb.symptoms}

## Root cause last time
{rb.root_cause}

## Procedure
1. Confirm the symptoms with read-only checks (`nightshift_diagnose`). Do not skip this:
   the same alert can have a different cause.
2. If the evidence matches, propose this fix with `nightshift_propose_fix`:
{fix}
3. Wait for human approval. Never run the fix directly.
4. Verify: {rb.verification}
5. Close with `nightshift_resolve`. If the cause was different, say so, so this runbook is updated.
"""
