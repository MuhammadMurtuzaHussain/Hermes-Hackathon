# Night Shift: build plan

Deadline: 11 November 2026 (confirm on the official rules page).

## Architecture

```
Prometheus ──alert──▶ Alertmanager ──▶ relay (HMAC V2) ──▶ Hermes webhook route "nightshift"
                                                              │  coalesce + filter + triage skill
                                                              ▼
                                                     Hermes agent run
             ┌──────────────────────────────┬──────────────┴───────────────┬──────────────────────┐
     nightshift_diagnose           nightshift_query_metrics        nightshift_recall       nightshift_propose_fix
     (read-only checks via          (PromQL, read-only)            (learned runbooks)      (stored, not executed)
      the terminal tool)                                                                        │
             ▲                                                                                  ▼
     pre_tool_call guard  ◀── blocks or escalates anything mutating          Slack/Telegram: "/nightshift approve INC-…"
                                                                                                │
                                                                     approved commands run ─────┘
                                                                                                ▼
                                                               nightshift_resolve ──▶ skill_manage: runbook skill written
```

## What is built (day 1)

- Plugin with five tools, the guard hook, `/nightshift` command and the triage skill.
- Read-only command classifier, incident store, runbook matching and SKILL.md rendering.
- Six unit tests covering the guard, matching and the full learn-then-recall loop.
- Demo stack: orders API with a leaky release, Postgres, exporter, Prometheus, Alertmanager,
  signing relay and chaos scripts.

Not yet verified against a live Hermes install or a running Docker stack. That is the first job.

## Day-by-day

| Day | Goal | Done when |
|---|---|---|
| 1 (6 Nov) | Scaffold, tests, demo stack | This commit |
| 2 (7 Nov) | Run it for real: `hermes plugins doctor`, gateway, webhook route, `fake-alert.sh` end to end | An alert produces a diagnosis in Telegram or Slack |
| 3 (8 Nov) | Scenario 1 (leaky deploy) end to end, including approval and runbook written | Second run recalls the runbook |
| 4 (9 Nov) | Slack Approve/Deny buttons (`register_slack_action_handler`), timing metrics ("resolved in 3 min, then 20 s") | One-tap approval from a phone |
| 5 (10 Nov) | Scenario 2 (API down), polish messages, record demo video | Video under 2 minutes |
| 6 (11 Nov) | README, write-up, tweet, submission, catalog listing (`hermes plugins validate`) | Submitted |

## Demo script (target 90 seconds)

1. **Hook (10 s).** "It's 3am. Your database just ran out of connections. Who do you want to wake up?"
2. **Break it (10 s).** Run `bad-deploy.sh`. Grafana or Prometheus shows connections climbing.
3. **Night Shift responds (25 s).** The phone buzzes: cause, evidence (release v2-leaky deployed
   at 03:02, connections 38/40), fix (roll back to v1), risk low. Tap approve. The service recovers.
4. **It learns (10 s).** Show the runbook skill it just wrote.
5. **Again (20 s).** `reset.sh`, then `bad-deploy.sh` again. This time the message opens with
   "Recognised from INC-…" and the fix arrives in seconds.
6. **Close (15 s).** "Read-only by default. Human approval for every change. Gets better
   every night."

## Stretch goals, in priority order

1. Slack buttons for approval.
2. A "time to resolve" chart comparing first and second occurrences.
3. NIS2 module: if severity is critical and customer-facing, start the 24-hour, 72-hour and
   one-month reporting clock and draft the early warning for the national CSIRT (NCSC in Ireland).
4. Kubernetes checks (`kubectl` is already in the read-only list).
5. Shared runbooks across a team via the plugin catalog.

## Open questions

- Confirm the official rules, judging criteria and submission format (tweet, video, form?).
- Does the route `skills:` field accept plugin-namespaced skills (`night-shift:triage`)?
  The prompt also calls `skill_view` as a fallback.
- Does `ctx.dispatch_tool("skill_manage", …)` work from a webhook-triggered session, and does
  it trigger Hermes's skill write gate? If not, fall back to asking the agent to call it.
- Is the guard's session key `task_id` or `session_id` in webhook runs? Check in a live run.
