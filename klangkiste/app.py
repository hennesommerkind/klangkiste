"""Klangkiste – Hauptprogramm: verbindet Player, Karten, Knöpfe, MQTT, Web."""
import argparse
import asyncio
import threading
import contextlib
import json
import logging
import mimetypes
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse, quote

from aiohttp import web

from . import __version__, system
from .backup import Backup, BackupError
from .bluetooth import Bluetooth, BluetoothError
from . import sounds
from .network import HOTSPOT_IP, Network, NetworkError
from .buttons import Buttons
from .config import BUTTON_ACTIONS, BUTTON_PINS, Store
from .library import IMAGES, Library, LibraryError
from .mqtt import Mqtt
from .player import Player
from .rfid import CardWatcher, MockReader
from .spotify import Spotify

# Befehle, die bei laufendem Spotify an Spotify gehen
SPOTIFY_ACTIONS = {"play_pause", "play", "pause", "stop", "next", "prev", "seek", "volume", "vol_up", "vol_down", "vol_delta"}

log = logging.getLogger("klangkiste")
# Nicht auf die Systemtabelle verlassen: falsche Typen lassen Browser
# Stildatei oder Skript stillschweigend verwerfen.
for _ext, _type in ((".css", "text/css"), (".js", "text/javascript"), (".svg", "image/svg+xml"),
                    (".html", "text/html"), (".webp", "image/webp"), (".png", "image/png"),
                    (".webmanifest", "application/manifest+json")):
    mimetypes.add_type(_type, _ext)
WEB = Path(__file__).resolve().parent.parent / "web"


