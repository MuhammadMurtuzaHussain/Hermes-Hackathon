# Night Shift

**An on-call engineer for Hermes Agent that never sleeps and gets better after every incident.**

Entry for the Nous Research Hermes Agent Plugin Hackathon (November 2026).

## The problem

On-call is one of the biggest causes of burnout in engineering. An alert fires at 3am, someone
wakes up, spends twenty minutes working out what broke, applies the same fix as last time, and
goes back to bed. The knowledge of how to fix it lives in their head, or in a runbook nobody
has updated since 2023.

## What Night Shift does

1. **Picks up the alert.** Alertmanager (or Grafana, PagerDuty) posts to a Hermes webhook route.
2. **Investigates, read-only.** Hermes checks container status, logs, resources, disk, database
   connections, recent deploys and Prometheus metrics. A guard on every tool call blocks
   anything that could change production.
3. **Proposes a fix in Slack or Telegram.** Cause, evidence, exact commands, risk, and an
   approve or deny command. A human decides.
4. **Learns.** Once the fix is verified, Night Shift writes the runbook itself, as a real Hermes
   skill. The next time the same alert fires, it recognises it and goes straight to the known fix.

The demo: break a service, approve Hermes's fix from your phone, then break it the same way
again and watch it resolve in seconds using the runbook it wrote the first time.

## Why only Hermes can do this

| Hermes capability | How Night Shift uses it |
|---|---|
| Always-on gateway and webhooks | Alerts trigger agent runs with no human in the loop |
| Lives in your chat apps | Diagnosis and approval happen in Slack or Telegram |
| Runs commands locally, over SSH or in Docker | Diagnostics run on the real infrastructure |
| `pre_tool_call` hook with approval escalation | Investigation is read-only by construction |
| Self-written skills | Every resolved incident becomes a reusable runbook |

## Repository layout

```
night-shift/        The Hermes plugin (the hackathon entry)
  plugin.yaml       Manifest
  __init__.py       register(ctx): tools, guard hook, /nightshift command, triage skill
  tools.py          Diagnose, metrics, recall, propose, resolve, approval flow
  policy.py         Read-only command classifier used by the guard
  runbooks.py       Incident and runbook model, matching, SKILL.md rendering
  skills/triage/    The incident protocol the agent follows
  config/           Example webhook route and plugin settings
  tests/            Unit tests (no Hermes install needed)
demo/               Docker stack to stage real incidents
  app/              Orders API with a deliberately leaky release
  prometheus/ alertmanager/   Monitoring and alert rules
  relay/            Signs Alertmanager webhooks with Hermes's HMAC V2 scheme
  chaos/            bad-deploy.sh, kill-api.sh, reset.sh, fake-alert.sh
docs/PLAN.md        Build plan, architecture, demo script, open questions
side-projects/      Sentinel and Rewind, parked for after the hackathon
```

## Quick start

```bash
# 1. Install the plugin
ln -s "$PWD/night-shift" ~/.hermes/plugins/night-shift
hermes plugins enable night-shift
hermes plugins doctor night-shift

# 2. Add the webhook route: merge night-shift/config/webhook-route.example.yaml into ~/.hermes/config.yaml
export NIGHTSHIFT_WEBHOOK_SECRET=$(openssl rand -hex 32)
hermes gateway

# 3. Start the demo stack and break something
cd demo && cp .env.example .env   # put the same secret in .env
docker compose up -d --build
./chaos/bad-deploy.sh
```

Run the tests with `python3 -m unittest discover -s night-shift/tests -v`.

## Safety model

- Investigation is read-only. Mutating commands are escalated to Hermes's approval gate.
- Fixes run only after `/nightshift approve <id>`, and only the exact commands that were shown.
- Alert content is treated as untrusted input, never as instructions.
- Webhooks are HMAC-signed with replay protection.

## Licence

MIT
