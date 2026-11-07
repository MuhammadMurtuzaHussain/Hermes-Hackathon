"""Tool handlers and the incident lifecycle.

Handlers follow the Hermes contract: take (args, **kwargs), always return a JSON string,
never raise. All Hermes access goes through the PluginContext passed to NightShift, so
this module stays testable with a fake context.

The lifecycle has one rule: after a human decides, no model is involved. The agent's job
ends at the proposal. Running the approved commands, checking recovery and writing the
runbook are plain code, so the last mile cannot wander, and nothing a model said after
the approval can change what ran or what was learned.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import threading
import time
import urllib.parse
import urllib.request

from . import policy
from .notify import Notifier
from .runbooks import ACTIVE, WORKING, Incident, Runbook, best_match, duration, one_line, render_skill, skill_name_for

logger = logging.getLogger(__name__)

MAX_INCIDENTS = 200       # resolved incidents kept in plugin state
MAX_COMMAND_CHARS = 2500  # the approver must be able to read everything that will run
CHECK_OUTPUT_CHARS = 2500
LOCAL_SURFACES = {"", "cli", "tui", "desktop", "local"}


def _ok(**data) -> str:
    return json.dumps(data, default=str)


def _err(message: str, **data) -> str:
    return json.dumps({"error": message, **data}, default=str)


class NightShift:
    """Holds the plugin context and the incident and runbook stores."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.guarded_sessions: set[str] = set()
        self.notifier = Notifier(self)
        self._lock = threading.RLock()

    # ---- storage (ctx.state is profile-scoped and atomic) -----------------
    def _incidents(self) -> dict[str, dict]:
        return self.ctx.state.get("incidents", default={}) or {}

    def _save_incident(self, inc: Incident) -> None:
        with self._lock:
            data = self._incidents()
            data[inc.id] = inc.to_dict()
            if len(data) > MAX_INCIDENTS:
                closed = sorted((d for d in data.values() if d.get("status") not in ACTIVE),
                                key=lambda d: d.get("opened_at", 0))
                for old in closed[:len(data) - MAX_INCIDENTS]:
                    data.pop(old["id"], None)
            self.ctx.state.set("incidents", data)

    def get_incident(self, incident_id: str) -> Incident | None:
        raw = self._incidents().get((incident_id or "").strip().upper())
        return Incident.from_dict(raw) if raw else None

    def open_incidents(self) -> list[Incident]:
        return [Incident.from_dict(d) for d in self._incidents().values() if d.get("status") in ACTIVE]

    def runbooks(self) -> list[Runbook]:
        return [Runbook.from_dict(d) for d in (self.ctx.state.get("runbooks", default=[]) or [])]

    def _save_runbook(self, rb: Runbook) -> None:
        with self._lock:
            books = [r for r in self.runbooks() if r.skill_name != rb.skill_name] + [rb]
            self.ctx.state.set("runbooks", [r.to_dict() for r in books])

    def cfg(self, key: str, default):
        try:
            return self.ctx.get_config(key, default=default)
        except Exception:
            return default

    # ---- running commands ---------------------------------------------------
    def _full(self, command: str) -> str:
        """Apply the optional remote prefix, e.g. 'ssh ops@prod-1'."""
        prefix = str(self.cfg("docker_host_prefix", "") or "").strip()
        return f"{prefix} {shlex.quote(command)}" if prefix else command

    def _exec(self, command: str, timeout: int = 30) -> tuple[int, str]:
        """Run through Hermes's terminal tool, which keeps its own safety checks. Returns (exit, output)."""
        try:
            raw = self.ctx.dispatch_tool("terminal", {"command": self._full(command), "timeout": timeout})
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(parsed, dict):
                return 0, str(parsed)
            text = str(parsed.get("output") or "")
            if parsed.get("error"):
                text += f"\n(error: {parsed['error']})"
            code = parsed.get("exit_code")
            failed = code not in (None, 0) or (code is None and parsed.get("error"))
            return (int(code or 1) if failed else 0), text.strip()
        except Exception as exc:  # one failed check must not sink the triage
            return 1, f"(check failed: {exc})"

    def _check(self, command: str) -> str:
        code, text = self._exec(command)
        if code:
            text += f"\n(exit code {code})"
        return (text.strip() or "(no output)")[-CHECK_OUTPUT_CHARS:]

    def checks_for(self, service: str) -> dict[str, str]:
        s = shlex.quote(service)
        checks = {
            "status": f"docker ps -a --filter name={s} --format '{{{{.Names}}}} {{{{.Status}}}} {{{{.Image}}}}'",
            "logs": f"docker logs --tail 40 --timestamps {s} 2>&1 | tail -n 40",
            "resources": f"docker stats --no-stream --format '{{{{.Name}}}} cpu={{{{.CPUPerc}}}} mem={{{{.MemPerc}}}}' {s}",
            "disk": "df -h",
            "db": str(self.cfg("db_check_command", "") or (
                "docker exec postgres psql -U postgres -t -c \"SELECT state, count(*) FROM pg_stat_activity "
                "GROUP BY state\"")),
            "deploys": f"docker inspect --format '{{{{.Created}}}} {{{{.Config.Image}}}} {{{{json .Config.Labels}}}}' {s}",
        }
        if history := str(self.cfg("deploy_history_command", "") or ""):
            checks["deploys"] += f"; {history}"
        return checks

    def _prometheus(self, promql: str) -> list:
        base = str(self.cfg("prometheus_url", "http://localhost:9090")).rstrip("/")
        url = f"{base}/api/v1/query?{urllib.parse.urlencode({'query': promql})}"
        with urllib.request.urlopen(url, timeout=10) as resp:  # read-only GET
            return json.loads(resp.read().decode()).get("data", {}).get("result", [])

    # ---- tools ------------------------------------------------------------
    @staticmethod
    def _session_ids(kwargs) -> set[str]:
        return {str(kwargs.get(k)) for k in ("task_id", "session_id") if kwargs.get(k)}

    def diagnose(self, args: dict, **kwargs) -> str:
        try:
            service, alertname = args.get("service", "").strip(), args.get("alertname", "").strip()
            if not service or not alertname:
                return _err("service and alertname are required")
            with self._lock:
                self.guarded_sessions |= self._session_ids(kwargs)
            inc = self.get_incident(args.get("incident_id", ""))
            if inc is None:
                if dup := self._active_for(service, alertname):
                    dup.mark(f"also alerted: {one_line(alertname, 80)}")
                    self._save_incident(dup)
                    return _ok(duplicate_of=dup.id, status=dup.status,
                               instruction=(f"{dup.id} already covers {service}. Do nothing else: reply with "
                                            "one line saying so and stop."))
                inc = Incident(alertname=alertname, service=service, summary=one_line(args.get("summary", ""), 300))
                inc.mark("alert received, investigation started")
            catalogue = self.checks_for(service)
            wanted = args.get("checks") or list(catalogue)
            results = {name: self._check(catalogue[name]) for name in wanted if name in catalogue}
            results["commands_run_from"] = self._check("pwd")
            inc.evidence.append(f"{time.strftime('%H:%M:%S')} diagnose: {', '.join(wanted)}")
            self._save_incident(inc)
            found = self._recall(alertname, service, "")
            next_step = (found["instruction"] if found else
                         "No runbook matches. Load skill_view('night-shift:triage') if you have not, and work "
                         "out the cause from the evidence.")
            if (fixed := self._recently_fixed(service)) and fixed.id != inc.id:
                ago = int(time.time() - fixed.resolved_at)
                next_step = (f"{fixed.id} on this service was fixed {ago}s ago, and alerts on rates lag behind recovery. "
                             "Check current metrics first. If the service is healthy now, this is an echo: call "
                             "nightshift_escalate with false_alarm=true. " + next_step)
            return _ok(incident_id=inc.id, service=service, alertname=alertname, results=results, known_runbook=found,
                       next_step=next_step)
        except Exception as exc:
            logger.exception("Night Shift diagnose failed")
            return _err(f"diagnose failed: {exc}")

    def _active_for(self, service: str, alertname: str) -> Incident | None:
        window = float(self.cfg("dedupe_minutes", 10)) * 60
        live = [i for i in self.open_incidents() if i.service == service and time.time() - i.opened_at < window
                and (i.status in WORKING or i.alertname == alertname)]
        return max(live, key=lambda i: i.opened_at) if live else None

    def _recently_fixed(self, service: str) -> Incident | None:
        """An alert just after a fix is often an echo: rate() windows lag behind recovery."""
        window = float(self.cfg("echo_minutes", 3)) * 60
        done = [Incident.from_dict(d) for d in self._incidents().values()
                if d.get("status") == "resolved" and d.get("service") == service and d.get("resolved_at")]
        done = [i for i in done if time.time() - i.resolved_at < window]
        return max(done, key=lambda i: i.resolved_at) if done else None

    def query_metrics(self, args: dict, **kwargs) -> str:
        promql = (args.get("promql") or "").strip()
        if not promql:
            return _err("promql is required")
        try:
            return _ok(promql=promql, result=self._prometheus(promql)[:20])
        except Exception as exc:
            return _err(f"Prometheus query failed: {exc}", promql=promql)

    def _recall(self, alertname: str, service: str, symptoms: str) -> dict | None:
        match = best_match(self.runbooks(), alertname, service, symptoms)
        if not match:
            return None
        rb, confidence = match
        rb.times_used += 1
        self._save_runbook(rb)
        last = rb.history[-1] if rb.history else {}
        return {
            "runbook": rb.skill_name, "confidence": confidence, "learned_from": rb.learned_from,
            "root_cause_last_time": rb.root_cause, "symptoms": rb.symptoms, "fix_last_time": rb.fix_commands,
            "verification": rb.verification, "verify_promql": rb.verify_promql, "verify_command": rb.verify_command,
            "times_resolved": len(rb.history), "times_failed": rb.failures, "last_recovery_seconds": last.get("time_to_recover"),
            "instruction": (f"If the evidence matches, call nightshift_propose_fix with runbook='{rb.skill_name}' and a "
                            "one-line diagnosis. The commands and checks come from the runbook. Do not investigate further."),
        }

    def recall(self, args: dict, **kwargs) -> str:
        found = self._recall(args.get("alertname", ""), args.get("service", ""), args.get("symptoms", ""))
        if not found:
            return _ok(match=None, message="No learned runbook yet. Investigate from first principles.")
        return _ok(match=found["runbook"], **found)

    def propose_fix(self, args: dict, **kwargs) -> str:
        try:
            inc = self.get_incident(args.get("incident_id", ""))
            if not inc:
                return _err("unknown incident_id")
            if inc.status not in {"open", "fix_proposed"}:
                return _err(f"{inc.id} is {inc.status}, it cannot take a new proposal")
            book = next((r for r in self.runbooks() if r.skill_name == args.get("runbook")), None)
            given = [str(c).strip() for c in (args.get("commands") or []) if str(c).strip()]
            commands = given or (list(book.fix_commands) if book else [])
            recalled = bool(book) and commands == book.fix_commands
            memory = book if recalled else None  # a changed fix is new: it stands on its own
            value = lambda key, attr=None: args.get(key) or (getattr(memory, attr or key, "") if memory else "")
            if missing := [k for k, v in (("symptoms", value("symptoms")), ("risk", value("risk")),
                                          ("verification", value("verification"))) if not v]:
                return _err(f"{', '.join(missing)} required for a fix that is not a recalled runbook")
            if not commands:
                return _err("at least one command is required")
            if all(policy.is_read_only(c) for c in commands):
                return _err("These commands change nothing, so they are not a fix. Put checks in verify_promql or "
                            "verify_command, or call nightshift_escalate if no fix is justified.")
            if sum(len(c) for c in commands) > MAX_COMMAND_CHARS:
                return _err("The commands are too long for an approver to read. Propose a shorter fix.")
            promql = str(value("verify_promql")).strip()
            check = str(value("verify_command")).strip()
            if not promql and not check:
                return _err("A fix needs a recovery check: give verify_promql or verify_command.")
            if promql and not re.search(r"[<>]|==|!=", promql):
                return _err("verify_promql needs a comparison, so that it returns nothing until healthy.")
            if check and not policy.is_read_only(check):
                return _err("verify_command must be read-only.")
            if self._verified({"verify_promql": promql, "verify_command": check}) is True:
                return _err("The recovery check already passes, so it cannot prove the fix worked. Use one that "
                            "fails now, or call nightshift_escalate if the service looks healthy.")
            inc.proposed_fix = {
                "commands": commands, "diagnosis": one_line(args.get("diagnosis"), 1500),
                "symptoms": one_line(value("symptoms"), 600), "risk": str(value("risk")),
                "verification": one_line(value("verification"), 600),
                "verify_promql": promql, "verify_command": check,
            }
            inc.status, inc.proposed_at = "fix_proposed", time.time()
            inc.recalled_from = book.skill_name if recalled else ""
            inc.mark("fix proposed" + (f" from runbook {inc.recalled_from}" if recalled else ""))
            problem = self.notifier.post(inc)
            self._save_incident(inc)
            with_card = not problem
            return _ok(incident_id=inc.id, status=inc.status, card_posted=with_card,
                       approval=f"/nightshift approve {inc.id}", deny=f"/nightshift deny {inc.id}",
                       instruction=("The card with Approve and Deny buttons is posted. Reply with one short line and stop."
                                    if with_card else
                                    "Tell the on-call engineer the diagnosis, the exact commands, the risk and how "
                                    "to approve, then stop."))
        except Exception as exc:
            logger.exception("Night Shift propose_fix failed")
            return _err(f"propose_fix failed: {exc}")

    def escalate(self, args: dict, **kwargs) -> str:
        inc = self.get_incident(args.get("incident_id", ""))
        if not inc:
            return _err("unknown incident_id")
        reason = one_line(args.get("reason"), 1200)
        if steps := one_line(args.get("next_steps"), 600):
            reason += f" Check first: {steps}"
        false_alarm = bool(args.get("false_alarm"))
        inc.status, inc.escalation = ("dismissed" if false_alarm else "escalated"), reason
        inc.mark("dismissed as a false alarm" if false_alarm else "escalated to a human")
        problem = self.notifier.post(inc)
        self._save_incident(inc)
        return _ok(incident_id=inc.id, status=inc.status, card_posted=not problem,
                   instruction="Reply with one short line and stop.")

    def resolve(self, args: dict, **kwargs) -> str:
        """Manual close. Learning only ever happens through a verified, approved fix."""
        inc = self.get_incident(args.get("incident_id", ""))
        if not inc:
            return _err("unknown incident_id")
        if inc.status == "resolved":
            return _ok(incident_id=inc.id, status="resolved", runbook_skill=inc.runbook)
        inc.status, inc.resolved_at = "resolved", time.time()
        inc.outcome = f"Closed by hand: {one_line(args.get('root_cause'), 300)}"
        inc.mark("closed by hand, no runbook written")
        self.notifier.post(inc)
        self._save_incident(inc)
        with self._lock:
            self.guarded_sessions -= self._session_ids(kwargs)
        return _ok(incident_id=inc.id, status="resolved", runbook_skill=None,
                   message="Closed. No runbook: only approved, verified fixes are learned.")

    # ---- approval and the deterministic last mile ---------------------------
    def authorize(self, platform: str, user_id: str) -> str:
        """Who may approve from a chat button. Returns '' if allowed, else why not. Fails closed."""
        allowed = {str(a).strip() for a in (self.cfg("approvers", []) or [])}
        if f"{platform}:{user_id}" in allowed or str(user_id) in allowed:
            return ""
        return (f"You are not on Night Shift's approvers list. To allow this, add '{platform}:{user_id}' "
                "to plugins.entries.night-shift.settings.approvers.")

    def authorize_command(self, platform: str, source) -> str:
        """Who may approve by slash command. Hermes only delivers commands from its authorised users;
        if an approvers list is set, it applies here too."""
        if source is None:
            return "" if platform in LOCAL_SURFACES else "Refusing: this command came with no identity."
        if self.cfg("approvers", []):
            return self.authorize(platform, str(getattr(source, "user_id", "") or ""))
        return ""

    def begin_approval(self, incident_id: str, actor: str) -> tuple[Incident | None, str]:
        """Atomically claim the incident, so a double tap or a Slack retry runs nothing twice."""
        with self._lock:
            inc = self.get_incident(incident_id)
            if not inc or not inc.proposed_fix:
                return None, f"No proposed fix for {incident_id or '(missing id)'}."
            if inc.status != "fix_proposed":
                return None, f"{inc.id} is already {inc.status.replace('_', ' ')}."
            ttl = float(self.cfg("approval_ttl_minutes", 60)) * 60
            if time.time() - (inc.proposed_at or 0) > ttl:
                return None, f"The proposal for {inc.id} is too old to run safely. Re-run the investigation."
            inc.status, inc.approved_at, inc.approved_by = "fixing", time.time(), actor
            inc.mark(f"approved by {actor}")
            self._save_incident(inc)
        self.notifier.post(inc)
        self._save_incident(inc)
        return inc, ""

    def run_fix(self, inc: Incident) -> str:
        """Run exactly the commands that were shown, check recovery, then learn. Never raises."""
        fix = inc.proposed_fix or {}
        log: list[str] = []
        try:
            for cmd in fix.get("commands", []):
                code, out = self._exec(cmd, timeout=120)
                inc.mark(f"ran: {cmd} (exit {code})")
                log.append(f"$ {cmd}\n{out[-800:]}" + (f"\n(exit code {code})" if code else ""))
                if code:
                    return self._fail(inc, f"Command failed: `{cmd}` (exit {code}). Later commands were not run.", log)
            if not self._await_recovery(fix):
                wait = int(self.cfg("verify_timeout_seconds", 90))
                return self._fail(inc, f"The fix ran but the recovery check did not pass within {wait}s.", log)
            return self._finish(inc, log)
        except Exception as exc:
            logger.exception("Night Shift run_fix failed for %s", inc.id)
            return self._fail(inc, f"Night Shift hit an error while fixing: {exc}", log)

    def _verified(self, fix: dict) -> bool | None:
        """True if every check passes, False if one fails, None if it cannot tell."""
        try:
            if (promql := fix.get("verify_promql")) and not self._prometheus(promql):
                return False
        except Exception as exc:
            logger.info("Night Shift: recovery check unavailable: %s", exc)
            return None
        if (check := fix.get("verify_command")) and self._exec(check)[0] != 0:
            return False
        return True

    def _await_recovery(self, fix: dict) -> bool:
        deadline = time.time() + float(self.cfg("verify_timeout_seconds", 90))
        interval = float(self.cfg("verify_interval_seconds", 3))
        while True:
            if self._verified(fix) is True:
                return True
            if time.time() >= deadline:
                return False
            time.sleep(interval)

    def _fail(self, inc: Incident, why: str, log: list[str]) -> str:
        inc.status, inc.outcome = "fix_failed", why
        inc.mark(f"fix failed: {why}")
        if inc.recalled_from:
            for rb in self.runbooks():
                if rb.skill_name == inc.recalled_from:
                    rb.failures += 1
                    self._save_runbook(rb)
        self.notifier.post(inc)
        self._save_incident(inc)
        return "\n".join([f"{inc.id}: {why}", *log])

    def _finish(self, inc: Incident, log: list[str]) -> str:
        inc.status, inc.resolved_at = "resolved", time.time()
        inc.mark("recovery verified")
        rb, written = self._learn(inc)
        inc.runbook = rb.skill_name
        verb = "updated" if len(rb.history) > 1 else "wrote"
        note = f"{verb} runbook `{rb.skill_name}`" if written else f"kept runbook `{rb.skill_name}` in plugin state"
        inc.outcome = f"Recovery verified {duration(inc.time_to_recover)} after approval. Night Shift {note}."
        if len(rb.history) > 1:
            first = rb.history[0]
            inc.outcome += (f" Card in {duration(inc.time_to_card)} this time, "
                            f"{duration(first.get('time_to_card'))} the first time.")
        self.notifier.post(inc)
        self._save_incident(inc)
        return "\n".join([f"{inc.id}: {inc.outcome}", *log])

    def _learn(self, inc: Incident) -> tuple[Runbook, bool]:
        """Everything executable here comes from the approved proposal, not from later model output."""
        fix = inc.proposed_fix or {}
        name = skill_name_for(inc.alertname, inc.service)
        rb = next((r for r in self.runbooks() if r.skill_name == name), None)
        if rb is None:
            rb = Runbook(skill_name=name, alertname=inc.alertname, service=inc.service, root_cause="",
                         fix_commands=[], verification="", symptoms="", learned_from=inc.id)
        rb.root_cause, rb.symptoms = fix.get("diagnosis", ""), fix.get("symptoms", "")
        rb.fix_commands, rb.verification = list(fix.get("commands", [])), fix.get("verification", "")
        rb.verify_promql, rb.verify_command = fix.get("verify_promql", ""), fix.get("verify_command", "")
        rb.risk = fix.get("risk", rb.risk)
        rb.history.append({"incident": inc.id, "at": inc.resolved_at, "time_to_card": inc.time_to_card,
                           "time_to_recover": inc.time_to_recover, "approved_by": inc.approved_by,
                           "recalled": bool(inc.recalled_from)})
        self._save_runbook(rb)
        return rb, self._write_skill(name, render_skill(rb, inc))

    def _write_skill(self, name: str, content: str) -> bool:
        """Write the runbook as a real Hermes skill through the built-in skill_manage tool."""
        category = str(self.cfg("runbook_category", "night-shift"))
        try:
            result = self._skill_op({"action": "create", "name": name, "content": content, "category": category})
            if "already exists" in str(result.get("error", "")):
                result = self._skill_op({"action": "patch", "name": name, "content": content})
            if result.get("staged"):  # the user's write gate holds it for review: still a success
                logger.info("Night Shift: runbook skill %s staged for review", name)
            if not result.get("success"):
                logger.warning("Night Shift: runbook skill %s not written: %s", name, result.get("error"))
            return bool(result.get("success"))
        except Exception as exc:
            logger.warning("Night Shift: runbook skill %s not written: %s", name, exc)
            return False  # the runbook is still in plugin state, so recall keeps working

    def _skill_op(self, op: dict) -> dict:
        raw = self.ctx.dispatch_tool("skill_manage", {"operations": [op]})
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, dict) else {}

    def approve(self, incident_id: str, actor: str = "local") -> str:
        inc, problem = self.begin_approval(incident_id, actor)
        return problem or self.run_fix(inc)

    def deny(self, incident_id: str, actor: str = "local") -> str:
        with self._lock:
            inc = self.get_incident(incident_id)
            if not inc:
                return f"Unknown incident {incident_id}."
            if inc.status != "fix_proposed":
                return f"{inc.id} is already {inc.status.replace('_', ' ')}."
            inc.status, inc.denied_by = "denied", actor
            inc.mark(f"denied by {actor}")
            self._save_incident(inc)
        self.notifier.post(inc)
        self._save_incident(inc)
        return f"Fix for {inc.id} denied. Nothing was run. The incident is yours."

    # ---- reporting -----------------------------------------------------------
    def status(self) -> str:
        active = self.open_incidents()
        books = self.runbooks()
        lines = [f"Night Shift: {len(active)} open incident(s), {len(books)} learned runbook(s)."]
        lines += [f"- {i.id} [{i.status}] {i.alertname} on {i.service}" for i in active]
        if books:
            lines.append("Runbooks:")
            lines += [f"- {r.skill_name} (from {r.learned_from}, resolved {len(r.history)}x)" for r in books]
        return "\n".join(lines)

    def stats(self) -> str:
        books = [r for r in self.runbooks() if r.history]
        if not books:
            return "Night Shift has not resolved anything yet."
        lines = ["Time from alert to a proposed fix, and from approval to verified recovery:"]
        for rb in books:
            lines.append(f"\n{rb.skill_name}")
            for n, h in enumerate(rb.history, start=1):
                tag = "recalled" if h.get("recalled") else "learned"
                lines.append(f"  #{n} {h['incident']} ({tag}): card {duration(h.get('time_to_card'))}, "
                             f"recovered {duration(h.get('time_to_recover'))}, approved by {h.get('approved_by')}")
        return "\n".join(lines)

    def show(self, incident_id: str) -> str:
        inc = self.get_incident(incident_id)
        if not inc:
            return f"Unknown incident {incident_id}."
        fix = inc.proposed_fix or {}
        start = inc.opened_at
        lines = [f"{inc.id} [{inc.status}] {inc.alertname} on {inc.service}"]
        if fix:
            lines += [f"Cause: {fix.get('diagnosis')}", "Fix:", *[f"  $ {c}" for c in fix.get("commands", [])]]
        lines += [f"Outcome: {inc.outcome or inc.escalation or '(in progress)'}", "Timeline:"]
        lines += [f"  +{duration(e['t'] - start)} {e['event']}" for e in inc.timeline]
        return "\n".join(lines)

    def forget(self, what: str) -> str:
        """Delete a learned runbook, or with 'all' every runbook and incident."""
        books = self.runbooks()
        targets = books if what == "all" else [r for r in books if r.skill_name == what]
        if what != "all" and not targets:
            return f"No runbook called {what}."
        for rb in targets:
            try:
                self._skill_op({"action": "delete", "name": rb.skill_name})
            except Exception as exc:
                logger.warning("Night Shift: could not delete skill %s: %s", rb.skill_name, exc)
        with self._lock:
            keep = [] if what == "all" else [r.to_dict() for r in books if r.skill_name != what]
            self.ctx.state.set("runbooks", keep)
            if what == "all":
                self.ctx.state.set("incidents", {})
        return f"Forgot {len(targets)} runbook(s)." + (" Incident history cleared." if what == "all" else "")

    # ---- guard hook ---------------------------------------------------------
    def _is_alert_run(self) -> bool:
        """An alert-triggered run is guarded from its very first tool call, not just after diagnose."""
        try:
            from gateway.session_context import get_session_env
            user = get_session_env("HERMES_SESSION_USER_ID", "")
        except Exception:
            return False
        return user == f"webhook:{self.cfg('webhook_route', 'nightshift')}"

    def pre_tool_call(self, tool_name: str = "", args: dict | None = None, task_id: str = "", **kwargs):
        mode = str(self.cfg("guard_mode", "incident"))
        if mode == "off":
            return None
        ids = {i for i in (task_id, kwargs.get("session_id", "")) if i}
        if mode == "incident" and not (ids & self.guarded_sessions) and not self._is_alert_run():
            return None
        return policy.evaluate(tool_name, args or {})
