#!/usr/bin/env bash
# Send a signed test alert straight to Hermes, no Docker stack needed.
# Usage: NIGHTSHIFT_WEBHOOK_SECRET=... ./fake-alert.sh [alertname] [summary]
set -euo pipefail
ALERT="${1:-DBConnectionsExhausted}"
SUMMARY="${2:-Test alert from fake-alert.sh}"
URL="${HERMES_WEBHOOK_URL:-http://localhost:8644/webhooks/nightshift}"
BODY=$(python3 -c 'import json,sys; a,s=sys.argv[1:3]; print(json.dumps({"status":"firing","groupKey":"demo/"+a,"commonLabels":{"alertname":a,"service":"api","severity":"critical"},"commonAnnotations":{"summary":s}}))' "$ALERT" "$SUMMARY")
TS=$(date +%s)
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$NIGHTSHIFT_WEBHOOK_SECRET" -hex | awk '{print $NF}')
curl -sS -X POST "$URL" -H "Content-Type: application/json" \
  -H "X-Webhook-Timestamp: $TS" -H "X-Webhook-Signature-V2: $SIG" -d "$BODY"
echo
