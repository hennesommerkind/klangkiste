"""Spotify Connect über go-librespot: Ereignisse empfangen und fernsteuern.

go-librespot läuft als eigener Dienst und bietet lokal eine HTTP-Schnittstelle
(Port 3678) mit Websocket-Ereignissen. Darüber erfährt die Klangkiste, wann
Spotify spielt, und kann es mit den Knöpfen steuern.
"""
import asyncio
import json
import logging
import time

import aiohttp

log = logging.getLogger("spotify")


class Spotify:
    def __init__(self, on_event, base="http://127.0.0.1:3678"):
        self.base = base
        self.on_event = on_event      # on_event(art) – „playing“, „paused“, „ended“, „metadata“
        self.available = False        # Dienst erreichbar
        self.active = False           # ein Handy ist verbunden
        self.playing = False
        self.track = {}
        self.position = 0.0
        self.volume = None            # Prozent
        self.synced = time.monotonic()
        self._session = None
        self._task = None

    # ---------- Zustand ----------
    def elapsed(self):
        if self.playing:
            return self.position + (time.monotonic() - self.synced)
        return self.position

    def info(self):
        t = self.track
        return {
            "available": self.available,
            "active": self.active,
            "playing": self.playing,
            "title": t.get("name", ""),
            "artist": ", ".join(t.get("artist_names") or []),
            "album": t.get("album_name", ""),
            "cover": t.get("album_cover_url", ""),
            "duration": round((t.get("duration") or 0) / 1000, 1),
            "elapsed": round(self.elapsed(), 1),
            "volume": self.volume,
        }

    def _set_position(self, ms):
        self.position = (ms or 0) / 1000
        self.synced = time.monotonic()

    # ---------- Verbindung ----------
    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self._session:
            await self._session.close()

    async def _loop(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=3))
        delay = 2
        while True:
            try:
                await self._refresh_status()
                async with self._session.ws_connect(self.base + "/events", heartbeat=30) as ws:
                    if not self.available:
                        log.info("Spotify Connect verbunden")
                    self.available, delay = True, 2
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                await self._handle(json.loads(msg.data))
                            except Exception:
                                log.exception("Spotify-Ereignis")
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.available:
                    log.info("Spotify Connect nicht erreichbar: %s", e)
            if self.available or self.active:
                self.available = False
                if self.active or self.playing:
                    self.active = self.playing = False
                    self.on_event("ended")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _refresh_status(self):
        async with self._session.get(self.base + "/status") as r:
            if r.status == 204:
                self.active = self.playing = False
                return
            d = await r.json()
        self.active = True
        self.playing = not d.get("paused") and not d.get("stopped")
        if d.get("volume") is not None:
            self.volume = round(100 * d["volume"] / (d.get("volume_steps") or 100))
        if d.get("track"):
            self.track = d["track"]
            self._set_position(self.track.get("position"))

    async def _handle(self, ev):
        typ, data = ev.get("type"), ev.get("data") or {}
        if typ == "active":
            self.active = True
        elif typ == "metadata":
            self.track = data
            self._set_position(data.get("position"))
            self.on_event("metadata")
        elif typ in ("playing", "will_play"):
            if typ == "playing":
                self.synced = time.monotonic()
            self.active = True
            was = self.playing
            self.playing = True
            if not was or typ == "will_play":
                self.on_event("playing")
        elif typ == "paused":
            self.position = self.elapsed()
            self.playing = False
            self.on_event("paused")
        elif typ == "volume":
            self.volume = round(100 * data.get("value", 0) / (data.get("max") or 100))
            self.on_event("metadata")
        elif typ == "seek":
            self._set_position(data.get("position"))
            self.on_event("metadata")
        elif typ in ("stopped", "not_playing", "inactive"):
            # Sitzung beendet, anderes Gerät gewählt oder Wiedergabe zu Ende
            self.playing = False
            if typ == "inactive":
                self.active = False
                self.track = {}
            self.on_event("ended")

    # ---------- Steuern ----------
    async def _post(self, path, body=None):
        if not self._session:
            raise ConnectionError("Spotify Connect ist nicht erreichbar")
        try:
            async with self._session.post(self.base + path, json=body or {},
                                          timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status >= 400:
                    raise ConnectionError(f"Spotify antwortet mit Fehler {r.status}")
                return await r.read()
        except aiohttp.ClientError as e:
            raise ConnectionError(f"Spotify Connect ist nicht erreichbar: {e}") from e

    async def command(self, action, value=None, vmax=100):
        if action == "play_pause":
            await self._post("/player/playpause")
        elif action == "play":
            await self._post("/player/resume")
        elif action in ("pause", "stop"):
            await self._post("/player/pause")
        elif action == "next":
            await self._post("/player/next")
        elif action == "prev":
            await self._post("/player/prev", {"allow_seeking": True})
        elif action == "seek":
            await self._post("/player/seek", {"position": int(float(value) * 1000)})
        elif action in ("vol_up", "vol_down", "volume", "vol_delta"):
            async with self._session.get(self.base + "/player/volume") as r:
                v = await r.json()
            cur, mx = int(v.get("value", 0)), int(v.get("max", 100)) or 100
            limit = round(mx * vmax / 100)
            if action == "volume":
                new = round(mx * int(float(value)) / 100)
            elif action == "vol_delta":
                new = cur + round(mx * float(value) / 100)
            else:
                new = cur + (1 if action == "vol_up" else -1) * max(1, round(mx * 0.05))
            await self._post("/player/volume", {"volume": max(0, min(limit, new))})
        else:
            raise ValueError(f"Spotify kann „{action}“ nicht")
