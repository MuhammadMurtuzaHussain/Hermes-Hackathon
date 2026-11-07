# Night Shift: build plan

Deadline: 22:00 Dublin (5PM EST), Wednesday 11 November 2026.

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

## Status

Built and verified against real Hermes on 7 November. See [FINDINGS.md](FINDINGS.md) for what the
live runs found, and [submission/](submission/README.md) for what is left.

## Demo script

The current script and shot list are in [submission/demo-script.md](submission/demo-script.md).

## Stretch goals

Done: Slack approve and deny buttons, and `/nightshift stats` as the time-to-resolve comparison.

Not done, in priority order:

1. NIS2 module: if severity is critical and customer-facing, start the 24-hour, 72-hour and
   one-month reporting clock and draft the early warning for the national CSIRT (NCSC in Ireland).
2. Kubernetes checks (`kubectl` is already in the read-only list).
3. Shared runbooks across a team via the plugin catalog.

## Open questions, resolved

| Question | Answer from the live runs |
|---|---|
| Official rules and submission format | A public PR to the plugin catalog, a demo video on X tagging NousResearch, and the Typeform. All six form fields are required, and it asks for the PR and X links |
| Does the route `skills:` field take `night-shift:triage`? | No. The gateway logs "Skill not found", because that field only sees installed skills. The prompt loads it with `skill_view` instead, and only when no runbook matches |
| Does `dispatch_tool("skill_manage")` work from a webhook run? | Yes, also from a Slack button's worker thread, with `{"operations": [...]}`. There is no `edit` action: updating is `patch` with `content`. Hermes refuses a description over 60 characters. The write gate is off by default; if on, the write is staged, and recall still works from plugin state |
| Is the guard key `task_id` or `session_id`? | Hooks get both. The guard records and checks both, so it does not depend on which one a run uses |