class Box:
    def __init__(self, args):
        self.args = args
        self.mock = args.mock
        self.store = Store(args.data)
        self.library = Library(self.store.music)
        self.player = Player(self.store, args.mpd_host, args.mpd_port, self._on_player,
                             files_for=self.library.all_files,
                             resumable=lambda f: not self.library.is_music(f))
        self.buttons = Buttons(self.store, self.dispatch_threadsafe)
        self.mqtt = Mqtt(self.store, self._mqtt_cmd, self.full_state, __version__)
        self.backup = None
        self.loop = None
        self.clients = set()
        self._unmute_to = 0
        self._vol_lock = threading.Lock()
        self._vol_target, self._vol_time = 0, 0.0
        self.closing = False
        self.learn = None
        self.active_card = None
        self.last_unknown = None
        self.spotify = Spotify(self._on_spotify, args.spotify_url)
        self.bluetooth = Bluetooth(self.store, self._on_headphones)
        self._activity = time.monotonic()
        self.network = Network(self.store, self._on_network, self.last_activity)
        self.controller = "box"          # wer gerade gesteuert wird: „box“ (Karten) oder „spotify“
        self.resume_after_spotify = False
        self.update_info = {}
        self.reader = None
        self.watcher = None
        self.reader_error = ""

    # ---------- Start / Stopp ----------
    async def start(self, app):
        self.loop = asyncio.get_running_loop()
        self.backup = Backup(self.store, on_restored=self._after_restore,
                             on_status=lambda s: self.emit("backup", s))
        self.player.start()
        await asyncio.to_thread(system.running_revision)   # jetzt merken, nicht erst während eines Updates
        await asyncio.to_thread(self._init_hardware)
        self.mqtt.start()
        app["scheduler"] = asyncio.create_task(self.backup.scheduler())
        app["updates"] = asyncio.create_task(self._update_checker())
        app["watcher"] = asyncio.create_task(self._library_watcher())
        asyncio.create_task(self._startup_volume())
        self.spotify.start()
        self.bluetooth.start()
        if not self.mock:
            self.network.start()
        asyncio.create_task(self._spotify_output(False))

    def _init_hardware(self):
        try:
            self.buttons.apply()
        except Exception as e:
            log.warning("Knöpfe nicht verfügbar: %s", e)
        if not self.store.config["system"]["leds"]:
            system.set_leds(False)
        cfg = self.store.config["rfid"]
        if self.mock:
            self.reader = MockReader()
        elif cfg["enabled"]:
            try:
                from .rfid import MFRC522
                self.reader = MFRC522(cfg["spi_bus"], cfg["spi_device"], cfg["rst_pin"],
                                      cfg.get("spi_speed", 1000000), cfg.get("gain", 4))
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
        for _ in range(180):  # MPD startet parallel und kann etwas brauchen
            if self.player.state.get("state") != "offline":
                await asyncio.to_thread(self.player.set_headphones, self.bluetooth.connected)
                await asyncio.to_thread(self.player.set_volume, self.store.config["audio"]["volume_start"])
                if system.booted_recently() and not self.mock:
                    # Ton nur beim Systemstart, nicht nach Updates oder Neustart des Dienstes
                    await asyncio.to_thread(sounds.chime, self.store.config["audio"]["volume_start"])
                return
            await asyncio.sleep(1)

    async def _update_checker(self):
        await asyncio.sleep(90)
        while True:
            try:
                self.update_info = await system.check_updates()
                if self.update_info.get("available"):
                    self.emit("update", self.update_info)
            except Exception as e:
                log.info("Update-Prüfung: %s", e)
            await asyncio.sleep(6 * 3600)

    async def stop(self, app):
        app["scheduler"].cancel()
        app["updates"].cancel()
        await self.spotify.stop()
        self.bluetooth.stop()
        self.network.stop()
        if self.watcher:
            self.watcher.stop()
        self.buttons.close()
        self.mqtt.stop()
        self.player.close()

    async def _library_watcher(self):
        """Dateien können auch am Netzlaufwerk vorbei auf die Box kommen. Ändert sich der
        Musikordner und bleibt danach eine Runde lang ruhig (Kopieren fertig), lernt MPD ihn neu."""
        last = pending = None
        while True:
            await asyncio.sleep(20)
            try:
                sig = await asyncio.to_thread(self.library.signature)
                if last is None:
                    last = sig
                elif sig != last:
                    if sig == pending:
                        last, pending = sig, None
                        log.info("Musikordner hat sich geändert – Datenbank wird aktualisiert")
                        await asyncio.to_thread(self.player.update_db)
                    else:
                        pending = sig
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Musikordner überwachen")

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
        s.update({"card": self.active_card, "spotify": False, "cover_url": None,
                  "controller": self.controller, "name": self.store.config["name"],
                  "update": bool(self.update_info.get("available")), "vmax": self.player.vmax(),
                  "figure": None, "figure_scale": 1.0})
        if s.get("folder") and self.controller != "spotify":
            fig, v, sc = self.library.figure_for(s["folder"])
            if fig:
                s["figure"] = f"/api/figure?folder={quote(fig)}&v={v}"
                s["figure_scale"] = sc
        if self.controller == "spotify":
            sp = self.spotify.info()
            s.update({"headphones": self.bluetooth.connected,
                      "state": "play" if sp["playing"] else "pause", "title": sp["title"] or "Spotify",
                      "artist": sp["artist"], "album": sp["album"], "file": "spotify", "folder": None,
                      "url": None, "track": 0, "tracks": 0, "elapsed": sp["elapsed"],
                      "duration": sp["duration"], "random": False, "spotify": True,
                      "cover_url": sp["cover"] or None})
            if sp["volume"] is not None:
                s["volume"] = sp["volume"]
        return s

    async def close_clients(self, app=None):
        """Offene Ereignis-Verbindungen der Browser beenden. Ohne das wartet der Dienst
        beim Beenden auf jede offene Seite – ein Neustart dauerte dann über eine Minute."""
        self.closing = True
        for q in list(self.clients):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)

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
        full = self.full_state()
        self.emit("state", full)
        self.mqtt.publish_state(full)

    def _on_spotify(self, kind):
        """Läuft im Ereignis-Thread. Spotify übernimmt beim Abspielen, die Karte
        läuft nach dem Ende der Spotify-Sitzung weiter."""
        if kind == "playing" and self.controller != "spotify":
            self.controller = "spotify"
            if self.player.state.get("state") == "play":
                self.resume_after_spotify = True
                self.loop.run_in_executor(None, self.player.pause)
            log.info("Spotify übernimmt%s", " – Karte pausiert" if self.resume_after_spotify else "")
        elif kind == "ended" and self.controller == "spotify":
            self.controller = "box"
            if self.resume_after_spotify:
                self.resume_after_spotify = False
                log.info("Spotify beendet – Karte läuft weiter")
                self.loop.run_in_executor(None, self.player.play)
            else:
                log.info("Spotify beendet")
        full = self.full_state()
        self._emit("state", full)
        self.mqtt.publish_state(full)

    # ---------- Netzwerk und Ansagen ----------
    def last_activity(self):
        return time.monotonic() if self.clients else self._activity

    def _on_network(self, kind, info):
        """Läuft im Ereignis-Thread."""
        if kind == "connected_from_hotspot":
            # Nur nach dem Wechsel aus dem Not-WLAN ansagen – sonst nur auf Knopfdruck
            self.loop.run_in_executor(None, self._announce_later)
        elif kind == "hotspot":
            ssid = self.store.config["wifi"]["hotspot_ssid"]
            self.loop.run_in_executor(None, self._say_paused,
                                      f"Kein bekanntes WLAN gefunden. Verbinde dich mit dem WLAN {ssid} "
                                      f"und öffne im Browser {sounds.speakable_ip(HOTSPOT_IP)}.")
        self._emit("network", {"kind": kind, "info": info})

    def _announce_later(self):
        time.sleep(8)  # Adresse vom Router abwarten
        self.announce(False)

    def _say_paused(self, text):
        """Ansage sprechen; laufende Wiedergabe dafür kurz anhalten."""
        was_playing = self.controller == "box" and self.player.state.get("state") == "play"
        if was_playing:
            self.player.pause()
        vol = max(25, min(self.player.vmax(), self.player.state.get("volume") or self.store.config["audio"]["volume_start"]))
        ok = sounds.say(text, vol)
        if was_playing:
            self.player.play()
        return ok

    def announce(self, force=True):
        """Adresse ansagen (nach dem Verbinden oder Play/Pause drei Sekunden halten)."""
        if self.network.hotspot:
            ssid = self.store.config["wifi"]["hotspot_ssid"]
            text = f"Ich bin im eigenen WLAN {ssid}. Adresse {sounds.speakable_ip(HOTSPOT_IP)}."
        else:
            ip = system.ip_address()
            if not ip:
                text = "Ich habe gerade keine Netzwerkverbindung."
            else:
                text = f"Meine Adresse ist {sounds.speakable_ip(ip)}."
        log.info("Ansage: %s", text)
        if not self._say_paused(text) and force:
            self.toast("Keine Sprachausgabe installiert – bitte ein Update einspielen")

    def _on_headphones(self, connected, name):
        """Läuft im Ereignis-Thread: Ausgabe von Box und Spotify umschalten."""
        self.loop.run_in_executor(None, self.player.set_headphones, connected)
        if self.controller == "spotify" and not connected:
            asyncio.ensure_future(self._spotify_quiet("pause"))
        asyncio.ensure_future(self._spotify_output(connected))
        self._emit("toast", {"message": f"Kopfhörer „{name}“ verbunden" if connected
                             else "Kopfhörer getrennt – Wiedergabe pausiert", "kind": ""})
        self._emit("state", self.full_state())

    async def _spotify_output(self, headphones):
        """go-librespot hat keinen Ausgabewechsel im Betrieb: Konfiguration
        anpassen und den Dienst neu starten (die Spotify-Sitzung beginnt neu)."""
        p = self.store.dir / "spotify" / "config.yml"
        try:
            text = p.read_text("utf-8")
        except OSError:
            return
        want = "bluealsa" if headphones else "default"
        new = re.sub(r"(?m)^audio_device:.*$", f"audio_device: {want}", text)
        if new == text:
            return
        try:
            p.write_text(new, "utf-8")
            await system.restart_spotify(self.mock)
            log.info("Spotify spielt jetzt über %s", "Kopfhörer" if headphones else "Lautsprecher")
        except Exception as e:
            log.warning("Spotify-Ausgabe umschalten: %s", e)

    def _take_over_from_spotify(self):
        """Karte oder Oberfläche startet etwas: Spotify pausieren, Box steuern."""
        self.resume_after_spotify = False
        if self.controller == "spotify":
            self.controller = "box"
            if self.loop:
                asyncio.run_coroutine_threadsafe(self._spotify_quiet("pause"), self.loop)

    async def _spotify_quiet(self, action):
        try:
            await self.spotify.command(action, vmax=self.player.vmax())
        except Exception as e:
            log.info("Spotify: %s", e)

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
        if action == "mute":   # Stumm / Laut – gilt für das, was gerade spielt
            cur = int(self.full_state().get("volume") or 0)
            if cur > 0:
                self._unmute_to = cur
                return self._do("volume", 0)
            return self._do("volume", self._unmute_to or self.store.config["audio"]["volume_start"])
        if action == "vol_delta":
            with self._vol_lock:   # Befehle vom Drehregler nacheinander, nicht durcheinander
                if self.controller == "spotify":
                    asyncio.run_coroutine_threadsafe(
                        self.spotify.command(action, value, p.vmax()), self.loop).result(timeout=8)
                    return
                now = time.monotonic()
                # MPD meldet die neue Lautstärke mit Verzögerung – kurz mit dem eigenen Ziel weiterrechnen
                base = self._vol_target if now - self._vol_time < 1.5 else int(p.state.get("volume") or 0)
                self._vol_target = max(0, min(p.vmax(), base + int(value)))
                self._vol_time = now
                p.set_volume(self._vol_target)
            return
        if self.controller == "spotify" and action in SPOTIFY_ACTIONS:
            fut = asyncio.run_coroutine_threadsafe(
                self.spotify.command(action, value, self.player.vmax()), self.loop)
            fut.result(timeout=8)
            return
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
            self._take_over_from_spotify()
            self.active_card = None
            p.play_folder(str(value).strip("/"), restart=False)
        elif action == "play_url":
            self._check_url(value)
            self._take_over_from_spotify()
            self.active_card = None
            p.play_url(value)
        elif action == "announce":
            self.announce(True)
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
        if self.learn and time.monotonic() < self.learn["until"]:
            log.info("Karte angelernt: %s", main)
            spec, self.learn = self.learn["spec"], None
            for k in [main, *alts]:
                cards.pop(k, None)
            cards[main] = spec
            self.store.save_cards()
            self.emit("learned", {"id": main, **spec})
            return
        log.info("Karte aufgelegt: %s (%s)", main, "bekannt" if key else "unbekannt")
        self.mqtt.publish_card(main, bool(key))
        if not key:
            self.last_unknown = main
            self.emit("card", {"id": main, "known": False})
            return
        card = cards[key]
        if card.get("type") != "command":
            self._take_over_from_spotify()
        self.emit("card", {"id": key, "known": True, **card})
        try:
            self._run_card(key, card)
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
            self.resume_after_spotify = False  # Figur ist weg: nach Spotify nicht weiterspielen
            if self.controller == "box":
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
    if request.path.startswith("/api/"):
        request.app["box"]._activity = time.monotonic()
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
        while not b.closing:
            try:
                item = await asyncio.wait_for(q.get(), 20)
                if item is None:      # Dienst wird beendet
                    break
                event, data = item
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


