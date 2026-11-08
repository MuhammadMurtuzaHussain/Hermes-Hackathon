# Submission package

**Deadline: 22:00 Dublin, Wednesday 11 November 2026** (5PM EST). The form closes a day later, but
treat this as the deadline.

Everything here is ready except what only you can do. Order matters, because the form asks for the
PR and the X post.

1. ~~Make the repo public, and merge the work to `main`.~~ Done 8 Nov.
2. ~~Pin the commit.~~ `7cd0473493bb21f756ae8c4465b18476a612c7a0`.
3. ~~Open the catalog PR.~~ [NousResearch/hermes-agent#151024](https://github.com/NousResearch/hermes-agent/pull/151024).
4. ~~Make the video.~~ [`docs/video/night-shift-demo.mp4`](../video/night-shift-demo.mp4), 87 seconds,
   drawn from a real run. A recording of your own, following [`demo-script.md`](demo-script.md),
   would be a stronger alternative if there is time.
5. **Post on X**, with the text in [`x-post.md`](x-post.md).
6. **Submit the form**, with [`form-answers.md`](form-answers.md).

## Before pinning

```bash
hermes plugins validate night-shift --install-deps
python3 -m unittest discover -s tests
cd demo && ./sandbox.sh setup && ./sandbox.sh gateway && ./e2e.py --runs 3 --fresh && ./e2e.py --edge-cases
```
