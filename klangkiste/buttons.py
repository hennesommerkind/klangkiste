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
        self._enc = None
        self._enc_lock = threading.Lock()

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
        from gpiozero import Button, InputDevice

        clk, dt, sw = int(enc["clk"]), int(enc["dt"]), int(enc["sw"])
        if {clk, dt, sw} & used:
            self.errors["encoder"] = "Ein Anschluss des Drehreglers ist schon mit einem Knopf belegt"
            return
        try:
            # Eigene Auswertung statt gpiozero.RotaryEncoder: Der verlangt alle vier Flanken in
            # exakter Reihenfolge und liegt bei prellenden Kontakten oder verpassten Flanken daneben.
            sign = -1 if enc.get("reverse") else 1
            a = InputDevice(clk, pull_up=True)
            b = InputDevice(dt, pull_up=True)
            self._devices += [a, b]
            self._enc = {"a": int(bool(a.pin.state)), "b": int(bool(b.pin.state)), "seq": [], "sign": sign, "logged": 0}
            # Die Pins merken sich Rückrufe nur schwach – eigene Verweise halten sie am Leben.
            self._enc_cbs = [lambda ticks, state: self._edge("a", state),
                             lambda ticks, state: self._edge("b", state)]
            for dev, cb in zip((a, b), self._enc_cbs):
                dev.pin.edges = "both"
                dev.pin.when_changed = cb
            press = enc.get("press")
            if press in BUTTON_ACTIONS:
                btn = Button(sw, pull_up=True, bounce_time=0.05)
                btn.when_pressed = lambda a=press: self.dispatch(a)
                self._devices.append(btn)
        except Exception as e:
            self.errors["encoder"] = str(e)
            log.warning("Drehregler: %s", e)

    def _edge(self, key, state):
        """Ruhelage an jeder Raste: beide Leitungen 1. Die Richtung ergibt sich daraus, welche
        Leitung zuerst auf 0 fällt; gezählt wird nur, wenn dazwischen beide auf 0 waren (sonst
        war es Prellen). Die Reihenfolge beim Zurückkehren ist bei günstigen Reglern unzuverlässig."""
        with self._enc_lock:
            e = self._enc
            old = (e["a"], e["b"])
            e[key] = 1 if state else 0
            new = (e["a"], e["b"])
            if new == old:
                return
            e["seq"].append(f"{new[0]}{new[1]}")
            if new != (1, 1):
                return
            seq, e["seq"] = e["seq"], []
            step = 0
            if "00" in seq:
                step = 1 if seq[0] == "01" else -1 if seq[0] == "10" else 0
            if e["logged"] < 60:   # die ersten Rasten mitschreiben – hilft bei der Fehlersuche
                e["logged"] += 1
                log.info("Drehregler: 11>%s → %s", ">".join(seq),
                         "nichts" if not step else "lauter" if step * e["sign"] > 0 else "leiser")
        if step:
            self._turn(step * e["sign"])

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
