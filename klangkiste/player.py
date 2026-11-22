import re
"""Wiedergabe über MPD (Music Player Daemon)."""
import logging
import threading
import time

import mpd

log = logging.getLogger("player")
STREAM_OUTPUT = "Hausstream"
SPEAKER_OUTPUT = "Lautsprecher"
HEADPHONE_OUTPUT = "Kopfhörer"



_DUMMY_TITLE = re.compile(r"^(untitled|unknown|unbekannt|ohne titel|no title|(track|titel|title|audio ?track)\s*\d*)$", re.I)


def _real_title(t):
    """Platzhalter-Titel aus den Tags („Untitled“, „Track 03“) wie fehlende behandeln – dann zählt der Dateiname."""
    t = (t[0] if isinstance(t, list) and t else t) or ""
    t = str(t).strip()
    return "" if not t or _DUMMY_TITLE.match(t) else t

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
        self._queue_files = None   # Reihenfolge der Warteschlange, wenn bekannt (für Folgen-Sprünge)
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
        title = _real_title(song.get("title")) or (song.get("name") if "://" in file else None)
        if not title and file:
            title = file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        stream = any(o.get("outputname") == STREAM_OUTPUT and o.get("outputenabled") == "1" for o in outs)
        ep, eps = self._episode_info(file)
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
            "episode": ep, "episodes": eps,
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
    # ---------- Serien: Vor/Zurück springen ganze Folgen ----------
    def _episode_of(self, file):
        """Folge (Unterordner des gespielten Ordners), zu der eine Datei gehört – sonst None."""
        base = self.current_folder
        if not base or not file or not file.startswith(base + "/"):
            return None
        rest = file[len(base) + 1:]
        return rest.split("/", 1)[0] if "/" in rest else None

    def _episode_info(self, file):
        """(Nummer der Folge, Anzahl Folgen) für die Anzeige, wenn eine Serie läuft."""
        files = self._queue_files
        ep = self._episode_of(file)
        if not files or ep is None:
            return 0, 0
        order = list(dict.fromkeys(self._episode_of(f) for f in files))
        if len(order) < 2 or None in order or ep not in order:
            return 0, 0
        return order.index(ep) + 1, len(order)

    def _episode_jump(self, c, direction):
        """Springt zur ersten Datei der nächsten/vorigen Folge. False, wenn keine Serie läuft."""
        st = c.status()
        if st.get("random") == "1" or "song" not in st:
            return False
        files = self._queue_files
        if not files or len(files) != int(st.get("playlistlength", 0)):
            files = [it.get("file", "") for it in c.playlistinfo()]
        eps = [self._episode_of(f) for f in files]
        if None in eps or len(set(eps)) < 2:
            return False
        pos = int(st["song"])
        start = pos
        while start > 0 and eps[start - 1] == eps[pos]:
            start -= 1
        if direction > 0:
            nxt = next((j for j in range(pos + 1, len(eps)) if eps[j] != eps[pos]), None)
            if nxt is None:
                log.info("Letzte Folge – kein Sprung nach vorn")
                return True
            c.play(nxt)
        else:
            if start == 0:
                c.play(0)          # erste Folge: es gibt keine davor – an ihren Anfang
                return True
            p = start - 1
            while p > 0 and eps[p - 1] == eps[start - 1]:
                p -= 1
            c.play(p)
        return True

    def play_folder(self, folder, shuffle=False, restart=False):
        cfg = self.store.config["audio"]
        self._save_resume(force=True)
        if restart or shuffle or not cfg["resume"] or not self.resumable(folder):
            r = None
        else:
            r = self.store.resume.get(folder)
        files = self.files_for(folder) if self.files_for else None

        exact = {"ok": False}

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
            exact["ok"] = added
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
        # Nur wenn einzeln hinzugefügt, stimmt die Liste mit der Warteschlange überein
        self._queue_files = list(files) if files and exact["ok"] else None
        self.refresh()

    def play_episode(self, folder):
        """Eine Folge antippen (Display): die Serie bleibt die Abspielliste, gesprungen wird an den
        Anfang dieser Folge – so laufen danach die nächsten Folgen weiter und Vor/Zurück springen folgenweise.
        Ordner direkt unter Hörspiele/Musik werden normal gestartet (mit Fortsetzen)."""
        folder = str(folder).strip("/")
        parts = folder.split("/")
        if len(parts) < 3:
            return self.play_folder(folder)
        series = "/".join(parts[:-1])
        if self.current_folder != series or not self._queue_files:
            self.play_folder(series, restart=True)

        def go(c):
            files = self._queue_files or [it.get("file", "") for it in c.playlistinfo()]
            i = next((j for j, f in enumerate(files) if f.startswith(folder + "/")), None)
            if i is None:
                raise ValueError(f"Folge „{parts[-1]}“ nicht gefunden")
            c.random(0)
            c.play(i)

        self._call(go)
        self.refresh()

    def play_url(self, url):
        self.current_folder = None
        self._queue_files = None

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
        # Läuft eine Serie (Ordner mit Folgen-Unterordnern), geht es zur nächsten Folge,
        # nicht zum nächsten Kapitel; sonst zum nächsten Titel.
        self._safe(lambda c: self._episode_jump(c, +1) or c.next())

    def prev(self):
        # Serie: immer eine Folge zurück (nicht erst an den Anfang der laufenden).
        def go(c):
            if self._episode_jump(c, -1):
                return
            if self.state.get("elapsed", 0) > 4 or self.state.get("track", 1) <= 1:
                c.seekcur(0)
            else:
                c.previous()
        self._safe(go)

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
                "title": _real_title(it.get("title")) or f.rsplit("/", 1)[-1].rsplit(".", 1)[0],
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
