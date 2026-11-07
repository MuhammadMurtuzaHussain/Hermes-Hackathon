# Submission package

**Deadline: 22:00 Dublin, Wednesday 11 November 2026** (5PM EST). The form closes a day later, but
treat this as the deadline.

Everything here is ready except what only you can do. Order matters, because the form asks for the
PR and the X post.

1. **Make the repo public, and merge the work to `main`.** Nothing has been made public.
2. **Pin the commit.** Put its full SHA in [`catalog-entry.yaml`](catalog-entry.yaml), in place of
   `<SHA>`, in both places.
3. **Open the catalog PR**, with [`catalog-pr.md`](catalog-pr.md) as the description.
4. **Record the video**, following [`demo-script.md`](demo-script.md). For real Slack, follow
   [`slack-setup.md`](slack-setup.md) first. The mock at <http://localhost:8770> also works.
5. **Post on X**, with the text in [`x-post.md`](x-post.md).
6. **Submit the form**, with [`form-answers.md`](form-answers.md).

## Before pinning

```bash
hermes plugins validate night-shift --install-deps
python3 -m unittest discover -s tests
cd demo && ./sandbox.sh setup && ./sandbox.sh gateway && ./e2e.py --runs 3 --fresh && ./e2e.py --edge-cases
```
