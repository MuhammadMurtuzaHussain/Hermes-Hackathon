"""Tool handlers and incident state for Night Shift.

Handlers follow the Hermes contract: take (args, **kwargs), always return a JSON
string, never raise. All Hermes access goes through the PluginContext passed to
NightShift, so this module stays testable with a fake context.
"""

from __future__ import annotations

import json
import shlex
import time
import urllib.parse
import urllib.request

from . import policy
from .runbooks import Incident, Runbook, best_match, render_skill, skill_name_for


def _ok(**data) -> str:
    return json.dumps(data, default=str)


def _err(message: str, **data) -> str:
    return json.dumps({"error": message, **data}, default=str)


class NightShift:
    """Holds the plugin context and the incident and runbook stores."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.approved_commands: set[str] = set()
        self.guarded_tasks: set[str] = set()

    # ---- storage (ctx.state is profile-scoped and atomic) -----------------
    def _incidents(self) -> dict[str, dict]:
        return self.ctx.state.get("incidents", default={}) or {}

    def _save_incident(self, inc: Incident) -> None:
        data = self._incidents()
        data[inc.id] = inc.to_dict()
        self.ctx.state.set("incidents", data)

    def get_incident(self, incident_id: str) -> Incident | None:
        raw = self._incidents().get(incident_id)
        return Incident.from_dict(raw) if raw else None

    def open_incidents(self) -> list[Incident]:
        return [Incident.from_dict(d) for d in self._incidents().values() if d.get("status") != "resolved"]

    def runbooks(self) -> list[Runbook]:
        return [Runbook.from_dict(d) for d in (self.ctx.state.get("runbooks", default=[]) or [])]

    def _save_runbook(self, rb: Runbook) -> None:
        books = [r for r in self.runbooks() if r.skill_name != rb.skill_name] + [rb]
        self.ctx.state.set("runbooks", [r.to_dict() for r in books])

    def _cfg(self, key: str, default):
        try:
            return self.ctx.get_config(key, default=default)
        except Exception:
            return default

    # ---- helpers ----------------------------------------------------------
    def _full(self, command: str) -> str:
        """Apply the optional remote prefix, e.g. 'ssh ops@prod-1'."""
        prefix = str(self._cfg("docker_host_prefix", "") or "").strip()
        return f"{prefix} {shlex.quote(command)}" if prefix else command

    def _run(self, command: str) -> str:
        full = self._full(command)
        try:
            raw = self.ctx.dispatch_tool("terminal", {"command": full, "timeout": 30})
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):  # terminal returns {"output", "exit_code", "error"}
                text = str(parsed.get("output") or "")
                if parsed.get("error"):
                    text += f"\n(error: {parsed['error']})"
                if parsed.get("exit_code") not in (None, 0):
                    text += f"\n(exit code {parsed['exit_code']})"
                return (text.strip() or "(no output)")[:4000]
            return str(parsed)[:4000]
        except Exception as exc:  # never let one failed check sink the triage
            return f"(check failed: {exc})"

    @staticmethod
    def checks_for(service: str) -> dict[str, str]:
        s = shlex.quote(service)
        return {
            "status": f"docker ps -a --filter name={s} --format '{{{{.Names}}}} {{{{.Status}}}} {{{{.Image}}}}'",
            "logs": f"docker logs --tail 80 --timestamps {s} 2>&1 | tail -n 80",
            "resources": f"docker stats --no-stream --format '{{{{.Name}}}} cpu={{{{.CPUPerc}}}} mem={{{{.MemPerc}}}}' {s}",
            "disk": "df -h",
            "db": ("docker exec postgres psql -U postgres -t -c \"SELECT state, count(*) FROM pg_stat_activity "
                   "GROUP BY state\""),
            "deploys": f"docker inspect --format '{{{{.Created}}}} {{{{.Config.Image}}}}' {s}",
        }

    def _task(self, kwargs) -> str:
        return str(kwargs.get("task_id") or kwargs.get("session_id") or "")

    # ---- tools ------------------------------------------------------------
    def diagnose(self, args: dict, **kwargs) -> str:
        try:
            service, alertname = args.get("service", "").strip(), args.get("alertname", "").strip()
            if not service or not alertname:
                return _err("service and alertname are required")
            inc = self.get_incident(args.get("incident_id", "")) or Incident(
                alertname=alertname, service=service, summary=args.get("summary", ""))
            if task := self._task(kwargs):
                self.guarded_tasks.add(task)
            catalogue = self.checks_for(service)
            wanted = args.get("checks") or list(catalogue)
            results = {name: self._run(catalogue[name]) for name in wanted if name in catalogue}
            inc.evidence.append(f"{time.strftime('%H:%M:%S')} diagnose: {', '.join(results)}")
            self._save_incident(inc)
            return _ok(incident_id=inc.id, service=service, alertname=alertname, results=results,
                       next_step="Call nightshift_recall with what you found.")
        except Exception as exc:
            return _err(f"diagnose failed: {exc}")

    def query_metrics(self, args: dict, **kwargs) -> str:
        promql = (args.get("promql") or "").strip()
        if not promql:
            return _err("promql is required")
        base = str(self._cfg("prometheus_url", "http://localhost:9090")).rstrip("/")
        url = f"{base}/api/v1/query?{urllib.parse.urlencode({'query': promql})}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # read-only GET
                body = json.loads(resp.read().decode())
            return _ok(promql=promql, result=body.get("data", {}).get("result", [])[:20])
        except Exception as exc:
            return _err(f"Prometheus query failed: {exc}", promql=promql)

    def recall(self, args: dict, **kwargs) -> str:
        match = best_match(self.runbooks(), args.get("alertname", ""), args.get("service", ""),
                           args.get("symptoms", ""))
        if not match:
            return _ok(match=None, message="No learned runbook yet. Investigate from first principles.")
        rb, confidence = match
        rb.times_used += 1
        self._save_runbook(rb)
        return _ok(match=rb.skill_name, confidence=confidence, learned_from=rb.learned_from,
                   root_cause_last_time=rb.root_cause, fix_last_time=rb.fix_commands,
                   instruction=f"Load skill '{rb.skill_name}', confirm the evidence matches, then propose its fix.")

    def propose_fix(self, args: dict, **kwargs) -> str:
        inc = self.get_incident(args.get("incident_id", ""))
        if not inc:
            return _err("unknown incident_id")
        commands = [c for c in (args.get("commands") or []) if str(c).strip()]
        if not commands:
            return _err("at least one command is required")
        inc.proposed_fix = {k: args.get(k) for k in ("diagnosis", "risk", "verification")} | {"commands": commands}
        inc.status = "fix_proposed"
        self._save_incident(inc)
        return _ok(incident_id=inc.id, status=inc.status,
                   approval=f"/nightshift approve {inc.id}", deny=f"/nightshift deny {inc.id}",
                   instruction=("Tell the on-call engineer the diagnosis, the exact commands, the risk, "
                                "and how to approve. Do not run the commands yourself."))

    def resolve(self, args: dict, **kwargs) -> str:
        inc = self.get_incident(args.get("incident_id", ""))
        if not inc:
            return _err("unknown incident_id")
        inc.status, inc.resolved_at = "resolved", time.time()
        rb = Runbook(
            skill_name=skill_name_for(inc.alertname, inc.service), alertname=inc.alertname,
            service=inc.service, root_cause=args.get("root_cause", ""),
            fix_commands=list(args.get("fix_commands") or []), verification=args.get("verification", ""),
            symptoms=args.get("symptoms", ""), learned_from=inc.id,
        )
        skill_md = render_skill(rb, inc)
        written = self._write_skill(rb.skill_name, skill_md)
        inc.runbook = rb.skill_name
        self._save_incident(inc)
        self._save_runbook(rb)
        self.guarded_tasks.discard(self._task(kwargs))
        minutes = max(1, round((inc.resolved_at - inc.opened_at) / 60))
        return _ok(incident_id=inc.id, status="resolved", time_to_resolve_min=minutes,
                   runbook_skill=rb.skill_name, skill_written=written,
                   message=f"Resolved in {minutes} min. Learned runbook '{rb.skill_name}' for next time.")

    def _write_skill(self, name: str, content: str) -> bool:
        """Write the runbook as a real Hermes skill through the built-in skill_manage tool."""
        category = str(self._cfg("runbook_category", "night-shift"))
        op = {"action": "create", "name": name, "content": content, "category": category}
        try:
            raw = self.ctx.dispatch_tool("skill_manage", {"operations": [op]})
            if "already exists" in str(raw).lower():
                raw = self.ctx.dispatch_tool("skill_manage", {"operations": [
                    {"action": "edit", "name": name, "content": content}]})
            return "error" not in str(raw).lower()[:200]
        except Exception:
            return False  # runbook still kept in plugin state for nightshift_recall

    # ---- approval flow (slash command) -------------------------------------
    def approve(self, incident_id: str) -> str:
        inc = self.get_incident(incident_id)
        if not inc or not inc.proposed_fix:
            return f"No proposed fix for {incident_id or '(missing id)'}."
        inc.status = "fixing"
        self._save_incident(inc)
        lines = [f"Approved fix for {inc.id}. Running {len(inc.proposed_fix['commands'])} command(s):"]
        for cmd in inc.proposed_fix["commands"]:
            self.approved_commands.add(policy.normalise(self._full(cmd)))
            out = self._run(cmd)
            lines.append(f"\n$ {cmd}\n{out[:800]}")
        inc.evidence.append(f"{time.strftime('%H:%M:%S')} fix approved and executed")
        self._save_incident(inc)
        lines.append(f"\nVerify: {inc.proposed_fix.get('verification', '')}")
        return "\n".join(lines)

    def deny(self, incident_id: str) -> str:
        inc = self.get_incident(incident_id)
        if not inc:
            return f"Unknown incident {incident_id}."
        inc.status, inc.proposed_fix = "open", None
        self._save_incident(inc)
        return f"Fix for {inc.id} denied. The incident stays open for a human to take over."

    def status(self) -> str:
        open_ = self.open_incidents()
        books = self.runbooks()
        lines = [f"Night Shift: {len(open_)} open incident(s), {len(books)} learned runbook(s)."]
        lines += [f"- {i.id} [{i.status}] {i.alertname} on {i.service}" for i in open_]
        if books:
            lines.append("Runbooks:")
            lines += [f"- {r.skill_name} (from {r.learned_from}, used {r.times_used}x)" for r in books]
        return "\n".join(lines)

    # ---- guard hook ---------------------------------------------------------
    def pre_tool_call(self, tool_name: str = "", args: dict | None = None, task_id: str = "", **kwargs):
        mode = str(self._cfg("guard_mode", "incident"))
        if mode == "off":
            return None
        if mode == "incident" and (task_id or kwargs.get("session_id", "")) not in self.guarded_tasks:
            return None
        return policy.evaluate(tool_name, args or {}, self.approved_commands)
