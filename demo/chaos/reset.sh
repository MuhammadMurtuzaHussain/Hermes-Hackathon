#!/usr/bin/env bash
# Put the stack back to healthy between demo takes. Learned runbooks are kept on purpose:
# use `hermes nightshift forget all` to start the learning story again.
set -euo pipefail
cd "$(dirname "$0")/.."
./deploy.sh v1
docker update --restart=unless-stopped api >/dev/null
