"""Night Shift: an on-call engineer for Hermes Agent that learns from every incident."""

from __future__ import annotations

import asyncio
from pathlib import Path

from . import schemas
from .notify import APPROVE_ACTION, DENY_ACTION
from .tools import NightShift

TOOLSET = "night-shift"
USAGE = ("Usage: /nightshift [status | stats | show <id> | approve <id> [code] | deny <id> | "
         "forget <runbook|all>]")


def _command(ns: NightShift, raw_args: str, *, platform: str = "", source=None, **_) -> str:
    parts = (raw_args or "").split()
    verb, rest = (parts[0].lower(), parts[1:]) if parts else ("status", [])
    if verb in {"status", "runbooks"}:
        return ns.status()
    if verb == "stats":
        return ns.stats()
    if verb == "show" and rest:
        return ns.show(rest[0])
    if verb in {"approve", "deny", "forget"} and rest:
        if problem := ns.authorize_command(platform, source):
            return problem
        user = getattr(source, "user_id", "") if source is not None else ""
        actor = f"{platform}:{user}" if user else "local"
        if verb == "approve":
            # Two steps: the first shows the stored commands verbatim, the second must quote their
            # fingerprint. Nobody approves from a model's summary, and a changed fix needs a new code.
            if len(rest) < 2 or rest[1].lower() != ns.approval_code(rest[0]):
                return ns.preview_approval(rest[0])
            return ns.approve(rest[0], actor)
        if verb == "deny":
            return ns.deny(rest[0], actor)
        return ns.forget(rest[0])
    return USAGE


def _slack_handler(ns: NightShift, verb: str):
    async def handler(ack, body, action):
        await ack()  # Slack wants this within 3 seconds; the fix takes longer
        user = str((body.get("user") or {}).get("id", ""))
        channel = str((body.get("channel") or {}).get("id", ""))
        incident_id = str(action.get("value") or "")

        def work() -> None:
            if problem := ns.authorize("slack", user):
                ns.notifier.tell(channel, user, problem)
                return
            actor = f"<@{user}>"
            if verb == "deny":
                result = ns.deny(incident_id, actor)
                if "denied" not in result:
                    ns.notifier.tell(channel, user, result)
                return
            inc, problem = ns.begin_approval(incident_id, actor)
            if problem:
                ns.notifier.tell(channel, user, problem)
                return
            ns.run_fix(inc)

        await asyncio.to_thread(work)

    return handler


def _cli_setup(parser) -> None:
    parser.add_argument("action", nargs="?", default="status",
                        help="status, stats, show <id>, approve <id>, deny <id>, forget <runbook|all>")
    parser.add_argument("target", nargs="?", default="")
    parser.add_argument("code", nargs="?", default="", help="for approve: the code it showed you")


def register(ctx):
    ns = NightShift(ctx)

    for schema, handler in (
        (schemas.DIAGNOSE, ns.diagnose),
        (schemas.QUERY_METRICS, ns.query_metrics),
        (schemas.RECALL, ns.recall),
        (schemas.PROPOSE_FIX, ns.propose_fix),
        (schemas.ESCALATE, ns.escalate),
        (schemas.RESOLVE, ns.resolve),
    ):
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema, handler=handler,
                          emoji="🌙")

    ctx.register_hook("pre_llm_call", ns.pre_llm_call)
    ctx.register_hook("pre_tool_call", ns.pre_tool_call)

    def command(raw_args: str, **session) -> str:
        return _command(ns, raw_args, **session)

    ctx.register_command("nightshift", command,
                         description="Night Shift incidents: status, stats, approve or deny a fix",
                         args_hint="[status|stats|show <id>|approve <id> [code]|deny <id>|forget <name>]")

    def cli(args) -> None:
        print(_command(ns, f"{args.action} {args.target} {args.code}".strip(), platform="cli"))

    ctx.register_cli_command("nightshift", help="Night Shift incidents: status, stats, approve, deny",
                             setup_fn=_cli_setup, handler_fn=cli)

    ctx.register_platform_handler("slack", ns.notifier.wire_slack)
    ctx.register_slack_action_handler(APPROVE_ACTION, _slack_handler(ns, "approve"))
    ctx.register_slack_action_handler(DENY_ACTION, _slack_handler(ns, "deny"))

    skills_dir = Path(__file__).parent / "skills"
    for child in sorted(skills_dir.iterdir()):
        if child.is_dir() and (child / "SKILL.md").exists():
            ctx.register_skill(child.name, child / "SKILL.md")
