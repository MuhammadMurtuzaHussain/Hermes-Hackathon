#!/usr/bin/env bash
# Put everything back to healthy between demo takes.
set -euo pipefail
cd "$(dirname "$0")/.."
RELEASE=v1 docker compose up -d --no-deps --force-recreate api
docker update --restart=unless-stopped api >/dev/null
echo "Reset to v1."
