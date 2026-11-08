"""Record a real Night Shift run as data for the video: nothing here is invented."""
import importlib.util, json, threading, time, subprocess
from pathlib import Path
DEMO = Path(__file__).resolve().parents[2] / "demo"
spec = importlib.util.spec_from_file_location("e2e", DEMO / "e2e.py"); e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
OUT = Path(__file__).parent
T0 = None; samples = []; events = []; stop = False

def now(): return round(time.time() - T0, 2)
def event(kind, **kw): events.append({"t": now(), "kind": kind, **kw}); print(now(), kind, str(kw)[:120], flush=True)

def sampler():
    while not stop:
        try:
            st = e.http(f"{e.SLACK}/ui/state")
            alerts = e.http(f"{e.PROM}/api/v1/alerts")["data"]["alerts"]
            q = e.http(f"{e.PROM}/api/v1/query?query=sum(pg_stat_activity_count)")["data"]["result"]
            samples.append({"t": now(), "conns": float(q[0]["value"][1]) if q else None,
                            "alerts": [[a["labels"]["alertname"], a["state"]] for a in alerts],
                            "messages": st["messages"], "notes": st["notes"]})
        except Exception as exc:
            print("sample error", exc)
        time.sleep(0.5)

def run(cmd):
    out = subprocess.run(cmd, cwd=DEMO, capture_output=True, text=True, shell=True).stdout.strip()
    event("terminal", cmd=cmd, out=out)
    return out

print(e.hermes("nightshift", "forget", "all"))
e.stack_ready()
T0 = time.time()
threading.Thread(target=sampler, daemon=True).start()
time.sleep(3)
# Incident 1: learn
run("./chaos/bad-deploy.sh")
msg = e.wait("card 1", lambda: (m := e.card()) and e.buttons(m) and m, 180); event("card", n=1)
time.sleep(5); e.click(msg, "nightshift_approve"); event("approve", n=1)
e.finished(); event("resolved", n=1)
time.sleep(4)
skill = next(Path.home().glob(".hermes-nightshift/skills/night-shift/runbook-api-dbconnectionsexhausted/SKILL.md")).read_text()
event("skill", text=skill)
# reset between incidents, recorded but cut in the edit
run("./chaos/reset.sh")
e.wait("healthy", e.healthy, 120, 1); time.sleep(12)
e.http(f"{e.SLACK}/ui/clear", {}); event("clear")
# Incident 2: recall, plus a stranger's click
run("./chaos/bad-deploy.sh")
msg = e.wait("card 2", lambda: (m := e.card()) and e.buttons(m) and m, 180); event("card", n=2)
time.sleep(3); e.click(msg, "nightshift_approve", user="U0STRANGER"); event("stranger")
e.wait("note", lambda: e.http(f"{e.SLACK}/ui/state")["notes"], 15); time.sleep(3)
e.click(msg, "nightshift_approve"); event("approve", n=2)
e.finished(); event("resolved", n=2)
time.sleep(4)
event("stats", text=e.hermes("nightshift", "stats"))
stop = True; time.sleep(1)
guard = subprocess.run("grep -h 'Night Shift: guarding alert run\\|Night Shift guard: approve' ~/.hermes-nightshift/logs/agent.log | head -3", shell=True, capture_output=True, text=True).stdout
event("guardlog", text=guard)
(OUT / "timeline.json").write_text(json.dumps({"samples": samples, "events": events}))
print("saved", len(samples), "samples")
