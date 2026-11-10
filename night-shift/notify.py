"""The incident card in Slack: what the on-call engineer reads and taps.

One card per incident, edited in place as it moves from proposed to resolved, so the channel
reads as a short story instead of a stream. Hermes hands us the Slack client through
``register_platform_handler``; the button clicks come back through
``register_slack_action_handler`` (see ``__init__.py``).

Tool handlers run on worker threads and the Slack client lives on the gateway's event loop,
so everything here is scheduled onto that loop. A failed post is reported, never raised: the
incident must survive a Slack outage, and the slash commands still work.
"""

from __future__ import annotations

import asyncio
import logging

from .runbooks import Incident, duration

logger = logging.getLogger(__name__)

APPROVE_ACTION = "nightshift_approve"
DENY_ACTION = "nightshift_deny"
POST_TIMEOUT_SECONDS = 15
SECTION_LIMIT = 3000  # Slack's limit for a section's text. The fix box is refused past it, never cut.
CONFIRM_LIMIT = 300   # Slack's limit for a confirm dialog's text
FENCE = "```"

_RISK_EMOJI = {"low": "🟢", "medium": "🟠", "high": "🔴"}


def esc(text: str) -> str:
    """Slack control characters. Without this a poisoned alert could ping <!channel> or hide a link."""
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _section(text: str) -> dict:
    """Prose only: a long cause or outcome may be clipped. Commands never go through here."""
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:SECTION_LIMIT]}}


def _plural(n: int) -> str:
    return f"{n} command{'' if n == 1 else 's'}"


def fix_text(commands: list[str], risk: str) -> str:
    """The fix box exactly as Slack receives it: every command, escaped, in one code block."""
    shown = "\n".join(esc(c) for c in commands)
    return (f"*Fix* · {_plural(len(commands))} · risk: {_RISK_EMOJI.get(risk, '⚪')} {esc(risk)}\n"
            f"{FENCE}\n{shown}\n{FENCE}")


def unshowable(commands: list[str], risk: str = "medium") -> str:
    """Why these commands cannot be shown in full on the card, or ''.

    The approver must see every command that runs. A fence inside a command would close the code
    block early, and a box over Slack's limit would be rejected or cut, so both are refused here,
    measured on the escaped text that is actually sent.
    """
    if any(FENCE in str(c) for c in commands):
        return "A command contains ``` and would break out of the code block on the card."
    size = len(fix_text(commands, risk))
    if size > SECTION_LIMIT:
        return (f"The commands take {size} characters once escaped for Slack, over its {SECTION_LIMIT}-character "
                "limit, so the approver could not see them all.")
    return ""


def confirm_text(commands: list[str]) -> str:
    """The confirm dialog repeats the commands when they fit, and otherwise points at the box above,
    which unshowable() guarantees is complete."""
    listed = f"Runs exactly:\n{FENCE}\n" + "\n".join(esc(c) for c in commands) + f"\n{FENCE}"
    if len(listed) <= CONFIRM_LIMIT:
        return listed
    return f"Runs the {_plural(len(commands))} in the Fix box, exactly as shown there, and nothing else."


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3000]}]}


def fallback_text(inc: Incident) -> str:
    """Plain text for notifications and for platforms without blocks."""
    head = f"{inc.id} · {inc.alertname} on {inc.service}"
    if inc.status == "fix_proposed":
        return f"{head}: fix proposed. /nightshift show {inc.id} to read it, then approve or deny."
    return f"{head}: {inc.status.replace('_', ' ')}. {inc.outcome}".strip()


