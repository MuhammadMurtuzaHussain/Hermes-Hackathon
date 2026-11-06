#!/usr/bin/env bash
# Scenario 2: the API process dies and stays down.
set -euo pipefail
docker update --restart=no api >/dev/null
docker kill api
echo "API killed. Alert ApiDown fires in ~20s."
