#!/usr/bin/env bash
# Scenario 2: the API process dies and stays down.
set -euo pipefail
docker update --restart=no api >/dev/null
docker kill api >/dev/null
echo "API killed. ApiDown fires in about 15 seconds."
