"""Klangkiste – Hauptprogramm: verbindet Player, Karten, Knöpfe, MQTT, Web."""
import argparse
import asyncio
import json
import logging
import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web

from . import __version__, system
from .backup import Backup, BackupError
from .buttons import Buttons
from .config import BUTTON_ACTIONS, BUTTON_PINS, Store
from .importer import import_legacy
from .library import IMAGES, Library, LibraryError
from .mqtt import Mqtt
from .player import Player
from .rfid import CardWatcher, MockReader

log = logging.getLogger("klangkiste")
# Nicht auf die Systemtabelle verlassen: falsche Typen lassen Browser
# Stildatei oder Skript stillschweigend verwerfen.
for _ext, _type in ((".css", "text/css"), (".js", "text/javascript"), (".svg", "image/svg+xml"),
                    (".html", "text/html"), (".webp", "image/webp")):
    mimetypes.add_type(_type, _ext)
WEB = Path(__file__).resolve().parent.parent / "web"


class Box:
    def __init__(self, args):
        self.args = args
        self.mock = args.mock
        self.store = Store(args.data)
        self.library = Library(self.store.music)
        self.player = Player(self.store, args.mpd_host, args.mpd_port, self._on_player)
        self.buttons = Buttons(self.store, self.dispatch_threadsafe)
        self.mqtt = Mqtt(self.store, self._mqtt_cmd, self.full_state, __version__)
        self.backup = None
        self.loop = None
        self.clients = set()
        self.learn = None
        self.active_card = None
        self.last_unknown = None
        self.spotify = False
        self.reader = None
        self.watcher = None
        self.reader_error = ""

    # ---------- Start / Stopp ----------
    async def start(self, app):
        self.loop = asyncio.get_running_loop()
        self.backup = Backup(self.store, on_restored=self._after_restore,
                             on_status=lambda s: self.emit("backup", s))
        self.player.start()
        await asyncio.to_thread(self._init_hardware)
        self.mqtt.start()
        app["scheduler"] = asyncio.create_task(self.backup.scheduler())
        asyncio.create_task(self._startup_volume())

    def _init_hardware(self):
        try:
            self.buttons.apply()
        except Exception as e:
            log.warning("Knöpfe nicht verfügbar: %s", e)
        cfg = self.store.config["rfid"]
        if self.mock:
            self.reader = MockReader()
        elif cfg["enabled"]:
            try:
                from .rfid import MFRC522
                self.reader = MFRC522(cfg["spi_bus"], cfg["spi_device"], cfg["rst_pin"], cfg.get("spi_speed", 1000000))
                v = self.reader.version()
                if v in (0x00, 0xFF):
                    self.reader_error = f"Kartenleser antwortet nicht (Kennung 0x{v:02X}) – Verkabelung/Reset-Pin prüfen"
                    log.warning(self.reader_error)
            except Exception as e:
                self.reader_error = f"Kartenleser nicht verfügbar: {e}"
                log.warning(self.reader_error)
        if self.reader:
            self.watcher = CardWatcher(self.reader, self.on_card, self.on_remove)
            self.watcher.start()

    async def _startup_volume(self):
        for _ in range(60):
            if self.player.state.get("state") != "offline":
                await asyncio.to_thread(self.player.set_volume, self.store.config["audio"]["volume_start"])
                return
            await asyncio.sleep(1)

    async def stop(self, app):
        app["scheduler"].cancel()
        if self.watcher:
            self.watcher.stop()
        self.buttons.close()
        self.mqtt.stop()
        self.player.close()

    def _after_restore(self):
        def go():
            self.buttons.apply()
            self.mqtt.start()
            self.player.update_db(wait=False)
        if self.loop:
            self.loop.run_in_executor(None, go)

    # ---------- Ereignisse an die Oberfläche ----------
    def full_state(self):
        s = dict(self.player.state)
        s.update({"card": self.active_card, "spotify": self.spotify, "name": self.store.config["name"]})
        return s

    def emit(self, event, data):
        if self.loop:
            self.loop.call_soon_threadsafe(self._emit, event, data)

    def _emit(self, event, data):
        for q in list(self.clients):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                pass

    def _on_player(self, state):
        if state.get("state") == "play":
            self.spotify = False
        full = self.full_state()
        self.emit("state", full)
        self.mqtt.publish_state(full)

    def toast(self, message, kind="error"):
        self.emit("toast", {"message": message, "kind": kind})

    # ---------- Befehle ----------
    def dispatch_threadsafe(self, action, value=None):
        """Für Knöpfe und MQTT (laufen in fremden Threads)."""
        if self.loop:
            self.loop.call_soon_threadsafe(
                lambda: self.loop.run_in_executor(None, self._do_safe, action, value))

    def _do_safe(self, action, value=None):
        try:
            self._do(action, value)
        except Exception as e:
            log.warning("Befehl %s: %s", action, e)
            self.toast(str(e))

    def _do(self, action, value=None):
        p = self.player
        if action == "play_pause":
            p.play_pause()
        elif action == "play":
            p.play()
        elif action == "pause":
            p.pause()
        elif action == "next":
            p.next()
        elif action == "prev":
            p.prev()
        elif action == "stop":
            p.stop()
        elif action == "vol_up":
            p.volume_step(+1)
        elif action == "vol_down":
            p.volume_step(-1)
        elif action == "volume":
            p.set_volume(int(float(value)))
        elif action == "seek":
            p.seek(value)
        elif action == "jump":
            p.jump(value)
        elif action == "stream":
            on = value if isinstance(value, bool) else str(value).strip().upper() in ("ON", "1", "TRUE")
            p.set_stream(on)
        elif action == "play_folder":
            self.library.path(value)
            self.active_card = None
            p.play_folder(str(value).strip("/"), restart=False)
        elif action == "play_url":
            self._check_url(value)
            self.active_card = None
            p.play_url(value)
        elif action == "shutdown":
            p.stop()
            if not self.mock:
                asyncio.run_coroutine_threadsafe(system.power("shutdown"), self.loop)
        else:
            raise ValueError(f"Unbekannter Befehl: {action}")

    @staticmethod
    def _check_url(url):
        if urlparse(str(url)).scheme not in ("http", "https"):
            raise ValueError("Bitte eine http(s)-Adresse angeben")

    def _mqtt_cmd(self, cmd, payload):
        self.dispatch_threadsafe(cmd, payload)

    # ---------- Karten ----------
    def on_card(self, main, alts):
        cards = self.store.cards
        key = next((k for k in [main, *alts] if k in cards), None)
        log.info("Karte aufgelegt: %s (%s)", main, "bekannt" if key else "unbekannt")
        if self.learn and time.monotonic() < self.learn["until"]:
            spec, self.learn = self.learn["spec"], None
            for k in [main, *alts]:
                cards.pop(k, None)
            cards[main] = spec
            self.store.save_cards()
            self.emit("learned", {"id": main, **spec})
            return
        self.mqtt.publish_card(main, bool(key))
        if not key:
            self.last_unknown = main
            self.emit("card", {"id": main, "known": False})
            return
        if key != main:  # importierte Karte auf neue Schreibweise umstellen
            card = cards.pop(key)
            card.pop("legacy", None)
            cards[main] = card
            self.store.save_cards()
        card = cards[main]
        self.emit("card", {"id": main, "known": True, **card})
        try:
            self._run_card(main, card)
        except Exception as e:
            log.warning("Karte %s: %s", main, e)
            self.toast(str(e))

    def _run_card(self, card_id, card):
        if card.get("type") == "command":
            self._do(card["target"])
            return
        st = self.player.state
        target = card.get("target", "")
        same = self.active_card == card_id and target in (st.get("folder"), st.get("url"))
        if same and st.get("state") == "play":
            return
        if same and st.get("state") == "pause":
            self.player.play()
            return
        if card.get("type") == "url":
            self.player.play_url(target)
        else:
            self.player.play_folder(target, shuffle=bool(card.get("shuffle")))
        self.active_card = card_id

    def on_remove(self):
        log.info("Karte entfernt")
        self.emit("card_removed", {})
        if self.store.config["audio"]["pause_on_remove"] and self.active_card:
            self.player.pause()


