#!/usr/bin/env python3
"""Run the whole Night Shift loop for real, repeatedly, and fail loudly if any step breaks.

Each run: reset to healthy, ship the leaky release, wait for Prometheus and Alertmanager to fire,
wait for the agent's card in mock Slack, click Approve the way Slack would, then check the API
really recovered on the good release. Run 1 must learn a runbook; later runs must recall it.

    ./sandbox.sh setup && ./sandbox.sh gateway
    ./e2e.py --runs 3 --fresh
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEMO = Path(__file__).resolve().parent
SLACK = "http://localhost:8770"
PROM = "http://localhost:9090"
API = f"http://localhost:{os.environ.get('API_PORT', '18090')}"
HEALTHY_DB = 0.5


def http(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode() or "{}")


def sh(*cmd):
    return subprocess.run(cmd, cwd=DEMO, check=True, capture_output=True, text=True).stdout.strip()


def hermes(*args):
    return sh("./sandbox.sh", "hermes", *args)


def wait(what, check, timeout, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if (value := check()):
                return value
        except Exception:
            pass
        time.sleep(interval)
    raise SystemExit(f"FAILED: timed out after {timeout}s waiting for {what}")


def db_saturation():
    query = urllib.parse.quote("sum(pg_stat_activity_count) / max(pg_settings_max_connections)")
    result = http(f"{PROM}/api/v1/query?query={query}")["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def firing():
    return [a["labels"]["alertname"] for a in http(f"{PROM}/api/v1/alerts")["data"]["alerts"]]


def text_of(message):
    parts = []
    for block in message["blocks"]:
        if "text" in block and isinstance(block["text"], dict):
            parts.append(block["text"]["text"])
        for element in block.get("elements", []):
            if element.get("type") == "mrkdwn":
                parts.append(element["text"])
    return "\n".join(parts)


def card():
    messages = http(f"{SLACK}/ui/state")["messages"]
    return messages[-1] if messages else None


def buttons(message):
    return next((b["elements"] for b in message["blocks"] if b["type"] == "actions"), None)


def healthy():
    health = http(f"{API}/health")
    saturation = db_saturation()
    ready = health.get("release") == "v1" and saturation is not None and saturation < HEALTHY_DB
    return ready and not firing()


def one_run(n, learned_before):
    extra = http(f"{SLACK}/ui/state")["messages"][1:]
    if extra:  # one failure must be one card, even after the alerts settle
        raise SystemExit(f"FAILED: the last incident produced extra cards: {[m['text'] for m in extra]}")
    http(f"{SLACK}/ui/clear", {})
    sh("./chaos/reset.sh")
    wait("the stack to be healthy", healthy, 90, 1)
    start = time.time()
    sh("./chaos/bad-deploy.sh")
    print(f"run {n}: leaky release deployed")

    proposal = wait("the fix proposal in Slack", lambda: (m := card()) and buttons(m) and m, 180)
    to_card = time.time() - start
    body = text_of(proposal)
    incident = re.search(r"INC-[0-9A-F]{6}", json.dumps(proposal["blocks"])).group(0)
    print(f"run {n}: {incident} card {to_card:.0f}s after the deploy")
    if "./deploy.sh v1" not in body and "deploy.sh v1" not in body:
        raise SystemExit(f"FAILED: the fix is not a rollback to v1:\n{body}")
    recognised = "Recognised" in body
    if learned_before and not recognised:
        raise SystemExit(f"FAILED: run {n} did not recall the runbook:\n{body}")
    if not learned_before and recognised:
        raise SystemExit("FAILED: a runbook was recalled before one was learned")
    saturation = db_saturation()
    if not saturation or saturation < 0.8:
        raise SystemExit(f"FAILED: the database was not actually saturated ({saturation})")

    clicked = time.time()
    http(f"{SLACK}/ui/click", {"ts": proposal["ts"], "action_id": "nightshift_approve", "user": "U0ONCALL"})
    done = wait("the card to finish", lambda: (t := text_of(card())) and ("Resolved." in t or "Needs a human" in t) and t, 120)
    recovered = time.time() - clicked
    if "Resolved." not in done:
        raise SystemExit(f"FAILED: the fix did not resolve:\n{done}")
    if buttons(card()):
        raise SystemExit("FAILED: the buttons are still on the resolved card")
    if http(f"{API}/health").get("release") != "v1":
        raise SystemExit("FAILED: the API is not on v1")
    orders = [urllib.request.urlopen(f"{API}/orders", timeout=5).status for _ in range(5)]
    if set(orders) != {200}:
        raise SystemExit(f"FAILED: orders are still failing: {orders}")
    if db_saturation() >= HEALTHY_DB:
        raise SystemExit("FAILED: the database is still saturated")
    print(f"run {n}: resolved {recovered:.0f}s after approval. recalled={recognised}")
    return {"run": n, "incident": incident, "card_after_deploy": round(to_card), "recovery": round(recovered),
            "recalled": recognised}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--fresh", action="store_true", help="forget runbooks first, so run 1 has to learn")
    args = parser.parse_args()

    if args.fresh:
        print(hermes("nightshift", "forget", "all"))
    learned = "resolved" in hermes("nightshift", "status")
    if not http(f"{SLACK}/ui/state")["connected"]:
        raise SystemExit("FAILED: Hermes is not connected to mock Slack. Run ./sandbox.sh gateway")

    results = []
    for n in range(1, args.runs + 1):
        results.append(one_run(n, learned))
        learned = True

    rejected = http(f"{SLACK}/ui/state")["rejected"]
    if rejected:
        raise SystemExit(f"FAILED: Slack would have rejected a card: {rejected}")

    print(f"\nPASS: {len(results)} of {args.runs} runs recovered for real.\n")
    print(f"{'run':<5}{'incident':<13}{'alert to card':<15}{'recalled':<10}{'recovery after approval'}")
    for r in results:
        print(f"{r['run']:<5}{r['incident']:<13}{str(r['card_after_deploy']) + 's':<15}{str(r['recalled']):<10}{r['recovery']}s")
    print("\n" + hermes("nightshift", "stats"))
    skill = Path(os.environ.get("NIGHTSHIFT_HERMES_HOME", Path.home() / ".hermes-nightshift"))
    found = list(skill.glob("skills/**/runbook-api-*/SKILL.md"))
    print(f"\nRunbook skill on disk: {found[0] if found else 'NOT FOUND'}")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
