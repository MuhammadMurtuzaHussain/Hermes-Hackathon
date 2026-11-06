"""Alertmanager -> Hermes signing relay.

Alertmanager cannot HMAC-sign webhooks, and Hermes rejects unsigned ones (good). This
relay receives Alertmanager's POST on the private Docker network, signs it with Hermes's
generic V2 scheme (HMAC-SHA256 of "<timestamp>.<body>", replay-protected) and forwards it.

Stdlib only. Env: NIGHTSHIFT_WEBHOOK_SECRET, HERMES_WEBHOOK_URL, PORT (default 9095).
"""

import hashlib
import hmac
import os
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ["NIGHTSHIFT_WEBHOOK_SECRET"].encode()
TARGET = os.environ.get("HERMES_WEBHOOK_URL", "http://host.docker.internal:8644/webhooks/nightshift")
MAX_BODY = 1_000_000


class Relay(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self.send_error(413 if length > MAX_BODY else 400)
            return
        body = self.rfile.read(length)
        ts = str(int(time.time()))
        sig = hmac.new(SECRET, ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(TARGET, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": ts,
            "X-Webhook-Signature-V2": sig,
            "X-Request-ID": str(uuid.uuid4()),
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
        except Exception as exc:
            print(f"forward failed: {exc}", flush=True)
            status = 502
        print(f"relayed alert ({len(body)} bytes) -> {status}", flush=True)
        self.send_response(200 if status < 300 else 502)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "9095"))), Relay).serve_forever()