# =====================================================================
# Web-API
# =====================================================================
routes = web.RouteTableDef()


def box(request) -> Box:
    return request.app["box"]


def ok(data=None):
    return web.json_response(data if data is not None else {"ok": True})


@web.middleware
async def guard(request, handler):
    # Schutz vor fremden Webseiten, die im Heimnetz Befehle absetzen wollen
    if request.method != "GET":
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return web.json_response({"error": "Fremde Herkunft"}, status=403)
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (LibraryError, BackupError, ValueError, KeyError) as e:
        msg = str(e) if not isinstance(e, KeyError) else f"Angabe fehlt: {e}"
        return web.json_response({"error": msg}, status=400)
    except ConnectionError as e:
        return web.json_response({"error": str(e)}, status=503)
    except Exception as e:
        log.exception("Fehler bei %s", request.path)
        return web.json_response({"error": f"Interner Fehler: {e}"}, status=500)


@routes.get("/api/state")
async def api_state(request):
    return ok(box(request).full_state())


@routes.get("/api/events")
async def api_events(request):
    b = box(request)
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})
    await resp.prepare(request)
    q = asyncio.Queue(maxsize=100)
    b.clients.add(q)
    try:
        await resp.write(f"event: state\ndata: {json.dumps(b.full_state())}\n\n".encode())
        while True:
            try:
                event, data = await asyncio.wait_for(q.get(), 20)
                await resp.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
        pass
    finally:
        b.clients.discard(q)
    return resp


