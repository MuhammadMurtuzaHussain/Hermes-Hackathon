"""Night Shift: an on-call engineer for Hermes Agent that learns from every incident."""

from __future__ import annotations

from pathlib import Path

from . import schemas
from .tools import NightShift

TOOLSET = "night-shift"


def _command(ns: NightShift, raw_args: str) -> str:
    parts = (raw_args or "").split()
    verb, rest = (parts[0].lower(), parts[1:]) if parts else ("status", [])
    if verb == "approve" and rest:
        return ns.approve(rest[0])
    if verb == "deny" and rest:
        return ns.deny(rest[0])
    if verb in {"status", "runbooks"}:
        return ns.status()
    return "Usage: /nightshift [status | approve <incident-id> | deny <incident-id>]"


def register(ctx):
    ns = NightShift(ctx)

    for schema, handler in (
        (schemas.DIAGNOSE, ns.diagnose),
        (schemas.QUERY_METRICS, ns.query_metrics),
        (schemas.RECALL, ns.recall),
        (schemas.PROPOSE_FIX, ns.propose_fix),
        (schemas.RESOLVE, ns.resolve),
    ):
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema, handler=handler,
                          emoji="🌙")

    ctx.register_hook("pre_tool_call", ns.pre_tool_call)

    ctx.register_command("nightshift", lambda raw: _command(ns, raw),
                         description="Night Shift incidents: status, approve or deny a fix",
                         args_hint="[status|approve <id>|deny <id>]")

    skills_dir = Path(__file__).parent / "skills"
    for child in sorted(skills_dir.iterdir()):
        if child.is_dir() and (child / "SKILL.md").exists():
            ctx.register_skill(child.name, child / "SKILL.md")