@routes.get("/api/library/search")
async def api_search(request):
    q = request.query.get("q", "")[:80]
    cat = request.query.get("cat") or None
    return ok(await asyncio.to_thread(box(request).library.search_tracks, q, cat))


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


@routes.post("/api/library/order")
async def api_order(request):
    d = await request.json()
    box(request).library.set_order(d["folder"], d["kind"], d["items"])
    return ok()


@routes.post("/api/library/move")
async def api_move(request):
    d = await request.json()
    b = box(request)
    old = d["folder"].strip("/")
    new = b.library.move(old, d["parent"])
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
    episode = request.query.get("episodes") == "1"
    reader = await request.multipart()
    saved, errors, episodes = [], [], []
    while True:
        part = await reader.next()
        if part is None:
            break
        if not part.filename:
            continue
        try:
            target, created = b.library.upload_target(folder, part.filename, episode)
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
            if created:
                episodes.append(b.library.rel(target.parent))
        except OSError as e:
            tmp.unlink(missing_ok=True)
            if created:
                with contextlib.suppress(OSError):
                    target.parent.rmdir()
            errors.append(f"{part.filename}: {e.strerror or e}")
    await asyncio.to_thread(b.player.update_db)
    return ok({"saved": saved, "errors": errors, "episodes": episodes})


