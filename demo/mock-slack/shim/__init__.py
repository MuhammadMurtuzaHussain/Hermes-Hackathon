"""Test shim: run Hermes's real Slack adapter against demo/mock-slack.

Only loaded by demo/sandbox.sh in its private HERMES_HOME, and only because slack_sdk has no
setting for its API host. It adds a private copy of the Slack libraries to sys.path, so the
Hermes environment is untouched, and redirects the SDK's base URL. Everything after that is the
real adapter, real slack_bolt and real Socket Mode. Night Shift itself never does this.
"""

import os
import sys

BASE_URL = os.environ.get("MOCK_SLACK_URL", "http://127.0.0.1:8770/api/")


def register(ctx):
    deps = os.environ.get("MOCK_SLACK_DEPS", "")
    if deps and deps not in sys.path:
        sys.path.insert(0, deps)
    import slack_sdk.web.async_base_client as base

    original = base.AsyncBaseClient.__init__

    def redirected(self, *args, **kwargs):
        kwargs["base_url"] = BASE_URL
        original(self, *args, **kwargs)

    base.AsyncBaseClient.__init__ = redirected
