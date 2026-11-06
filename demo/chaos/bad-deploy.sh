#!/usr/bin/env bash
# Scenario 1: ship the leaky release. Postgres runs out of connections in ~1-2 minutes.
# Expected fix (what Night Shift should learn): roll back to v1.
set -euo pipefail
cd "$(dirname "$0")/.."
RELEASE=v2-leaky docker compose up -d --no-deps api
echo "Deployed v2-leaky. Watch: http://localhost:9090/alerts"