@routes.get("/api/cover")
async def api_cover(request):
    data, mime = await asyncio.to_thread(box(request).library.cover_bytes, request.query["folder"])
    if data is None:
        raise web.HTTPNotFound()
    headers = {"Cache-Control": "max-age=60"}
    if isinstance(data, Path):
        return web.FileResponse(data, headers=headers)
    return web.Response(body=data, content_type=mime or "image/jpeg", headers=headers)


@routes.delete("/api/cover")
async def api_cover_delete(request):
    box(request).library.remove_cover(request.query["folder"])
    return ok()


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


@routes.get("/api/figure")
async def api_figure(request):
    f = await asyncio.to_thread(box(request).library.figure_file, request.query["folder"])
    if f is None:
        raise web.HTTPNotFound()
    return web.FileResponse(f, headers={"Content-Type": "image/png", "Cache-Control": "max-age=86400"})


@routes.post("/api/figure")
async def api_figure_set(request):
    data = await request.read()
    if len(data) > 4_000_000:
        raise ValueError("Das Bild der Figur ist zu groß")
    b = box(request)
    await asyncio.to_thread(b.library.set_figure, request.query["folder"], data)
    b.emit("state", b.full_state())
    return ok()


@routes.post("/api/figure/size")
async def api_figure_size(request):
    b = box(request)
    body = await request.json()
    scale = await asyncio.to_thread(b.library.set_figure_scale, request.query["folder"], body.get("scale", 1))
    b.emit("state", b.full_state())
    return ok({"scale": scale})


