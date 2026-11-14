"""Einstellungen und Datenablage (JSON-Dateien, atomar geschrieben)."""
import copy
import json
import os
import tempfile
import threading
from pathlib import Path

DEFAULTS = {
    "name": "Klangkiste",
    "audio": {
        "volume_start": 40,
        "volume_max": 80,
        "volume_step": 5,
        "resume": True,          # Hörspiele an der letzten Stelle fortsetzen
        "pause_on_remove": False,  # Karte wegnehmen = Pause
        "stream_port": 8000,
    },
    "buttons": [
        {"pin": 27, "action": "prev"},
        {"pin": 12, "action": "play_pause"},
        {"pin": 5, "action": "next"},
    ],
    "rfid": {"enabled": True, "rst_pin": 25, "spi_bus": 0, "spi_device": 0},
    "mqtt": {
        "enabled": False,
        "host": "",
        "port": 1883,
        "username": "",
        "password": "",
        "base_topic": "klangkiste",
        "discovery": True,
    },
    "backup": {
        "url": "https://webdav.hidrive.ionos.com/",
        "username": "",
        "password": "",
        "remote_path": "Klangkiste",
        "include_music": True,
        "schedule": "off",   # off | daily | weekly
        "hour": 3,
        "weekday": 6,        # 0 = Montag
        "keep": 10,
    },
}

BUTTON_ACTIONS = {
    "play_pause": "Play / Pause",
    "next": "Nächster Titel",
    "prev": "Vorheriger Titel",
    "vol_up": "Lauter",
    "vol_down": "Leiser",
    "stop": "Stopp",
    "shutdown": "Ausschalten",
}

# Pins, die frei für Knöpfe sind (BCM). Nicht dabei: SPI (7-11), Leser-Reset,
# I2S der Soundkarte (18-21), I2C (2, 3), UART (14, 15).
BUTTON_PINS = [4, 5, 6, 12, 13, 16, 17, 22, 23, 24, 26, 27]


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Store:
    """Hält Einstellungen, Karten und Fortsetzungs-Positionen."""

    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.music = self.dir / "music"
        self.music.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self.config = _merge(DEFAULTS, self._read("config.json", {}))
        self.cards = self._read("cards.json", {})
        self.resume = self._read("resume.json", {})

    def _read(self, name, default):
        try:
            return json.loads((self.dir / name).read_text("utf-8"))
        except (OSError, ValueError):
            return default

    def _write(self, name, data):
        with self._lock:
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=name + ".")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.dir / name)

    def save_config(self):
        self._write("config.json", self.config)

    def save_cards(self):
        self._write("cards.json", self.cards)

    def save_resume(self):
        self._write("resume.json", self.resume)

    def update_config(self, patch):
        with self._lock:
            self.config = _merge(self.config, patch)
            self.save_config()

    def reload(self):
        with self._lock:
            self.config = _merge(DEFAULTS, self._read("config.json", {}))
            self.cards = self._read("cards.json", {})
            self.resume = self._read("resume.json", {})

    def public_config(self):
        """Einstellungen ohne Passwörter (für die Oberfläche)."""
        c = copy.deepcopy(self.config)
        for sec in ("mqtt", "backup"):
            c[sec]["password_set"] = bool(c[sec].get("password"))
            c[sec]["password"] = ""
        return c
