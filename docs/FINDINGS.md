# What the live runs found

The scaffold's six tests passed against a stand-in for Hermes. These are the things only a real
gateway, a real stack and repeated runs could show. Each one is fixed and has a test.

## The design changed

| Found | Consequence | Now |
|---|---|---|
| Once the agent proposed a fix, its run ended. Nothing was left to verify or close the incident | The learning loop could never finish | After approval, plain code runs the fix, polls recovery and writes the runbook. No model after the human decides |
| `dispatch_tool` does not fire `pre_tool_call` | The scaffold's approved-commands list did nothing | Removed. The guard covers the model; approved fixes run from plugin code |
| The guard only turned on after `diagnose` | A hijacked run could act before diagnosing | A public `pre_llm_call` hook turns it on before the first tool call |
| Everything except `terminal` passed the guard | `write_file` or `execute_code` could change state | Default deny: only known-safe tools pass |
| Hermes auto-approves dangerous commands in a worker thread with no session | A tapped `rm -rf` would have run | Destructive commands are refused at proposal, so they never reach a button |

## Real bugs

| Found by | Bug | Fix |
|---|---|---|
| Repeat runs | An echo alert was escalated, and dedupe then swallowed the next real outage | Escalated incidents only absorb their own alert. Alerts just after a fix are checked as echoes |
| First live run | One failure raised two alerts, so two incidents | Alertmanager inhibition, plus dedupe in `diagnose` |
| First run through to the end | Runbook skill silently not written: Hermes refuses descriptions over 60 characters | Short description, and the real error is logged |
| Scenario 2 | `docker compose up` refused by the terminal tool as a foreground server, so the fix failed | Re-sent in the background as the tool advises; the recovery check confirms it |
| Scenario 2 | Agent escalated a dead service, wanting certainty before a restart | Protocol: a down service gets a proposal, because a human reviews it anyway |
| Scenario 2 | The guard refused bare `docker inspect` and the agent gave up on the exit reason | `diagnose` includes exit code and OOM flag; the refusal says how to ask |
| Real deploy | Agent saw a redeploy but not what changed, and proposed killing sessions | Deploy history and release labels as evidence: it now proposes the rollback |
| Fake alert | A "fix" made only of read-only commands | Refused, and `nightshift_escalate` exists for "no fix is justified" |
| Stack | API and exporter shared a superuser, so a leak would blind monitoring | API uses a normal role. Postgres keeps reserved slots for monitoring |
| Stack | Host port 8080 was taken | Loopback-only ports, API on 18090 |
| Catalog check | Security scan failed on attack strings in tests and a sensitive path in the guard | Tests moved out of the plugin folder; the path check no longer needs the literal |
| Catalog check | `get_session_env` flagged as a private core import | Replaced with the public `pre_llm_call` hook |
| Own review | Guard bypasses: newlines, `&`, `env rm`, `awk system()`, `curl -F`, multi-statement SQL | Quote-aware parser and per-program rules, with an adversarial test list |
| Own review | Commands in a runbook came from whatever the model passed at resolve | Runbooks use only the approved proposal |

## Measured

From the latest repeat run. "Card" is the model's time from starting the investigation to the
proposal. Deploy to card adds the monitoring path: saturation, Prometheus `for: 5s`, Alertmanager
and coalescing, about 25 seconds.

RESULTS_PLACEHOLDER

The honest reading: recognising a failure saves seconds, because Haiku is already quick when the
evidence is good. The plan's "3 minutes, then 20 seconds" would not be true. The value of recall is
that the approver sees a track record, and that the commands cannot drift from what was approved.

## What has not been verified

- **Real Slack.** Everything ran against the mock, through Hermes's real adapter, real `slack_bolt`
  and real Socket Mode framing. The mock rejects Block Kit that Slack rejects, but no card has been
  seen in a real workspace.
- **Other backends.** Only local Docker. SSH via `docker_host_prefix` and Kubernetes are untested.
- **Other models.** All runs used `claude-haiku-5-5`.
- **A catalog install.** The repo is private, so `hermes plugins install night-shift` cannot be
  tested yet. `hermes plugins validate --install-deps` passes.