@routes.post("/api/player")
async def api_player(request):
    d = await request.json()
    b = box(request)
    await asyncio.to_thread(b._do, d["action"], d.get("value"))
    return ok(b.full_state())


@routes.get("/api/queue")
async def api_queue(request):
    return ok(await asyncio.to_thread(box(request).player.queue))


# ---------- Musik ----------
@routes.get("/api/library")
async def api_library(request):
    b = box(request)
    folders = await asyncio.to_thread(b.library.folders)
    by_folder = {}
    for cid, c in b.store.cards.items():
        if c.get("type", "folder") == "folder":
            by_folder.setdefault(c.get("target"), []).append(cid)
    for f in folders:
        f["cards"] = by_folder.get(f["name"], [])
        f["resume"] = f["name"] in b.store.resume
    return ok({"folders": folders, "disk": b.library.disk()})


@routes.get("/api/library/tracks")
async def api_tracks(request):
    return ok(await asyncio.to_thread(box(request).library.tracks, request.query["folder"]))


@routes.post("/api/library/folder")
async def api_folder_create(request):
    d = await request.json()
    return ok({"name": box(request).library.create(d["name"])})


def _retarget(b, old, new):
    """Karten und Fortsetzungs-Positionen nach Umbenennen/Löschen anpassen."""
    changed = False
    for c in b.store.cards.values():
        t = c.get("target", "")
        if c.get("type", "folder") == "folder" and (t == old or t.startswith(old + "/")):
            if new is not None:
                c["target"] = new + t[len(old):]
                changed = True
    if new is None:
        for cid in [k for k, c in b.store.cards.items() if c.get("type", "folder") == "folder"
                    and (c.get("target") == old or str(c.get("target", "")).startswith(old + "/"))]:
            del b.store.cards[cid]
            changed = True
    if changed:
        b.store.save_cards()
    for k in [k for k in b.store.resume if k == old or k.startswith(old + "/")]:
        v = b.store.resume.pop(k)
        if new is not None:
            b.store.resume[new + k[len(old):]] = v
    b.store.save_resume()


@routes.post("/api/library/rename")
async def api_rename(request):
    d = await request.json()
    b = box(request)
    if d.get("file"):
        b.library.rename_file(d["folder"], d["file"], d["name"])
        new = d["folder"]
    else:
        old = d["folder"].strip("/")
        new = b.library.rename(old, d["name"])
        _retarget(b, old, new)
    await asyncio.to_thread(b.player.update_db)
    return ok({"name": new})


@routes.post("/api/library/delete")
async def api_delete(request):
    d = await request.json()
    b = box(request)
    if d.get("file"):
        b.library.delete_file(d["folder"], d["file"])
    else:
        if b.player.state.get("folder") == d["folder"].strip("/"):
            await asyncio.to_thread(b.player.stop)
        b.library.delete_folder(d["folder"])
        _retarget(b, d["folder"].strip("/"), None)
    await asyncio.to_thread(b.player.update_db)
    return ok()


