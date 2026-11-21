"""Minimaler MPD-Ersatz für Tests (spricht genug vom MPD-Protokoll)."""
import asyncio, os, shlex, sys

ROOT = sys.argv[1]; PORT = int(sys.argv[2])
S = {"state": "stop", "volume": 50, "song": 0, "elapsed": 0.0, "random": 0, "queue": [], "outputs": {0: 1, 1: 0, 2: 0}}
AUDIO = (".mp3", ".ogg", ".flac", ".m4a", ".wav")

def files(uri):
    base = os.path.join(ROOT, uri)
    if "://" in uri: return [uri]
    if os.path.isfile(base): return [uri]
    out = []
    for dp, dn, fn in os.walk(base):
        dn.sort()
        for f in sorted(fn):
            if f.lower().endswith(AUDIO):
                out.append(os.path.relpath(os.path.join(dp, f), ROOT))
    return out

def handle(cmd, a):
    q = S["queue"]
    if cmd == "status":
        r = [f"volume: {S['volume']}", f"state: {S['state']}", f"random: {S['random']}", f"playlistlength: {len(q)}"]
        if S["state"] != "stop" and q:
            r += [f"song: {S['song']}", f"elapsed: {S['elapsed']:.3f}", "duration: 180.000"]
        return r
    if cmd == "currentsong":
        if S["state"] == "stop" or not q: return []
        return [f"file: {q[S['song']]}", f"Pos: {S['song']}"]
    if cmd == "playlistinfo":
        r = []
        for i, f in enumerate(q): r += [f"file: {f}", f"Pos: {i}", "duration: 180.000"]
        return r
    if cmd == "outputs":
        r = []
        for i, n in ((0, "Lautsprecher"), (1, "Hausstream"), (2, "Kopfhörer")):
            r += [f"outputid: {i}", f"outputname: {n}", f"outputenabled: {S['outputs'][i]}"]
        return r
    if cmd == "clear": q.clear(); S["state"] = "stop"; S["song"] = 0
    elif cmd == "add":
        fs = files(a[0])
        if not fs or not os.path.exists(os.path.join(ROOT, a[0])) and "://" not in a[0]: return "ACK [50@0] {add} No such directory"
        q.extend(fs)
    elif cmd == "play":
        if not q: return "ACK [2@0] {play} Bad song index"
        if a: S["song"] = int(a[0])
        S["state"] = "play"; S["elapsed"] = 0.0
    elif cmd == "pause": S["state"] = "pause" if a[0] == "1" else "play"
    elif cmd == "stop": S["state"] = "stop"
    elif cmd == "next":
        if S["state"] == "stop": return "ACK [55@0] {next} Not playing"
        if S["song"] + 1 < len(q): S["song"] += 1; S["elapsed"] = 0.0
        else: S["state"] = "stop"
    elif cmd == "previous":
        if S["state"] == "stop": return "ACK [55@0] {previous} Not playing"
        S["song"] = max(0, S["song"] - 1); S["elapsed"] = 0.0
    elif cmd == "setvol": S["volume"] = int(a[0])
    elif cmd == "seekcur": S["elapsed"] = float(a[0])
    elif cmd == "random": S["random"] = int(a[0])
    elif cmd == "enableoutput": S["outputs"][int(a[0])] = 1
    elif cmd == "disableoutput": S["outputs"][int(a[0])] = 0
    elif cmd == "update": return ["updating_db: 1"]
    return []

async def client(r, w):
    w.write(b"OK MPD 0.23.5\n")
    batch = None
    while True:
        line = await r.readline()
        if not line: break
        parts = shlex.split(line.decode())
        if not parts: continue
        if parts[0] in ("command_list_ok_begin", "command_list_begin"):
            batch = []; continue
        if parts[0] == "command_list_end":
            out = ""
            for p in batch:
                res = handle(p[0], p[1:])
                if isinstance(res, str): out += res + "\n"; break
                out += "".join(x + "\n" for x in res) + "list_OK\n"
            else:
                out += "OK\n"
            batch = None; w.write(out.encode()); await w.drain(); continue
        if batch is not None:
            batch.append(parts); continue
        res = handle(parts[0], parts[1:])
        if isinstance(res, str): w.write((res + "\n").encode())
        else: w.write(("".join(x + "\n" for x in res) + "OK\n").encode())
        await w.drain()

async def tick():
    while True:
        await asyncio.sleep(0.5)
        if S["state"] == "play": S["elapsed"] += 0.5

async def main():
    srv = await asyncio.start_server(client, "127.0.0.1", PORT)
    asyncio.create_task(tick())
    await srv.serve_forever()

asyncio.run(main())
