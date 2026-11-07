#!/usr/bin/env bash
# Deploy a release of the orders API and record it. Night Shift reads this log as deploy history.
# Usage: ./deploy.sh <release>   e.g. ./deploy.sh v2-leaky, or ./deploy.sh v1 to roll back
set -euo pipefail
cd "$(dirname "$0")"
RELEASE_NAME="${1:?usage: deploy.sh <release>}"
PREVIOUS=$(docker inspect --format '{{index .Config.Labels "nightshift.release"}}' api 2>/dev/null || echo none)
RELEASE="$RELEASE_NAME" docker compose up -d --no-deps --force-recreate api 2>&1 | tail -1
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) api: deployed release=$RELEASE_NAME previous=$PREVIOUS (roll back with: ./deploy.sh $PREVIOUS)" >> deploys.log
echo "Deployed $RELEASE_NAME (was $PREVIOUS)."
