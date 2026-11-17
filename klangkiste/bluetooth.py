"""Bluetooth-Kopfhörer über BlueZ (bluetoothctl) und bluez-alsa.

Die Klangkiste merkt sich einen Kopfhörer. Ist er verbunden, läuft der Ton
nur dort; wird er getrennt, pausiert die Wiedergabe. Ist er gekoppelt, aber
nicht verbunden, versucht die Box regelmäßig, ihn zu erreichen – so verbindet
er sich kurz nach dem Einschalten von selbst.
"""
import asyncio
import logging
import re
import shutil

log = logging.getLogger("bluetooth")
MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


class BluetoothError(RuntimeError):
    pass


def _clean_mac(mac):
    mac = str(mac or "").strip().upper()
    if not MAC_RE.match(mac):
        raise BluetoothError("Ungültige Geräteadresse")
    return mac


class Bluetooth:
    POLL = 3          # Sekunden zwischen Verbindungsprüfungen
    RECONNECT = 15    # Sekunden zwischen Verbindungsversuchen

    def __init__(self, store, on_change):
        self.store = store
        self.on_change = on_change     # on_change(verbunden: bool, name: str)
        self.available = bool(shutil.which("bluetoothctl"))
        self.connected = False
        self.name = ""
        self.busy = False              # während Suchen/Koppeln keine Hintergrundversuche
        self._task = None

    @property
    def cfg(self):
        return self.store.config["bluetooth"]

    # ---------- bluetoothctl ----------
    async def _ctl(self, *args, timeout=15, agent=False):
        if not self.available:
            raise BluetoothError("Bluetooth ist auf dieser Box nicht eingerichtet")
        cmd = ["bluetoothctl"]
        if agent:
            cmd += ["--agent", "NoInputNoOutput"]
        cmd += ["--timeout", str(timeout), *args]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT,
                                                    stdin=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout + 5)
        except asyncio.TimeoutError:
            proc.kill()
            raise BluetoothError("Bluetooth antwortet nicht")
        text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x01|\x02", "", out.decode("utf-8", "replace"))
        return proc.returncode, text

    async def info(self, mac):
        code, out = await self._ctl("info", mac, timeout=5)
        d = {"mac": mac}
        for line in out.splitlines():
            m = re.match(r"\s*(Name|Alias|Icon|Paired|Trusted|Connected|Bonded):\s*(.*)", line)
            if m:
                d[m.group(1).lower()] = m.group(2).strip()
        d["audio"] = d.get("icon", "").startswith("audio") or "Audio Sink" in out
        for k in ("paired", "trusted", "connected", "bonded"):
            d[k] = d.get(k) == "yes"
        d["name"] = d.get("alias") or d.get("name") or mac
        return d

    async def _device_macs(self, which=None):
        args = ["devices"] + ([which] if which else [])
        _, out = await self._ctl(*args, timeout=5)
        return [m.group(1) for m in re.finditer(r"Device ([0-9A-F:]{17})", out)]

    async def power_on(self):
        try:
            await self._ctl("power", "on", timeout=5)
        except BluetoothError as e:
            log.warning("Bluetooth einschalten: %s", e)

    # ---------- für die Oberfläche ----------
    async def status(self):
        dev = None
        mac = self.cfg.get("device")
        if mac and self.available:
            try:
                dev = await self.info(mac)
            except BluetoothError:
                dev = {"mac": mac, "name": self.cfg.get("name") or mac, "connected": False}
        return {"available": self.available, "device": dev, "connected": self.connected,
                "volume_max": self.cfg["volume_max"]}

    async def scan(self, seconds=12):
        self.busy = True
        try:
            await self.power_on()
            await self._ctl("scan", "on", timeout=seconds)
            out = []
            for mac in await self._device_macs():
                try:
                    d = await self.info(mac)
                except BluetoothError:
                    continue
                if d["name"] == mac.replace(":", "-") or d["name"] == mac:
                    continue  # namenlose Geräte (meist Telefone im Hintergrund) ausblenden
                out.append(d)
            out.sort(key=lambda d: (not d["audio"], not d["paired"], d["name"].lower()))
            return out
        finally:
            self.busy = False

    async def pair(self, mac):
        mac = _clean_mac(mac)
        self.busy = True
        try:
            d = await self.info(mac)
            if not d["paired"]:
                code, out = await self._ctl("pair", mac, timeout=25, agent=True)
                if "Pairing successful" not in out and "AlreadyExists" not in out:
                    err = re.search(r"Failed to pair: (\S+)", out)
                    raise BluetoothError("Koppeln fehlgeschlagen" + (f" ({err.group(1)})" if err else "")
                                         + " – ist der Kopfhörer im Kopplungsmodus?")
            await self._ctl("trust", mac, timeout=5)
            d = await self.info(mac)
            self.store.update_config({"bluetooth": {"device": mac, "name": d["name"]}})
            await self._connect(mac)
            return d
        finally:
            self.busy = False

    async def _connect(self, mac):
        code, out = await self._ctl("connect", mac, timeout=12)
        if "Connection successful" not in out and code != 0:
            raise BluetoothError("Verbinden fehlgeschlagen – ist der Kopfhörer eingeschaltet und in der Nähe?")
        await self._check()

    async def connect(self):
        mac = self.cfg.get("device")
        if not mac:
            raise BluetoothError("Noch kein Kopfhörer gekoppelt")
        self.busy = True
        try:
            await self.power_on()
            await self._connect(mac)
        finally:
            self.busy = False

    async def disconnect(self):
        mac = self.cfg.get("device")
        if mac:
            await self._ctl("disconnect", mac, timeout=8)
            await self._check()

    async def forget(self):
        mac = self.cfg.get("device")
        if mac:
            try:
                await self._ctl("remove", mac, timeout=8)
            except BluetoothError:
                pass
        self.store.update_config({"bluetooth": {"device": "", "name": ""}})
        await self._check()

    # ---------- Hintergrund ----------
    def start(self):
        if self.available:
            self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _check(self):
        mac = self.cfg.get("device")
        connected, name = False, ""
        if mac:
            try:
                d = await self.info(mac)
                connected, name = d["connected"], d["name"]
            except BluetoothError:
                pass
        if connected != self.connected:
            name = name or self.name or self.cfg.get("name", "")
            self.connected, self.name = connected, name
            log.info("Kopfhörer %s %s", name or mac, "verbunden" if connected else "getrennt")
            try:
                self.on_change(connected, name)
            except Exception:
                log.exception("Kopfhörer-Wechsel")

    async def _loop(self):
        await self.power_on()
        since_try = self.RECONNECT
        while True:
            try:
                if not self.busy:
                    await self._check()
                    since_try += self.POLL
                    mac = self.cfg.get("device")
                    if mac and not self.connected and self.cfg.get("auto_connect", True) and since_try >= self.RECONNECT:
                        since_try = 0
                        await self._ctl("connect", mac, timeout=8)
                        await self._check()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("Bluetooth-Prüfung: %s", e)
            await asyncio.sleep(self.POLL)