@routes.post("/api/library/upload")
async def api_upload(request):
    b = box(request)
    folder = request.query["folder"]
    reader = await request.multipart()
    saved, errors = [], []
    while True:
        part = await reader.next()
        if part is None:
            break
        if not part.filename:
            continue
        try:
            target = b.library.upload_target(folder, part.filename)
        except LibraryError as e:
            errors.append(str(e))
            await part.release()
            continue
        tmp = target.with_name("." + target.name + ".part")
        try:
            with open(tmp, "wb") as f:
                while True:
                    chunk = await part.read_chunk(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, target)
            saved.append(target.name)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            errors.append(f"{part.filename}: {e.strerror or e}")
    await asyncio.to_thread(b.player.update_db)
    return ok({"saved": saved, "errors": errors})


@routes.get("/api/cover")
async def api_cover(request):
    data, mime = await asyncio.to_thread(box(request).library.cover_bytes, request.query["folder"])
    if data is None:
        raise web.HTTPNotFound()
    headers = {"Cache-Control": "max-age=60"}
    if isinstance(data, Path):
        return web.FileResponse(data, headers=headers)
    return web.Response(body=data, content_type=mime or "image/jpeg", headers=headers)


@routes.post("/api/cover")
async def api_cover_set(request):
    reader = await request.multipart()
    part = await reader.next()
    if part is None or not part.filename:
        raise ValueError("Kein Bild erhalten")
    data = await part.read(decode=False)
    if len(data) > 15_000_000:
        raise ValueError("Das Bild ist zu groß (max. 15 MB)")
    box(request).library.set_cover(request.query["folder"], bytes(data), Path(part.filename).suffix)
    return ok()


# ---------- Karten ----------
def _card_spec(d):
    t = d.get("type", "folder")
    target = str(d.get("target", "")).strip()
    if t == "command":
        if target not in BUTTON_ACTIONS:
            raise ValueError("Unbekannter Befehl")
    elif t == "url":
        Box._check_url(target)
    elif t == "folder":
        target = target.strip("/")
        if not target:
            raise ValueError("Bitte einen Ordner wählen")
    else:
        raise ValueError("Unbekannte Kartenart")
    spec = {"type": t, "target": target, "shuffle": bool(d.get("shuffle"))}
    if d.get("label"):
        spec["label"] = str(d["label"])[:80]
    return spec


@routes.get("/api/cards")
async def api_cards(request):
    b = box(request)
    cards = [{"id": k, **v} for k, v in b.store.cards.items()]
    for c in cards:
        if c.get("type", "folder") == "folder":
            try:
                c["missing"] = not b.library.path(c["target"]).is_dir()
            except LibraryError:
                c["missing"] = True
    cards.sort(key=lambda c: (c.get("type", "folder"), str(c.get("target", "")).lower()))
    return ok({"cards": cards, "last_unknown": b.last_unknown,
               "reader_ok": bool(b.watcher and b.watcher.ok) and not b.reader_error,
               "reader_error": b.reader_error})


@routes.post("/api/cards/learn")
async def api_learn(request):
    b = box(request)
    d = await request.json()
    spec = _card_spec(d)
    if spec["type"] == "folder":
        b.library.path(spec["target"])
    if d.get("id"):  # eben gelesene, unbekannte Karte direkt zuweisen
        b.store.cards[str(d["id"])] = spec
        b.store.save_cards()
        return ok({"id": d["id"]})
    b.learn = {"spec": spec, "until": time.monotonic() + 30}
    return ok({"waiting": 30})


@routes.post("/api/cards/learn/cancel")
async def api_learn_cancel(request):
    box(request).learn = None
    return ok()


@routes.post("/api/cards/update")
async def api_card_update(request):
    b = box(request)
    d = await request.json()
    if d["id"] not in b.store.cards:
        raise ValueError("Karte nicht gefunden")
    b.store.cards[d["id"]] = _card_spec(d)
    b.store.save_cards()
    return ok()


@routes.post("/api/cards/delete")
async def api_card_delete(request):
    b = box(request)
    b.store.cards.pop((await request.json())["id"], None)
    b.store.save_cards()
    return ok()


# ---------- Einstellungen ----------
@routes.get("/api/config")
async def api_config(request):
    b = box(request)
    return ok({"config": b.store.public_config(), "actions": BUTTON_ACTIONS, "pins": BUTTON_PINS,
               "button_errors": b.buttons.errors,
               "mqtt": {"connected": b.mqtt.connected, "error": b.mqtt.error}})


