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
        self._turns = 0
        self._turn_lock = threading.Lock()
        self._turn_timer = None

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
                    hold = {"shutdown": 2.0, "play_pause": 3.0}.get(action, 0.4)
                    dev = Button(pin, pull_up=True, bounce_time=0.05, hold_time=hold,
                                 hold_repeat=action in ("vol_up", "vol_down"))
                    if action == "shutdown":
                        dev.when_held = lambda a=action: self.dispatch(a)
                    else:
                        if action == "play_pause":  # drei Sekunden halten: Adresse ansagen
                            dev.when_held = lambda: self.dispatch("announce")
                        dev.when_pressed = lambda a=action: self.dispatch(a)
                        if action in ("vol_up", "vol_down"):
                            dev.when_held = lambda a=action: self.dispatch(a)
                    self._devices.append(dev)
                except Exception as e:
                    self.errors[pin] = str(e)
                    log.warning("Knopf an GPIO %s: %s", pin, e)

            self._apply_encoder(seen)

    def _apply_encoder(self, used):
        """Drehregler: Drehen ändert die Lautstärke, Drücken löst eine Knopf-Funktion aus."""
        enc = self.store.config.get("encoder") or {}
        if not enc.get("enabled"):
            return
        from gpiozero import Button, RotaryEncoder

        clk, dt, sw = int(enc["clk"]), int(enc["dt"]), int(enc["sw"])
        if {clk, dt, sw} & used:
            self.errors["encoder"] = "Ein Anschluss des Drehreglers ist schon mit einem Knopf belegt"
            return
        try:
            rot = RotaryEncoder(clk, dt, max_steps=0)
            sign = -1 if enc.get("reverse") else 1
            rot.when_rotated_clockwise = lambda: self._turn(sign)
            rot.when_rotated_counter_clockwise = lambda: self._turn(-sign)
            self._devices.append(rot)
            press = enc.get("press")
            if press in BUTTON_ACTIONS:
                btn = Button(sw, pull_up=True, bounce_time=0.05)
                btn.when_pressed = lambda a=press: self.dispatch(a)
                self._devices.append(btn)
        except Exception as e:
            self.errors["encoder"] = str(e)
            log.warning("Drehregler: %s", e)

    def _turn(self, direction):
        # Schnelles Drehen bündeln: ein Befehl alle 60 ms statt einer pro Raste
        with self._turn_lock:
            self._turns += direction
            if self._turn_timer is None:
                self._turn_timer = threading.Timer(0.06, self._flush_turns)
                self._turn_timer.daemon = True
                self._turn_timer.start()

    def _flush_turns(self):
        with self._turn_lock:
            n, self._turns, self._turn_timer = self._turns, 0, None
        if n:
            step = int((self.store.config.get("encoder") or {}).get("step", 3))
            self.dispatch("vol_delta", n * step)

    def learn(self, timeout=15):
        """Wartet auf einen Tastendruck an einem freien Pin und gibt ihn zurück."""
        from gpiozero import Button

        with self._lock:
            self._close()
            found = []
            done = threading.Event()
            devs = []
            try:
                enc = self.store.config.get("encoder") or {}
                skip = {int(enc[k]) for k in ("clk", "dt", "sw")} if enc.get("enabled") else set()
                for pin in BUTTON_PINS:
                    if pin in skip:   # der Drehregler ist kein Knopf
                        continue
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