@routes.delete("/api/figure")
async def api_figure_delete(request):
    b = box(request)
    await asyncio.to_thread(b.library.remove_figure, request.query["folder"])
    b.emit("state", b.full_state())
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
                d = b.library.path(c["target"])
                c["missing"] = not d.is_dir()
                c["figure_v"] = 0 if c["missing"] else b.library._figure_v(d)
                c["figure_s"] = b.library._figure_s(d) if c["figure_v"] else 1.0
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
    if "system" in d:
        if "leds" in d["system"]:
            patch["system"] = {"leds": bool(d["system"]["leds"])}
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
    if "encoder" in d:
        e, cur = d["encoder"], b.store.config["encoder"]
        out = {"enabled": bool(e.get("enabled", cur["enabled"])), "reverse": bool(e.get("reverse", cur["reverse"])),
               "step": max(1, min(10, int(e.get("step", cur["step"])))), "press": e.get("press", cur["press"])}
        for k in ("clk", "dt", "sw"):
            out[k] = int(e.get(k, cur[k]))
            if out[k] not in BUTTON_PINS:
                raise ValueError(f"GPIO {out[k]} ist nicht für den Drehregler frei")
        if len({out["clk"], out["dt"], out["sw"]}) < 3:
            raise ValueError("Der Drehregler braucht drei verschiedene Anschlüsse")
        if out["press"] not in BUTTON_ACTIONS and out["press"] != "none":
            raise ValueError("Unbekannte Funktion für den Druck auf den Drehregler")
        used = {int(x["pin"]) for x in patch.get("buttons", b.store.config["buttons"])}
        if out["enabled"] and used & {out["clk"], out["dt"], out["sw"]}:
            raise ValueError("Ein Anschluss des Drehreglers ist schon mit einem Knopf belegt")
        patch["encoder"] = out
    if "wifi" in d:
        w = d["wifi"]
        out = {}
        if "hotspot" in w:
            out["hotspot"] = bool(w["hotspot"])
        if w.get("hotspot_ssid") is not None:
            ssid = str(w["hotspot_ssid"]).strip()
            if not 1 <= len(ssid.encode()) <= 32:
                raise ValueError("Der WLAN-Name braucht 1 bis 32 Zeichen")
            out["hotspot_ssid"] = ssid
        if w.get("hotspot_password"):
            pw = str(w["hotspot_password"])
            if not 8 <= len(pw) <= 63:
                raise ValueError("Das WLAN-Kennwort braucht 8 bis 63 Zeichen")
            out["hotspot_password"] = pw
        patch["wifi"] = out
    if "bluetooth" in d:
        bt = d["bluetooth"]
        out = {}
        if "volume_max" in bt:
            out["volume_max"] = max(5, min(100, int(bt["volume_max"])))
        if "auto_connect" in bt:
            out["auto_connect"] = bool(bt["auto_connect"])
        patch["bluetooth"] = out
    if "mqtt" in d:
        m = dict(d["mqtt"])
        if not m.get("password"):
            m.pop("password", None)
        if "host" in m:
            # „http://192.168.178.55:8123/“ → nur die Adresse; 8123 ist die Weboberfläche, nicht MQTT
            h = re.sub(r"^\w+://", "", str(m["host"]).strip()).split("/")[0]
            if re.fullmatch(r"[^:]+:\d+", h):
                h, p = h.rsplit(":", 1)
                if p != "8123":
                    m["port"] = p
            m["host"] = h
        if "port" in m:
            m["port"] = int(m["port"])
            if m["port"] == 8123:
                raise ValueError("Port 8123 ist die Weboberfläche von Home Assistant – MQTT läuft auf Port 1883")
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
    if "buttons" in patch or "encoder" in patch:
        await asyncio.to_thread(b.buttons.apply)
    if "system" in patch:
        await asyncio.to_thread(system.set_leds, patch["system"]["leds"])
    if "mqtt" in patch or "name" in patch:
        b.mqtt.start()
    if "audio" in patch or "bluetooth" in patch:
        vmax = b.player.vmax()
        if b.player.state.get("volume", 0) > vmax:
            await asyncio.to_thread(b.player.set_volume, vmax)
    b.emit("state", b.full_state())
    return ok({"config": b.store.public_config(), "button_errors": b.buttons.errors})