def _clean_patch(b, d):
    patch = {}
    if "name" in d:
        patch["name"] = str(d["name"]).strip()[:40] or "Klangkiste"
    if "audio" in d:
        a = d["audio"]
        out = {}
        for k in ("volume_start", "volume_max", "volume_step"):
            if k in a:
                out[k] = max(1, min(100, int(a[k])))
        for k in ("resume", "pause_on_remove"):
            if k in a:
                out[k] = bool(a[k])
        patch["audio"] = out
    if "buttons" in d:
        btns, seen = [], set()
        for x in d["buttons"]:
            pin = int(x["pin"])
            if pin not in BUTTON_PINS:
                raise ValueError(f"GPIO {pin} ist nicht für Knöpfe frei")
            if pin in seen:
                raise ValueError(f"GPIO {pin} ist doppelt belegt")
            if x["action"] not in BUTTON_ACTIONS:
                raise ValueError("Unbekannte Knopf-Funktion")
            seen.add(pin)
            btns.append({"pin": pin, "action": x["action"]})
        patch["buttons"] = btns
    if "mqtt" in d:
        m = dict(d["mqtt"])
        if not m.get("password"):
            m.pop("password", None)
        if "port" in m:
            m["port"] = int(m["port"])
        m.pop("password_set", None)
        patch["mqtt"] = m
    if "backup" in d:
        k = dict(d["backup"])
        if not k.get("password"):
            k.pop("password", None)
        k.pop("password_set", None)
        for f in ("hour", "weekday", "keep"):
            if f in k:
                k[f] = int(k[f])
        if k.get("schedule") not in (None, "off", "daily", "weekly"):
            raise ValueError("Unbekannter Zeitplan")
        if "url" in k:
            Box._check_url(k["url"])
        patch["backup"] = k
    return patch


@routes.post("/api/config")
async def api_config_set(request):
    b = box(request)
    d = await request.json()
    patch = _clean_patch(b, d)
    b.store.update_config(patch)
    if "buttons" in patch:
        await asyncio.to_thread(b.buttons.apply)
    if "mqtt" in patch or "name" in patch:
        b.mqtt.start()
    if "audio" in patch:
        vmax = b.store.config["audio"]["volume_max"]
        if b.player.state.get("volume", 0) > vmax:
            await asyncio.to_thread(b.player.set_volume, vmax)
    b.emit("state", b.full_state())
    return ok({"config": b.store.public_config(), "button_errors": b.buttons.errors})


@routes.post("/api/buttons/learn")
async def api_button_learn(request):
    pin = await asyncio.to_thread(box(request).buttons.learn, 15)
    return ok({"pin": pin})


# ---------- Sicherung ----------
@routes.get("/api/backup/status")
async def api_backup_status(request):
    b = box(request)
    return ok({**b.backup.status, "configured": b.backup.configured()})


@routes.post("/api/backup/run")
async def api_backup_run(request):
    b = box(request)
    if not b.backup.configured():
        raise BackupError("Bitte erst Benutzername und Passwort für HiDrive eintragen")

    async def go():
        try:
            await b.backup.run()
        except BackupError:
            pass

    asyncio.create_task(go())
    return ok()


@routes.post("/api/backup/test")
async def api_backup_test(request):
    await box(request).backup.test()
    return ok()


@routes.get("/api/backup/list")
async def api_backup_list(request):
    return ok(await box(request).backup.list())


@routes.post("/api/backup/restore")
async def api_backup_restore(request):
    d = await request.json()
    b = box(request)
    await b.backup.restore(d["name"], bool(d.get("music", True)))
    await asyncio.to_thread(b.player.update_db)
    return ok()


