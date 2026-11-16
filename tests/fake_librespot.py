"""Attrappe von go-librespot (HTTP + Websocket /events) für Tests.
POST /debug/emit {"type": "...", "data": {...}} schickt ein Ereignis an alle Zuhörer."""
import asyncio, json, sys
from aiohttp import web

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3678
clients, calls = set(), []
state = {"active": False, "paused": True, "volume": 50}
TRACK = {"name": "Lummerland", "artist_names": ["Jim Knopf"], "album_name": "Kinderlieder", "album_cover_url": "", "duration": 185000, "position": 12000}

async def status(r):
    if not state["active"]: return web.Response(status=204)
    return web.json_response({"stopped": False, "paused": state["paused"], "track": TRACK, "volume": state["volume"], "volume_steps": 100})

async def events(r):
    ws = web.WebSocketResponse(); await ws.prepare(r); clients.add(ws)
    try:
        async for _ in ws: pass
    finally:
        clients.discard(ws)
    return ws

async def player(r):
    name = r.match_info["cmd"]
    try: body = await r.json()
    except Exception: body = {}
    calls.append([name, body])
    if name == "volume":
        if r.method == "GET": return web.json_response({"value": state["volume"], "max": 100})
        state["volume"] = body["volume"]
    if name in ("pause", "playpause", "resume"):
        state["paused"] = (name == "pause") or (name == "playpause" and not state["paused"])
        await emit({"type": "paused" if state["paused"] else "playing", "data": {}})
    return web.Response()

async def emit(ev):
    if ev["type"] in ("playing", "active", "metadata"): state["active"] = True
    if ev["type"] == "playing": state["paused"] = False
    if ev["type"] == "inactive": state["active"] = False
    if ev["type"] == "metadata": ev["data"] = TRACK
    for ws in list(clients): await ws.send_str(json.dumps(ev))

async def debug_emit(r):
    await emit(await r.json()); return web.Response()

async def debug_calls(r):
    out = list(calls); calls.clear(); return web.json_response(out)

app = web.Application()
app.add_routes([web.get("/status", status), web.get("/events", events), web.route("*", "/player/{cmd}", player),
                web.post("/debug/emit", debug_emit), web.get("/debug/calls", debug_calls)])
web.run_app(app, host="127.0.0.1", port=PORT, print=None)