@routes.post("/api/buttons/learn")
async def api_button_learn(request):
    pin = await asyncio.to_thread(box(request).buttons.learn, 15)
    return ok({"pin": pin})


# ---------- Bluetooth-Kopfhörer ----------
@routes.get("/api/bluetooth")
async def api_bt(request):
    return ok(await box(request).bluetooth.status())


@routes.post("/api/bluetooth/{action}")
async def api_bt_action(request):
    bt = box(request).bluetooth
    a = request.match_info["action"]
    try:
        if a == "scan":
            return ok(await bt.scan())
        if a == "pair":
            await bt.pair((await request.json())["mac"])
        elif a == "connect":
            await bt.connect()
        elif a == "disconnect":
            await bt.disconnect()
        elif a == "forget":
            await bt.forget()
        else:
            raise web.HTTPNotFound()
    except BluetoothError as e:
        return web.json_response({"error": str(e)}, status=400)
    return ok(await bt.status())


# ---------- Netzlaufwerk ----------
@routes.get("/api/share")
async def api_share(request):
    return ok(await system.share_status(box(request).mock))


@routes.post("/api/share")
async def api_share_set(request):
    b, d = box(request), await request.json()
    try:
        await system.share_set(bool(d.get("enabled")), str(d.get("password") or ""), b.mock)
    except RuntimeError as e:
        return web.json_response({"error": str(e)}, status=400)
    return ok(await system.share_status(b.mock))


# ---------- WLAN ----------
@routes.get("/api/network")
async def api_net(request):
    return ok(await box(request).network.status())


@routes.post("/api/network/{action}")
async def api_net_action(request):
    net = box(request).network
    a = request.match_info["action"]
    d = await request.json() if request.can_read_body else {}
    try:
        if a == "scan":
            return ok(await net.scan())
        if a == "connect":
            ssid = str(d.get("ssid", "")).strip()
            if not ssid:
                raise NetworkError("Bitte ein WLAN wählen")
            await net.connect(ssid, str(d.get("password", "")))
        elif a == "forget":
            await net.forget(str(d["name"]))
        else:
            raise web.HTTPNotFound()
    except NetworkError as e:
        return web.json_response({"error": str(e)}, status=400)
    return ok(await net.status())


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