def brief(text: str, limit: int = 420) -> str:
    """The card is read at 3am. The full text stays in /nightshift show."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:end + 1] if end > limit // 2 else cut.rstrip()) + " …"


def status_line(inc: Incident) -> str:
    if inc.status == "fix_proposed":
        return f"Waiting for approval · card ready in {duration(inc.time_to_card)}"
    if inc.status == "fixing":
        return f"✅ Approved by {esc(inc.approved_by)} · running the fix and checking recovery…"
    if inc.status == "resolved":
        return f"✅ *Resolved.* {esc(inc.outcome)}"
    if inc.status == "fix_failed":
        return f"🚨 *Needs a human.* {esc(inc.outcome)}"
    if inc.status == "denied":
        return f"⛔ Denied by {esc(inc.denied_by)}. Nothing was run. The incident is yours."
    if inc.status == "escalated":
        return f"🙋 *Needs a human.* {esc(brief(inc.escalation, 700))}"
    if inc.status == "dismissed":
        return f"💤 *Dismissed as a false alarm.* {esc(brief(inc.escalation, 700))}"
    return esc(inc.status)


def slack_blocks(inc: Incident) -> list[dict]:
    fix = inc.proposed_fix or {}
    blocks: list[dict] = [{
        "type": "header",
        "text": {"type": "plain_text", "text": f"🌙 {inc.id} · {inc.alertname} on {inc.service}"[:150]},
    }]
    if inc.recalled_from:
        blocks.append(_section(f"🧠 *Recognised.* Runbook `{esc(inc.recalled_from)}`. {esc(inc.track_record)}".strip()))
    if fix:
        blocks.append(_section(f"*Cause*\n{esc(brief(fix.get('diagnosis', '')))}"))
        commands = [str(c) for c in fix.get("commands", [])]
        risk = str(fix.get("risk", "unknown"))
        if hidden := unshowable(commands, risk):
            # Only reachable for a proposal stored before this check existed: propose_fix refuses these.
            blocks.append(_section(f"*Fix* · {_plural(len(commands))}\n⚠️ {esc(hidden)} It cannot be approved "
                                   f"from Slack. Read it with `/nightshift show {esc(inc.id)}`."))
        else:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": fix_text(commands, risk)}})
        blocks.append(_context(f"Recovery check: {esc(brief(fix.get('verification', ''), 200))}"))
    blocks.append(_section(status_line(inc)))
    if inc.status == "fix_proposed":
        commands = [str(c) for c in fix.get("commands", [])]
        buttons = [] if unshowable(commands, str(fix.get("risk", ""))) else [
            {"type": "button", "action_id": APPROVE_ACTION, "style": "primary", "value": inc.id,
             "text": {"type": "plain_text", "text": "Approve fix"},
             "confirm": {
                 "title": {"type": "plain_text", "text": "Run this fix?"},
                 "text": {"type": "mrkdwn", "text": confirm_text(commands)},
                 "confirm": {"type": "plain_text", "text": "Run it"},
                 "deny": {"type": "plain_text", "text": "Cancel"},
             }}]
        buttons.append({"type": "button", "action_id": DENY_ACTION, "style": "danger", "value": inc.id,
                        "text": {"type": "plain_text", "text": "Deny"}})
        blocks.append({"type": "actions", "block_id": f"nightshift:{inc.id}", "elements": buttons})
    return blocks


class Notifier:
    def __init__(self, ns):
        self.ns = ns
        self.client = None
        self.loop = None

    @property
    def channel(self) -> str:
        return str(self.ns.cfg("slack_channel", "") or "")

    @property
    def ready(self) -> bool:
        return bool(self.client and self.loop and self.channel)

    def wire_slack(self, app, adapter) -> None:
        """Called by the Slack adapter at connect time, on the gateway's event loop."""
        self.client = app.client
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None
            logger.warning("Night Shift: Slack connected without a running loop; cards are disabled.")

    def _call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(POST_TIMEOUT_SECONDS)

    def post(self, inc: Incident) -> str:
        """Post a new card or edit the existing one. Returns an error message, or '' on success."""
        if not self.ready:
            return "Slack is not connected or slack_channel is not set"
        try:
            kwargs = {"channel": inc.card.get("channel") or self.channel, "text": fallback_text(inc),
                      "blocks": slack_blocks(inc)}
            if inc.card.get("ts"):
                self._call(self.client.chat_update(ts=inc.card["ts"], **kwargs))
            else:
                resp = self._call(self.client.chat_postMessage(**kwargs))
                inc.card = {"channel": resp["channel"], "ts": resp["ts"]}
            return ""
        except Exception as exc:
            logger.warning("Night Shift: could not post the card for %s: %s", inc.id, exc)
            return str(exc)

    def tell(self, channel: str, user: str, text: str) -> None:
        """A note only the clicker sees, e.g. why their click was refused."""
        if not self.ready:
            return
        try:
            self._call(self.client.chat_postEphemeral(channel=channel, user=user, text=text))
        except Exception as exc:
            logger.warning("Night Shift: could not send an ephemeral note: %s", exc)
