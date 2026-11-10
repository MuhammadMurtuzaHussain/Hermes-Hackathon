# Submission package

**Deadline: 22:00 Dublin, Wednesday 11 November 2026** (5PM EST). The form closes a day later, but
treat this as the deadline.

Everything here is ready except what only you can do. Order matters, because the form asks for the
PR and the X post.

1. ~~Make the repo public, and merge the work to `main`.~~ Done 8 Nov.
2. ~~Pin the commit.~~ `857ff062407f620f16edd2824bbe61570c2a4cfb`, re-pinned after the maintainer review (first pin `7cd04734`).
3. ~~Open the catalog PR.~~ [NousResearch/hermes-agent#151024](https://github.com/NousResearch/hermes-agent/pull/151024).
4. ~~Make the video.~~ [`docs/video/night-shift-demo.mp4`](../video/night-shift-demo.mp4), 87 seconds,
   drawn from a real run. A recording of your own, following [`demo-script.md`](demo-script.md),
   would be a stronger alternative if there is time.
5. ~~Post on X.~~ [x.com/hussainreflects/status/2119263040092930144](https://x.com/hussainreflects/status/2119263040092930144), 8 Nov.
6. ~~Submit the form.~~ Submitted 8 Nov; Typeform confirmed "Submission Received".

What is left: watch the catalog PR for CI and reviewer comments. A fork's PR may need a maintainer to
approve its workflow run before CI starts.

## Before pinning

```bash
hermes plugins validate night-shift --install-deps
python3 -m unittest discover -s tests
cd demo && ./sandbox.sh setup && ./sandbox.sh gateway && ./e2e.py --runs 3 --fresh && ./e2e.py --edge-cases
```