# ---------- System ----------
@routes.get("/api/system")
async def api_system(request):
    b = box(request)
    info = await asyncio.to_thread(system.info, __version__)
    info.update({"disk": b.library.disk(), "storage": await asyncio.to_thread(b.library.usage), "mock": b.mock, "mpd": b.player.state.get("state") != "offline",
                 "tts": sounds.tts_engine(),
                 "apk": (b.store.dir / "klangkiste.apk").is_file(),
                 "leds": system.leds_available(), "leds_on": b.store.config["system"]["leds"],
                 "spotify": b.spotify.available, "update_info": b.update_info,
                 "stream_url": f"http://{info['ip'] or info['hostname']}:{b.store.config['audio']['stream_port']}/klangkiste.mp3"})
    return ok(info)


@routes.post("/api/system")
async def api_system_action(request):
    b = box(request)
    action = (await request.json())["action"]
    if action == "announce":
        asyncio.get_running_loop().run_in_executor(None, b.announce, True)
        return ok()
    if action == "check_update":
        b.update_info = await system.check_updates()
        b.emit("state", b.full_state())
        return ok(b.update_info)
    if action == "update":
        await system.start_update(b.mock)
        return ok({"started": True})
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


# ---------- nur im Simulationsmodus ----------
@routes.post("/api/debug/card")
async def api_debug_card(request):
    b = box(request)
    if not b.mock:
        raise web.HTTPNotFound()
    b.reader.uid = (await request.json()).get("uid")
    return ok()


async def apk(request):
    """Die Android-App zum Herunterladen (lädt das Update aus den GitHub-Releases)."""
    f = box(request).store.dir / "klangkiste.apk"
    if not f.is_file():
        raise web.HTTPNotFound(text="Die App ist noch nicht auf der Box – bitte einmal aktualisieren.")
    return web.FileResponse(f, headers={"Content-Type": "application/vnd.android.package-archive",
                                        "Content-Disposition": 'attachment; filename="klangkiste.apk"',
                                        "Cache-Control": "no-cache"})


async def manifest(request):
    name = box(request).store.config["name"] or "Klangkiste"
    return web.json_response({
        "name": name, "short_name": name[:12], "lang": "de", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#fbf6ee", "theme_color": "#f2643d",
        "description": "Musik und Hörspiele auf der Klangkiste verwalten",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"},
        ],
    }, content_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


async def service_worker(request):
    return web.FileResponse(WEB / "sw.js", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


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
    app.router.add_get("/manifest.webmanifest", manifest)
    app.router.add_get("/klangkiste.apk", apk)
    app.router.add_get("/sw.js", service_worker)
    app.router.add_static("/static/", WEB)
    app.on_startup.append(b.start)
    app.on_shutdown.append(b.close_clients)
    app.on_cleanup.append(b.stop)
    return app


def main():
    ap = argparse.ArgumentParser(description="Klangkiste")
    ap.add_argument("--data", default="/var/lib/klangkiste")
    # Nur IPv4: Über eine öffentliche IPv6-Adresse geladen, stufen Browser die Seite als
    # „Internetseite“ ein und fragen bei jedem Zugriff auf die Box um Erlaubnis.
    ap.add_argument("--host", default="0.0.0.0", help="Standard: alle IPv4-Adressen")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--mpd-host", default="127.0.0.1")
    ap.add_argument("--mpd-port", type=int, default=6600)
    ap.add_argument("--spotify-url", default="http://127.0.0.1:3678")
    ap.add_argument("--mock", action="store_true", help="ohne Hardware laufen (Test)")
    args = ap.parse_args()
    if args.mock:
        os.environ.setdefault("GPIOZERO_PIN_FACTORY", "mock")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.captureWarnings(True)
    web.run_app(make_app(args), host=args.host, port=args.port, access_log=None, shutdown_timeout=3)
