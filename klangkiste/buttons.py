"""Knöpfe an GPIO-Pins (gpiozero) inklusive Anlernmodus."""
import logging
import threading

from .config import BUTTON_ACTIONS, BUTTON_PINS

log = logging.getLogger("buttons")


class Buttons:
    def __init__(self, store, dispatch):
        self.store = store
        self.dispatch = dispatch  # wird aus einem Hintergrund-Thread aufgerufen
        self._devices = []
        self._lock = threading.Lock()
        self.errors = {}

    def _close(self):
        for d in self._devices:
            try:
                d.close()
            except Exception:
                pass
        self._devices = []

    def apply(self):
        """Richtet die Knöpfe gemäß Einstellungen (neu) ein."""
        from gpiozero import Button

        with self._lock:
            self._close()
            self.errors = {}
            seen = set()
            for b in self.store.config["buttons"]:
                pin, action = int(b["pin"]), b.get("action")
                if action not in BUTTON_ACTIONS or pin in seen:
                    continue
                seen.add(pin)
                try:
                    hold = 2.0 if action == "shutdown" else 0.4
                    dev = Button(pin, pull_up=True, bounce_time=0.05, hold_time=hold,
                                 hold_repeat=action in ("vol_up", "vol_down"))
                    if action == "shutdown":
                        dev.when_held = lambda a=action: self.dispatch(a)
                    else:
                        dev.when_pressed = lambda a=action: self.dispatch(a)
                        if action in ("vol_up", "vol_down"):
                            dev.when_held = lambda a=action: self.dispatch(a)
                    self._devices.append(dev)
                except Exception as e:
                    self.errors[pin] = str(e)
                    log.warning("Knopf an GPIO %s: %s", pin, e)

    def learn(self, timeout=15):
        """Wartet auf einen Tastendruck an einem freien Pin und gibt ihn zurück."""
        from gpiozero import Button

        with self._lock:
            self._close()
            found = []
            done = threading.Event()
            devs = []
            try:
                for pin in BUTTON_PINS:
                    try:
                        d = Button(pin, pull_up=True, bounce_time=0.05)
                    except Exception:
                        continue
                    if d.is_pressed:  # dauerhaft auf Masse: kein Knopf
                        d.close()
                        continue

                    def hit(p=pin):
                        found.append(p)
                        done.set()

                    d.when_pressed = hit
                    devs.append(d)
                done.wait(timeout)
            finally:
                for d in devs:
                    d.close()
        self.apply()
        return found[0] if found else None

    def close(self):
        with self._lock:
            self._close()
