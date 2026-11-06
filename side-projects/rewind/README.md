# Rewind (parked until after the hackathon)

Undo for AI agents: every action Hermes takes is snapshotted, and a timeline lets you scrub back
and reverse it.

## Scope
- Snapshot files before `write_file`, patch and terminal commands that touch the filesystem
  (`pre_tool_call`), store diffs per session.
- Record reversible actions for supported tools (drafted emails, created issues) with their inverse.
- A Hermes Desktop timeline panel (see the Desktop plugin SDK) plus a `/rewind <step>` command.

## Open questions
- What can be safely reversed outside the filesystem? Start with files only.
- Storage limits: `ctx.state` is capped at 10 MiB, so snapshots need the plugin data directory.
