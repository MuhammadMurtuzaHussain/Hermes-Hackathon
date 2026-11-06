#!/usr/bin/env bash
# Send a signed test alert straight to Hermes, no Docker stack needed.
# Usage: NIGHTSHIFT_WEBHOOK_SECRET=... ./fake-alert.sh [alertname]
set -euo pipefail
ALERT="${1:-DBConnectionsExhausted}"
URL="${HERMES_WEBHOOK_URL:-http://localhost:8644/webhooks/nightshift}"
BODY=$(printf '{"status":"firing","groupKey":"demo/%s","commonLabels":{"alertname":"%s","service":"api","severity":"critical"},"commonAnnotations":{"summary":"Test alert from fake-alert.sh"}}' "$ALERT" "$ALERT")
TS=$(date +%s)
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$NIGHTSHIFT_WEBHOOK_SECRET" -hex | awk '{print $NF}')
curl -sS -X POST "$URL" -H "Content-Type: application/json" \
  -H "X-Webhook-Timestamp: $TS" -H "X-Webhook-Signature-V2: $SIG" -d "$BODY"
echo
