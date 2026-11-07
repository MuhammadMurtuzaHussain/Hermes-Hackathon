"""A local stand-in for Slack, so the approval loop can be tested and rehearsed without a workspace.

It speaks enough of the real protocols for Hermes's real Slack adapter to connect unchanged:
the Web API (auth.test, chat.postMessage, chat.update, chat.postEphemeral) and Socket Mode
(hello, then "interactive" envelopes that must be acknowledged). It rejects Block Kit that real
Slack would reject, so a card that renders here will not bounce there.

Open http://localhost:8770 to see the channel and click the buttons. Clicking sends the same
block_actions payload Slack sends. demo/e2e.py drives the same endpoints.
"""

import asyncio
import json
import os
import time
import uuid

from aiohttp import WSMsgType, web

HOST_FOR_CLIENTS = os.environ.get("MOCK_SLACK_WS_HOST", "127.0.0.1:8770")
TEAM, BOT, APP = "T0NIGHT", "U0BOT", "A0NIGHT"
USERS = {"U0ONCALL": "on-call engineer", "U0STRANGER": "someone else in the channel"}

state = {"messages": [], "notes": [], "sockets": set(), "rejected": []}


def block_problem(blocks):
    """The limits real Slack enforces with invalid_blocks."""
    if not isinstance(blocks, list) or len(blocks) > 50:
        return "too many blocks"
    for block in blocks:
        kind = block.get("type")
        if kind == "header":
            text = block.get("text", {})
            if text.get("type") != "plain_text" or not 1 <= len(text.get("text", "")) <= 150:
                return "header must be plain_text of 1 to 150 characters"
        elif kind == "section":
            if not 1 <= len(block.get("text", {}).get("text", "")) <= 3000:
                return "section text must be 1 to 3000 characters"
        elif kind == "context":
            if not 1 <= len(block.get("elements", [])) <= 10:
                return "context needs 1 to 10 elements"
        elif kind == "actions":
            elements = block.get("elements", [])
            if not 1 <= len(elements) <= 25 or len(block.get("block_id", "")) > 255:
                return "bad actions block"
            for el in elements:
                if el.get("type") != "button" or not 1 <= len(el.get("text", {}).get("text", "")) <= 75:
                    return "button text must be 1 to 75 characters"
                if len(el.get("action_id", "")) > 255 or len(el.get("value", "")) > 2000:
                    return "button action_id or value too long"
                if el.get("style") not in (None, "primary", "danger"):
                    return "bad button style"
        else:
            return f"unsupported block type {kind}"
    return ""


async def payload(request):
    if request.content_type == "application/json":
        return await request.json()
    data = dict(await request.post())
    if isinstance(data.get("blocks"), str):
        data["blocks"] = json.loads(data["blocks"])
    return data


async def api(request):
    method = request.match_info["method"]
    body = await payload(request)
    if method == "auth.test":
        return web.json_response({"ok": True, "url": "http://mock.slack/", "team": "Night Shift Demo", "user": "nightshift",
                                  "team_id": TEAM, "user_id": BOT, "bot_id": "B0BOT"})
    if method == "apps.connections.open":
        return web.json_response({"ok": True, "url": f"ws://{HOST_FOR_CLIENTS}/socket"})
    if method in ("chat.postMessage", "chat.update"):
        if body.get("blocks") and (problem := block_problem(body["blocks"])):
            state["rejected"].append(problem)
            return web.json_response({"ok": False, "error": "invalid_blocks", "response_metadata": {"messages": [problem]}})
        if method == "chat.update":
            for msg in state["messages"]:
                if msg["ts"] == body.get("ts"):
                    msg.update(text=body.get("text", ""), blocks=body.get("blocks", []), edited=time.time())
                    return web.json_response({"ok": True, "channel": msg["channel"], "ts": msg["ts"]})
            return web.json_response({"ok": False, "error": "message_not_found"})
        ts = f"{time.time():.6f}"
        msg = {"ts": ts, "channel": body.get("channel", ""), "text": body.get("text", ""), "blocks": body.get("blocks", []),
               "posted": time.time()}
        state["messages"].append(msg)
        return web.json_response({"ok": True, "channel": msg["channel"], "ts": ts, "message": msg})
    if method == "chat.postEphemeral":
        state["notes"].append({"t": time.time(), "user": body.get("user", ""), "text": body.get("text", "")})
        return web.json_response({"ok": True, "message_ts": f"{time.time():.6f}"})
    return web.json_response({"ok": True})


async def socket(request):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    state["sockets"].add(ws)
    await ws.send_json({"type": "hello", "num_connections": len(state["sockets"]),
                        "debug_info": {"host": "mock"}, "connection_info": {"app_id": APP}})
    try:
        async for frame in ws:
            if frame.type == WSMsgType.ERROR:
                break
    finally:
        state["sockets"].discard(ws)
    return ws


async def click(request):
    """What Slack does when a person taps a button."""
    body = await request.json()
    msg = next((m for m in state["messages"] if m["ts"] == body.get("ts")), None)
    if not msg:
        return web.json_response({"ok": False, "error": "no such message"}, status=404)
    action = next((e for b in msg["blocks"] if b.get("type") == "actions" for e in b["elements"]
                   if e["action_id"] == body.get("action_id")), None)
    if not action:
        return web.json_response({"ok": False, "error": "button is gone"}, status=409)
    user = body.get("user", "U0ONCALL")
    block = next(b for b in msg["blocks"] if b.get("type") == "actions")
    interaction = {
        "type": "block_actions", "team": {"id": TEAM, "domain": "mock"},
        "user": {"id": user, "username": user.lower(), "team_id": TEAM},
        "api_app_id": APP, "token": "mock", "trigger_id": uuid.uuid4().hex,
        "container": {"type": "message", "message_ts": msg["ts"], "channel_id": msg["channel"], "is_ephemeral": False},
        "channel": {"id": msg["channel"], "name": "on-call"},
        "message": {"type": "message", "ts": msg["ts"], "text": msg["text"], "blocks": msg["blocks"]},
        "response_url": f"http://{HOST_FOR_CLIENTS}/response",
        "actions": [{"type": "button", "action_id": action["action_id"], "block_id": block.get("block_id", ""),
                     "value": action.get("value", ""), "action_ts": f"{time.time():.6f}"}],
    }
    envelope = {"envelope_id": str(uuid.uuid4()), "type": "interactive", "accepts_response_payload": False,
                "payload": interaction}
    if not state["sockets"]:
        return web.json_response({"ok": False, "error": "gateway is not connected"}, status=503)
    await asyncio.gather(*(ws.send_json(envelope) for ws in list(state["sockets"])[:1]))
    return web.json_response({"ok": True})


async def ui_state(_):
    return web.json_response({"messages": state["messages"], "notes": state["notes"][-5:],
                              "connected": len(state["sockets"]), "rejected": state["rejected"]})


async def clear(_):
    state["messages"].clear()
    state["notes"].clear()
    state["rejected"].clear()
    return web.json_response({"ok": True})


async def index(_):
    return web.FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


app = web.Application()
app.add_routes([web.post("/api/{method}", api), web.get("/socket", socket), web.post("/ui/click", click),
                web.get("/ui/state", ui_state), web.post("/ui/clear", clear), web.get("/", index)])

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8770, print=None)
