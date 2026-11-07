"""Incident records and learned runbooks.

After a fix is approved and verified, Night Shift turns what worked into a Hermes skill
(a SKILL.md). Next time an alert with the same signature fires, the runbook is recalled
and the agent can go straight to the known fix.

Pure Python, no Hermes imports, so it can be tested in isolation.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "was", "for", "with", "at"}

# Incident states that mean someone (human or agent) is already on it.
ACTIVE = {"open", "fix_proposed", "fixing", "escalated"}


def tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOPWORDS and len(w) > 2}


def slug(text: str, limit: int = 48) -> str:
    s = "-".join(_WORD.findall((text or "").lower()))
    return s[:limit].strip("-") or "incident"


def one_line(text: str, limit: int = 600) -> str:
    """Flatten text from the outside world (alert fields, model output) before it goes into a
    skill file: no new headings, no frontmatter, no fences."""
    flat = " ".join(str(text or "").split()).replace("```", "'''").replace("---", "-")
    return flat[:limit]


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    seconds = int(round(seconds))
    return f"{seconds}s" if seconds < 90 else f"{seconds // 60}m {seconds % 60:02d}s"


@dataclass
class Incident:
    alertname: str
    service: str
    summary: str = ""
    id: str = field(default_factory=lambda: "INC-" + uuid.uuid4().hex[:6].upper())
    opened_at: float = field(default_factory=time.time)
    # open | fix_proposed | fixing | resolved | fix_failed | denied | escalated
    status: str = "open"
    evidence: list[str] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    proposed_fix: dict | None = None
    proposed_at: float | None = None
    approved_at: float | None = None
    approved_by: str = ""
    denied_by: str = ""
    resolved_at: float | None = None
    runbook: str | None = None
    recalled_from: str = ""
    escalation: str = ""
    outcome: str = ""
    card: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Incident":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def mark(self, event: str) -> None:
        self.timeline.append({"t": time.time(), "event": event})

    @property
    def time_to_card(self) -> float | None:
        return self.proposed_at - self.opened_at if self.proposed_at else None

    @property
    def time_to_recover(self) -> float | None:
        return self.resolved_at - self.approved_at if self.resolved_at and self.approved_at else None


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
    verify_promql: str = ""
    verify_command: str = ""
    created_at: float = field(default_factory=time.time)
    times_used: int = 0
    failures: int = 0
    history: list[dict] = field(default_factory=list)  # one entry per verified resolution

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Runbook":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def skill_name_for(alertname: str, service: str) -> str:
    return f"runbook-{slug(service, 20)}-{slug(alertname, 36)}"[:64].rstrip("-")


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
    """Render a learned runbook as a Hermes SKILL.md.

    The alert name and everything the model wrote are untrusted, so they are JSON-quoted in the
    frontmatter and flattened in the body. The commands are the ones a human approved.
    """
    fix = "\n".join(f"   ```bash\n   {c.replace('```', '')}\n   ```" for c in rb.fix_commands)
    alert, service = one_line(rb.alertname, 80), one_line(rb.service, 80)
    check = []
    if rb.verify_promql:
        check.append(f"PromQL returns a result: `{one_line(rb.verify_promql, 300)}`")
    if rb.verify_command:
        check.append(f"Command exits 0: `{one_line(rb.verify_command, 300)}`")
    seen = "\n".join(
        f"- {h['incident']}: card in {duration(h.get('time_to_card'))}, "
        f"recovered {duration(h.get('time_to_recover'))} after approval"
        for h in rb.history) or "- (first occurrence)"
    description = (f"Learned runbook for the {alert} alert on {service}. Use when this alert fires or "
                   f"the symptoms below appear. Written by Night Shift from {rb.learned_from}.")
    return f"""---
name: {rb.skill_name}
description: {json.dumps(description)}
metadata:
  night_shift:
    alertname: {json.dumps(alert)}
    service: {json.dumps(service)}
    learned_from: {rb.learned_from}
---

# Runbook: {alert} on {service}

Learned from {rb.learned_from}. These commands were approved by a human and the recovery check
passed before this file was written.

## Symptoms
{one_line(rb.symptoms)}

## Root cause last time
{one_line(rb.root_cause)}

## Procedure
1. Confirm the evidence with `nightshift_diagnose`. Do not skip this: the same alert can have a
   different cause.
2. If it matches, propose this fix with `nightshift_propose_fix`, passing `runbook: {rb.skill_name}`:
{fix}
3. Verify: {one_line(rb.verification)}
{chr(10).join(f'   - {c}' for c in check)}
4. Night Shift runs the fix after human approval, checks recovery and updates this runbook. If the
   cause is different this time, say so in the diagnosis and propose what the evidence supports.

## Seen before
{seen}
"""
