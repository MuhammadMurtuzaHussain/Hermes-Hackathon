# Night Shift

**An on-call engineer for Hermes Agent. It investigates production alerts read-only, asks a human
to approve the fix from Slack, proves the fix worked, and turns it into a runbook for next time.**

On-call is the same loop every time: an alert fires at 3am, someone wakes up, spends twenty
minutes finding what broke, applies the fix from last time and goes back to bed. The knowledge
lives in their head. Night Shift does the investigation, keeps the human on the one decision that
matters, and writes down what worked.

```
Prometheus ─▶ Alertmanager ─▶ signed webhook ─▶ Hermes gateway ─▶ alert run
                                                                     │  read-only guard on every tool call
                                                                     ▼
                     nightshift_diagnose ─▶ known runbook? ─▶ nightshift_propose_fix
                                                                     │
                                         Slack card: cause, exact commands, risk, Approve / Deny
                                                                     │  a person taps
                                                                     ▼
              plugin code, no model: run those commands ─▶ poll recovery ─▶ write runbook skill
```

## What it does

- **Investigates with a guard on.** Container status, exit codes, logs, resources, disk, database
  connections, deploy history and Prometheus. Alert runs are read-only from their first tool call.
- **Asks one question.** One Slack card with the cause, the exact commands, the risk and two
  buttons. The card edits itself as the incident moves, so a channel reads as a short story.
- **Proves the fix.** After approval, plain code runs exactly what was shown and polls a recovery
  check. If the check fails, the card says so and asks for a person. A fix that does not pass is
  never recorded as a success.
- **Learns.** A verified fix becomes a real Hermes skill. Next time the same alert fires, the card
  opens with "Recognised" and the fix's track record, and the model skips the investigation.

## Why a plugin

A prompt can suggest a fix. It cannot do these:

| Hermes surface | What Night Shift does with it |
|---|---|
| Webhook routes and the always-on gateway | An alert starts an agent run with nobody at a keyboard |
| `pre_tool_call` with human escalation | Read-only by construction, not by asking the model nicely |
| Slack action handlers | One-tap approval, with an identity check on who tapped |
| `skill_manage` | Every verified fix becomes a skill you can read, edit or delete |
| Plugin state | Incidents and approvals survive a gateway restart |
| Terminal backends | The same checks work locally, over SSH or in Docker |

## Install

```bash
hermes plugins install night-shift
hermes plugins enable night-shift
```

Merge [`config/webhook-route.example.yaml`](config/webhook-route.example.yaml) into
`~/.hermes/config.yaml`, set `NIGHTSHIFT_WEBHOOK_SECRET` in `~/.hermes/.env`, and point your
monitoring at the route. Alertmanager cannot sign webhooks, so
[`demo/relay/relay.py`](../demo/relay/relay.py) is a small relay that adds Hermes's HMAC signature.

For the Slack card, connect the Slack platform (`hermes slack manifest --agent-view --write`, then
follow the [Slack guide](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/slack)),
and set `slack_channel` and `approvers`. Without Slack, set the route's `deliver` to any Hermes
platform and approve with `/nightshift approve <id>`.

## Commands

`/nightshift` in any chat, or `hermes nightshift` in a terminal:

| Command | What it does |
|---|---|
| `status` | Open incidents and learned runbooks |
| `stats` | Time to a proposed fix and to verified recovery, per runbook |
| `show <id>` | Cause, fix, outcome and timeline of an incident |
| `approve <id>` / `deny <id>` | The same decisions as the buttons |
| `forget <runbook\|all>` | Delete a runbook, or every runbook and the incident history |

## Settings

Under `plugins.entries.night-shift.settings`:

| Key | Default | Meaning |
|---|---|---|
| `slack_channel` | | Channel id for incident cards |
| `approvers` | `[]` | Who may approve, e.g. `["slack:U012ABC"]`. Buttons refuse everyone until this is set |
| `prometheus_url` | `http://localhost:9090` | For metric queries and recovery checks |
| `deploy_history_command` | | Read-only command that prints recent deploys. The best evidence you can give it |
| `db_check_command` | `pg_stat_activity` in a container named `postgres` | Read-only database check |
| `docker_host_prefix` | | Prefix for commands, e.g. `ssh ops@prod-1` |
| `guard_mode` | `incident` | `incident`, `always` or `off` |
| `runbook_category` | `night-shift` | Skill category for learned runbooks |
| `verify_timeout_seconds` | `90` | How long to wait for recovery after a fix |
| `approval_ttl_minutes` | `60` | A proposal older than this cannot be approved |
| `dedupe_minutes` | `10` | Further alerts for a service with a fix in progress join that incident |
| `echo_minutes` | `3` | After a fix, a new alert on that service is checked as a possible echo |

## Safety model

The model only ever reads and proposes. It never changes anything.

- **Guard.** Terminal commands must match a read-only allowlist. Anything else goes to Hermes's
  approval gate, which refuses it in alert runs because nobody is there to answer. All other tools
  that could write are blocked. The allowlist understands quoting, chaining, substitution and
  redirects, and refuses secrets such as `.env` files and a bare `docker inspect`.
- **Approval.** Commands run only after a listed person approves. The click is checked against
  `approvers`, can only run once, and expires. The card shows every command in full, and Night
  Shift refuses a proposal too long to read.
- **No destructive fixes.** `rm -rf`, `DROP TABLE`, volume deletion, force-pushes and the like are
  refused at proposal time, so they never reach an Approve button. Those are a human's job.
- **Runbooks.** Commands in a runbook are only ever ones a person approved and a check verified.
  Nothing the model says after approval can change them. Text from alerts is flattened before it
  reaches a skill file, and is escaped before it reaches Slack.
- **Webhooks.** HMAC-signed, with replay protection, handled by Hermes.

### What you should know before installing

- It **runs shell commands** on the machine or terminal backend Hermes uses: read-only diagnostics
  during an incident, and after approval the commands shown on the card. Once you approve a fix,
  the plugin runs it through Hermes's terminal tool. The click is the approval, so Hermes will not
  ask a second time.
- It **reads** container logs, status and labels, disk usage, process lists and database activity,
  and sends them to your model provider as part of the investigation.
- It **calls Prometheus** at the URL you configure, and Slack through your connected Hermes Slack
  app. It makes no other network calls and has no telemetry.
- It **writes** skills under your Hermes home, in the `night-shift` category, and incident history
  to its plugin state.
- It **stores no credentials** and reads none. Slack and model access stay inside Hermes.

## Limits

- Diagnostics assume Docker. `docker_host_prefix`, `deploy_history_command` and `db_check_command`
  cover most other setups, but only Docker has been exercised end to end.
- Recall matches on alert name and service. It confirms the evidence, but a new failure under an
  old alert name relies on the model noticing. The card always shows the cause before you approve.
- Recognising a failure speeds up the model by seconds. Most of the benefit is that the person
  approving sees "this worked 4 times" instead of reading a fresh investigation.

## Licence

MIT
