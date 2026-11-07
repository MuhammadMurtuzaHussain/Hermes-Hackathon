# Catalog PR

Title: `plugin-catalog: add night-shift`

Fork `NousResearch/hermes-agent`, add one file, `plugin-catalog/night-shift.yaml`, from
[`catalog-entry.yaml`](catalog-entry.yaml) with `<SHA>` replaced by the commit you pin, and open the
PR with the description below.

---

## What it does

Night Shift is an on-call engineer. A signed monitoring webhook starts an agent run that investigates
read-only and proposes a fix. A listed human approves it with a Slack button or `/nightshift approve`.
The plugin then runs exactly those commands, polls a recovery check, and writes the fix as a skill
through `skill_manage`, so the next occurrence is recognised.

## Hermes surfaces used

`register_tool` (six tools), `register_hook` (`pre_llm_call`, `pre_tool_call`), `register_command`,
`register_cli_command`, `register_skill`, `register_platform_handler("slack")`,
`register_slack_action_handler`, `ctx.state`, `ctx.dispatch_tool` (`terminal`, `skill_manage`) and
webhook routes through user config. No core overrides and no private imports.

## Disclosure (rule 13)

- **Shell commands.** Runs read-only diagnostics through the `terminal` tool during an incident.
  After a listed human approves, it runs the approved commands through the same tool, from plugin
  code on a worker thread. The click is the approval, so Hermes's gate does not prompt again. This
  is the one thing a reviewer should look at, in `tools.py` (`run_fix`) and `__init__.py`
  (`_slack_handler`). Destructive commands are refused at proposal time (`policy.is_destructive`),
  so they cannot reach an Approve button.
- **Rule 12.** The guard escalates through Hermes's own gate with `{"action": "approve"}`, and
  `approvals.unattended_mode` then denies it in webhook runs. Nothing is auto-approved. Approval
  waits are asynchronous state, so no agent run hangs waiting for a person, and proposals expire.
- **Reads.** Container logs, status and labels, disk usage, and database activity. These go to the
  model provider. Secret paths and bare `docker inspect` are refused by the guard.
- **Network.** Prometheus at the configured URL, and Slack through the user's Hermes Slack app.
  No other calls and no telemetry.
- **Writes.** Skills in the `night-shift` category, and plugin state.
- **Credentials.** Stores none and reads none. There is no `requires_env`.
- **Background processes.** None. If Hermes refuses a fix as a foreground server (`docker compose
  up`), it is re-sent with `background=true` as the tool advises, and the recovery check confirms it.

## Validation

`hermes plugins validate night-shift --install-deps` passes at this SHA on Hermes 0.22.5.
Screenshots of the Slack card are in the entry.
