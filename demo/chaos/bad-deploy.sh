#!/usr/bin/env bash
# Scenario 1: ship the leaky release. Postgres runs out of connections in about 15 seconds.
# What Night Shift should work out: roll back to the previous release.
set -euo pipefail
cd "$(dirname "$0")/.."
./deploy.sh v2-leaky
echo "Watch: http://localhost:9090/alerts"
