"""Demo 'orders' API used to stage incidents for Night Shift.

RELEASE=v1          healthy: uses a small connection pool.
RELEASE=v2-leaky    the bad deploy: opens a DB connection per request and never closes it,
                    so Postgres runs out of connections within a minute or two under load.
"""

import os
import time

import psycopg2
from flask import Flask, jsonify
from prometheus_client import Counter, Gauge, generate_latest
from psycopg2.pool import ThreadedConnectionPool

RELEASE = os.environ.get("RELEASE", "v1")
DSN = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/postgres")

app = Flask(__name__)
REQUESTS = Counter("http_requests_total", "Requests", ["status"])
INFO = Gauge("app_release_info", "Running release", ["release"])
INFO.labels(release=RELEASE).set(1)

_pool = None
_leaked = []  # the bug: connections kept forever


def _query():
    global _pool
    if RELEASE == "v2-leaky":
        conn = psycopg2.connect(DSN)
        _leaked.append(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return
    if _pool is None:
        _pool = ThreadedConnectionPool(1, 5, DSN)
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    finally:
        _pool.putconn(conn)


@app.get("/orders")
def orders():
    try:
        _query()
        REQUESTS.labels(status="200").inc()
        return jsonify(release=RELEASE, ok=True, ts=time.time())
    except Exception as exc:
        REQUESTS.labels(status="500").inc()
        app.logger.error("database error: %s", exc)
        return jsonify(error=str(exc)), 500


@app.get("/health")
def health():
    return jsonify(status="ok", release=RELEASE)


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": "text/plain; version=0.0.4"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
