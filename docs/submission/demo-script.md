# Demo video: 85 seconds

Deadline: **22:00 Dublin, Wednesday 11 November**.

The rule for the edit: nothing is faked, and anything sped up says so on screen. Real times from
the test runs are in `docs/FINDINGS.md`. The honest headline is alert to approval card in about 30
seconds, and recovery seconds after the tap. Do not claim the second run is dramatically faster: it
is seconds faster. What changes is that the card says it has worked before.

## Before you record

```bash
cd demo
./sandbox.sh setup && ./sandbox.sh gateway
./sandbox.sh hermes nightshift forget all
./chaos/reset.sh
```

Layout: terminal on the left, Slack (or <http://localhost:8770>) on the right, Prometheus
(<http://localhost:9090/graph?g0.expr=sum(pg_stat_activity_count)&g0.tab=0&g0.range_input=5m>) in a
tab. Use a big terminal font. Do one full dry run first, because a real take needs the alert to
have cleared: wait until <http://localhost:9090/alerts> is green before each break.

## Shot list

| Time | Show | Say |
|---|---|---|
| 0:00 to 0:08 | Black screen, then a phone lock screen at 03:02 | "It's 3am. Your database just ran out of connections. Who do you want to wake up?" |
| 0:08 to 0:16 | Terminal: `./chaos/bad-deploy.sh` | "A bad release just shipped. Every request leaks a database connection." |
| 0:16 to 0:24 | Prometheus connections climbing to the 40 limit. Sped up, labelled "sped up" | "Prometheus sees it. The alert goes to Hermes. Nobody is at a keyboard." |
| 0:24 to 0:40 | The card arrives. Hold on Cause and Fix, zoom in. Tap **Approve fix**, confirm | "Night Shift investigated, read-only. It found the deploy, and it's asking one question. Roll back? I read the exact command and tap." |
| 0:40 to 0:48 | The card flips to **Resolved** | "It ran what I approved, and it proved the service recovered. Not 'done'. Verified." |
| 0:48 to 0:58 | Terminal: `cat` the runbook skill, scroll to Procedure | "Then it wrote the runbook. A real Hermes skill. Only commands a human approved and a check verified." |
| 0:58 to 1:12 | `./chaos/bad-deploy.sh` again. Sped-up wait. Card with **Recognised** and "Worked 1x before". Tap. Resolved | "Same failure tomorrow night. It recognises it, shows the track record, and asks. One tap." |
| 1:12 to 1:20 | Split: a stranger's click refused, then `./e2e.py --only poison` passing, or the guard log | "A stranger can't approve. A poisoned alert can't talk it into anything. The guard is code, not a prompt." |
| 1:20 to 1:25 | Title: Night Shift. Repo URL. Tag line | "Read-only by default. A human approves every change. Better every night." |

## Fallback if a take goes wrong

- No card after a minute: run `./sandbox.sh hermes nightshift status`. An old incident waiting for
  approval absorbs new alerts by design, so clear it with `forget all`.
- Card arrives but the fix looks wrong: that is a real model decision. Press Deny, run
  `./chaos/reset.sh`, and take it again.
