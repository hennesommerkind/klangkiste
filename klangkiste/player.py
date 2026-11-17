"""Wiedergabe über MPD (Music Player Daemon)."""
import logging
import threading
import time

import mpd

log = logging.getLogger("player")
STREAM_OUTPUT = "Hausstream"
SPEAKER_OUTPUT = "Lautsprecher"
HEADPHONE_OUTPUT = "Kopfhörer"


class Player:
    def __init__(self, store, host="127.0.0.1", port=6600, on_change=None, files_for=None, resumable=None):
        self.files_for = files_for            # Ordner -> Titelliste in richtiger Reihenfolge
        self.resumable = resumable or (lambda folder: True)  # Hörspiel ja, Musik nein
        self.store = store
        self.host, self.port = host, port
        self.on_change = on_change or (lambda s: None)
        self._c = mpd.MPDClient()
        self._c.timeout = 10
        self._lock = threading.RLock()
        self._connected = False
        self._stop = threading.Event()
        self.state = {"state": "offline", "volume": 0}
        self._last_cmp = None
        self._last_resume_write = 0.0
        self._user_stop = False
        self._prev = {}
        self.current_folder = None
        self.headphones = False

    # ---------- Verbindung ----------
    def _call(self, fn):
        """Führt fn(client) aus, verbindet bei Bedarf neu."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if not self._connected:
                        self._c.connect(self.host, self.port)
                        self._connected = True
                    return fn(self._c)
                except (mpd.ConnectionError, OSError) as e:
                    self._connected = False
                    try:
                        self._c.disconnect()
                    except Exception:
                        pass
                    if attempt == 2:
                        raise ConnectionError(f"MPD nicht erreichbar: {e}") from e

    def start(self):
        threading.Thread(target=self._poll_loop, daemon=True, name="mpd-poll").start()

    def close(self):
        self._stop.set()
        self._save_resume(force=True)

    # ---------- Status ----------
    def _folder_of(self, file):
        if not file or "://" in file:
            return None
        return file.rsplit("/", 1)[0] if "/" in file else ""

    def refresh(self):
        try:
            st, song, outs = self._call(lambda c: (c.status(), c.currentsong(), c.outputs()))
        except (ConnectionError, mpd.MPDError) as e:
            new = {"state": "offline", "volume": 0, "error": str(e)}
            self._publish(new)
            return new
        file = song.get("file", "")
        folder = self._folder_of(file)
        if self.current_folder and file.startswith(self.current_folder + "/"):
            folder = self.current_folder
        elapsed = float(st.get("elapsed", 0) or 0)
        duration = float(st.get("duration", song.get("duration", 0)) or 0)
        title = song.get("title") or (song.get("name") if "://" in file else None)
        if not title and file:
            title = file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        stream = any(o.get("outputname") == STREAM_OUTPUT and o.get("outputenabled") == "1" for o in outs)
        new = {
            "state": st.get("state", "stop"),
            "volume": int(st.get("volume", 0) or 0),
            "title": title or "",
            "artist": song.get("artist", ""),
            "album": song.get("album", ""),
            "file": file,
            "folder": folder,
            "url": file if "://" in file else None,
            "track": int(st.get("song", 0)) + 1 if "song" in st else 0,
            "tracks": int(st.get("playlistlength", 0)),
            "elapsed": round(elapsed, 1),
            "duration": round(duration, 1),
            "random": st.get("random") == "1",
            "stream": stream,
            "headphones": self.headphones,
        }
        self._track_resume(new)
        self._publish(new)
        return new

    def _publish(self, new):
        self.state = new
        cmp = {k: v for k, v in new.items() if k != "elapsed"}
        if cmp != self._last_cmp:
            self._last_cmp = cmp
            try:
                self.on_change(new)
            except Exception:
                log.exception("on_change")

    def _poll_loop(self):
        while not self._stop.wait(1.0):
            try:
                self.refresh()
            except Exception:
                log.exception("Statusabfrage")

    # ---------- Fortsetzen ----------
    def _track_resume(self, s):
        prev, self._prev = self._prev, s
        folder = s.get("folder")
        if not self.store.config["audio"]["resume"]:
            return
        active = (s["state"] in ("play", "pause") and folder is not None and not s.get("random")
                  and self.resumable(folder))
        if active and (s["tracks"] > 1 or s["duration"] > 600):
            self.store.resume[folder] = {"pos": s["track"] - 1, "elapsed": int(s["elapsed"])}
            if s["state"] == "pause" and prev.get("state") == "play":
                self._save_resume(force=True)
            else:
                self._save_resume()
        elif s["state"] == "stop" and prev.get("state") == "play":
            # Ordner zu Ende gespielt -> beim nächsten Mal von vorn
            if not self._user_stop and prev.get("folder") is not None:
                self.store.resume.pop(prev["folder"], None)
            self._user_stop = False
            self._save_resume(force=True)

    def _save_resume(self, force=False):
        now = time.monotonic()
        if force or now - self._last_resume_write > 60:
            self._last_resume_write = now
            try:
                self.store.save_resume()
            except OSError:
                log.exception("Fortsetzungsdaten")

    # ---------- Befehle ----------
    def play_folder(self, folder, shuffle=False, restart=False):
        cfg = self.store.config["audio"]
        self._save_resume(force=True)
        if restart or shuffle or not cfg["resume"] or not self.resumable(folder):
            r = None
        else:
            r = self.store.resume.get(folder)
        files = self.files_for(folder) if self.files_for else None

        def go(c):
            c.clear()
            c.random(1 if shuffle else 0)
            added = False
            if files:
                try:  # einzeln hinzufügen: MPD sortiert sonst „Folge 10“ vor „Folge 2“
                    c.command_list_ok_begin()
                    for f in files:
                        c.add(f)
                    c.command_list_end()
                    added = True
                except mpd.CommandError:
                    c.clear()
            if not added:
                c.add(folder)
            n = int(c.status().get("playlistlength", 0))
            if n == 0:
                raise ValueError(f"Ordner „{folder}“ enthält keine Titel")
            if r and 0 <= r.get("pos", 0) < n:
                c.play(r["pos"])
                if r.get("elapsed", 0) > 5:
                    try:
                        c.seekcur(max(0, r["elapsed"] - 3))
                    except mpd.CommandError:
                        pass
            else:
                c.play(0)

        self._call(go)
        self.current_folder = folder
        self.refresh()

    def play_url(self, url):
        def go(c):
            c.clear()
            c.random(0)
            c.add(url)
            c.play(0)

        self._call(go)
        self.refresh()

    def _safe(self, fn):
        try:
            self._call(fn)
        except mpd.CommandError as e:
            log.info("MPD: %s", e)
        self.refresh()

    def play_pause(self):
        st = self.state.get("state")
        if st == "play":
            self._safe(lambda c: c.pause(1))
        elif st == "pause":
            self._safe(lambda c: c.pause(0))
        else:
            self._safe(lambda c: c.play())

    def play(self):
        self._safe(lambda c: c.pause(0) if self.state.get("state") == "pause" else c.play())

    def pause(self):
        if self.state.get("state") == "play":
            self._safe(lambda c: c.pause(1))

    def stop(self):
        self._user_stop = True
        self._save_resume(force=True)
        self._safe(lambda c: c.stop())

    def next(self):
        self._safe(lambda c: c.next())

    def prev(self):
        if self.state.get("elapsed", 0) > 4 or self.state.get("track", 1) <= 1:
            self._safe(lambda c: c.seekcur(0))
        else:
            self._safe(lambda c: c.previous())

    def seek(self, seconds):
        self._safe(lambda c: c.seekcur(max(0, float(seconds))))

    def jump(self, pos):
        self._safe(lambda c: c.play(int(pos)))

    def vmax(self):
        if self.headphones:
            return int(self.store.config["bluetooth"]["volume_max"])
        return int(self.store.config["audio"]["volume_max"])

    def set_headphones(self, on):
        """Ausgabe umschalten. Beim Trennen wird pausiert, damit es nicht
        plötzlich laut aus dem Lautsprecher kommt."""
        self.headphones = bool(on)

        def go(c):
            if not on and c.status().get("state") == "play":
                c.pause(1)
            for o in c.outputs():
                name = o.get("outputname")
                if name == HEADPHONE_OUTPUT:
                    (c.enableoutput if on else c.disableoutput)(o["outputid"])
                elif name == SPEAKER_OUTPUT:
                    (c.disableoutput if on else c.enableoutput)(o["outputid"])

        self._safe(go)
        if on and self.state.get("volume", 0) > self.vmax():
            self.set_volume(self.vmax())

    def set_volume(self, vol):
        vmax = self.vmax()
        vol = max(0, min(vmax, int(vol)))
        self._safe(lambda c: c.setvol(vol))

    def volume_step(self, direction):
        step = int(self.store.config["audio"]["volume_step"])
        self.set_volume(self.state.get("volume", 0) + direction * step)

    def set_stream(self, on):
        def go(c):
            for o in c.outputs():
                if o.get("outputname") == STREAM_OUTPUT:
                    (c.enableoutput if on else c.disableoutput)(o["outputid"])

        self._safe(go)

    def queue(self):
        try:
            items = self._call(lambda c: c.playlistinfo())
        except (ConnectionError, mpd.MPDError):
            return []
        out = []
        for it in items:
            f = it.get("file", "")
            out.append({
                "pos": int(it.get("pos", 0)),
                "title": it.get("title") or f.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                "duration": round(float(it.get("duration", it.get("time", 0)) or 0)),
            })
        return out

    def update_db(self, wait=True):
        try:
            self._call(lambda c: c.update())
            if wait:
                for _ in range(120):
                    if "updating_db" not in self._call(lambda c: c.status()):
                        break
                    time.sleep(0.25)
        except (ConnectionError, mpd.MPDError) as e:
            log.warning("Datenbank-Update: %s", e)
