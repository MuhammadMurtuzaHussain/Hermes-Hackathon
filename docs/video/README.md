# The demo video

`night-shift-demo.mp4` (87 seconds) is drawn from a real run, not mocked up. `record.py` drove two
incidents through the sandbox (real gateway, real model, real stack, the local Slack mock) and saved
what happened every half second to `timeline.js`: connection counts, alert states, the Slack cards
exactly as Hermes posted them, terminal output and the runbook it wrote. `player.html` draws that
data at any moment, and `capture.js` renders it frame by frame with headless Chrome.

Clips faster than real time say so on screen. The guard scene shows log lines from the separate
red-team test described in `docs/FINDINGS.md`, and is labelled as such.

To re-render: `npm i puppeteer-core@23.10.0 && node capture.js`, then
`ffmpeg -framerate 30 -i frames/%05d.jpg -c:v libx264 -crf 18 -pix_fmt yuv420p night-shift-demo.mp4`.