@routes.get("/api/backup/download")
async def api_backup_download(request):
    data = box(request).backup.make_archive()
    name = time.strftime("klangkiste-%Y-%m-%d.tar.gz")
    return web.Response(body=data, content_type="application/gzip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@routes.post("/api/backup/upload")
async def api_backup_upload(request):
    reader = await request.multipart()
    part = await reader.next()
    if part is None:
        raise ValueError("Keine Datei erhalten")
    data = await part.read(decode=False)
    box(request).backup.apply_archive(bytes(data))
    return ok()


@routes.post("/api/import/legacy")
async def api_import(request):
    b = box(request)
    reader = await request.multipart()
    part = await reader.next()
    if part is None:
        raise ValueError("Keine Datei erhalten")
    tmp = b.store.dir / "import-upload.tar"
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = await part.read_chunk(1024 * 512)
                if not chunk:
                    break
                f.write(chunk)
        try:
            stats = await asyncio.to_thread(import_legacy, tmp, b.store)
        except (OSError, EOFError) as e:
            raise ValueError(f"Das Archiv konnte nicht gelesen werden: {e}")
        except Exception as e:
            if type(e).__name__ in ("ReadError", "TarError", "BadGzipFile"):
                raise ValueError("Das ist kein gültiges tar-Archiv")
            raise
    finally:
        tmp.unlink(missing_ok=True)
    await asyncio.to_thread(b.player.update_db)
    return ok(stats)


# ---------- System ----------
@routes.get("/api/system")
async def api_system(request):
    b = box(request)
    info = await asyncio.to_thread(system.info, __version__)
    info.update({"disk": b.library.disk(), "mock": b.mock, "mpd": b.player.state.get("state") != "offline",
                 "stream_url": f"http://{info['ip'] or info['hostname']}:{b.store.config['audio']['stream_port']}/klangkiste.mp3"})
    return ok(info)


@routes.post("/api/system")
async def api_system_action(request):
    b = box(request)
    action = (await request.json())["action"]
    if action == "update":
        return ok(await system.update(b.mock))
    if action not in ("reboot", "shutdown", "restart"):
        raise ValueError("Unbekannte Aktion")
    if action != "restart":
        await asyncio.to_thread(b.player.stop)
    asyncio.get_running_loop().call_later(0.5, lambda: asyncio.ensure_future(system.power(action, b.mock)))
    return ok()


@routes.get("/api/homeassistant")
async def api_ha(request):
    b = box(request)
    ip = system.ip_address() or "IP-DER-BOX"
    base = b.store.config["mqtt"]["base_topic"]
    port = b.store.config["audio"]["stream_port"]
    text = f"""# In Home Assistant unter Einstellungen > Automatisierungen > Skripte
# als neues Skript im YAML-Modus einfügen. „media_player.wohnzimmer“
# durch dein MusicCast-Gerät ersetzen.
alias: Klangkiste im Haus mithören
sequence:
  - action: mqtt.publish
    data:
      topic: {base}/cmd/stream
      payload: "ON"
  - delay: "00:00:02"
  - action: media_player.play_media
    target:
      entity_id: media_player.wohnzimmer
    data:
      media_content_type: music
      media_content_id: "http://{ip}:{port}/klangkiste.mp3"
"""
    return web.Response(text=text, content_type="text/plain", charset="utf-8")


@routes.post("/api/hook/spotify")
async def api_spotify(request):
    if request.remote not in ("127.0.0.1", "::1"):
        raise web.HTTPForbidden()
    b = box(request)
    event = (await request.post()).get("event", "")
    if event in ("playing", "started", "session_connected", "track_changed"):
        b.spotify = True
        b.active_card = None
        await asyncio.to_thread(b.player.pause)
    elif event in ("paused", "stopped", "session_disconnected", "end_of_track"):
        b.spotify = False
    b.emit("state", b.full_state())
    return ok()


# ---------- nur im Simulationsmodus ----------
@routes.post("/api/debug/card")
async def api_debug_card(request):
    b = box(request)
    if not b.mock:
        raise web.HTTPNotFound()
    b.reader.uid = (await request.json()).get("uid")
    return ok()


async def index(request):
    return web.FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@web.middleware
async def no_cache_static(request, handler):
    resp = await handler(request)
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def make_app(args):
    app = web.Application(middlewares=[guard, no_cache_static], client_max_size=64 * 1024 * 1024)
    b = Box(args)
    app["box"] = b
    app.add_routes(routes)
    app.router.add_get("/", index)
    app.router.add_static("/static/", WEB)
    app.on_startup.append(b.start)
    app.on_cleanup.append(b.stop)
    return app


def main():
    ap = argparse.ArgumentParser(description="Klangkiste")
    ap.add_argument("--data", default="/var/lib/klangkiste")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--mpd-host", default="127.0.0.1")
    ap.add_argument("--mpd-port", type=int, default=6600)
    ap.add_argument("--mock", action="store_true", help="ohne Hardware laufen (Test)")
    args = ap.parse_args()
    if args.mock:
        os.environ.setdefault("GPIOZERO_PIN_FACTORY", "mock")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    web.run_app(make_app(args), host=args.host, port=args.port, access_log=None)
