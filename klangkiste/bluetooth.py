"""Bluetooth-Kopfhörer über BlueZ (bluetoothctl) und bluez-alsa.

Die Klangkiste merkt sich einen Kopfhörer. Ist er verbunden, läuft der Ton
nur dort; wird er getrennt, pausiert die Wiedergabe. Ist er gekoppelt, aber
nicht verbunden, versucht die Box regelmäßig, ihn zu erreichen – so verbindet
er sich kurz nach dem Einschalten von selbst.
"""
import asyncio
import json
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
    # Jeder Verbindungsversuch belegt mehrere Sekunden lang die Antenne, die sich Bluetooth
    # und WLAN beim Pi teilen – das WLAN hakt so lange. Deshalb nur selten selbst suchen;
    # ein eingeschalteter, gekoppelter Kopfhörer meldet sich ohnehin von sich aus.
    RECONNECT = 300   # Sekunden zwischen Verbindungsversuchen

    def __init__(self, store, on_change):
        self.store = store
        self.on_change = on_change     # on_change(verbunden: bool, name: str)
        self.available = bool(shutil.which("bluetoothctl"))
        self.connected = False
        self.name = ""
        self.busy = False              # während Suchen/Koppeln keine Hintergrundversuche
        self._scan_mode = "bredr"      # nur klassisches Bluetooth; ältere BlueZ kennen nur „on“
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
        if "Waiting to connect to bluetoothd" in text:
            raise BluetoothError("Keine Verbindung zum Bluetooth-Dienst (Berechtigung) – Box einmal neu starten")
        return proc.returncode, text

    async def _objects(self, only=None):
        """Alle bekannten Geräte in einem Aufruf (D-Bus ObjectManager).
        Mit only=MAC nur die Eigenschaften dieses einen Geräts (viel kleiner)."""
        if only:
            path = "/org/bluez/hci0/dev_" + only.replace(":", "_")
            cmd = ["busctl", "--system", "--json=short", "call", "org.bluez", path,
                   "org.freedesktop.DBus.Properties", "GetAll", "s", "org.bluez.Device1"]
        else:
            cmd = ["busctl", "--system", "--json=short", "call", "org.bluez", "/",
                   "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 8)
        except asyncio.TimeoutError:
            proc.kill()
            raise BluetoothError("Bluetooth antwortet nicht")
        if proc.returncode != 0 and only and ("UnknownObject" in err.decode("utf-8", "replace")
                                                or "json" in err.decode("utf-8", "replace")):
            return {}  # Gerät nicht bekannt oder Eigenschaften nicht lesbar
        if proc.returncode != 0:
            msg = err.decode("utf-8", "replace").strip()
            if "Access denied" in msg or "not allowed" in msg:
                msg = "keine Berechtigung (Box einmal neu starten)"
            raise BluetoothError(f"Bluetooth-Dienst nicht erreichbar: {msg[-160:]}")
        if only and proc.returncode != 0:
            return {}  # Gerät (noch) nicht bekannt
        data = json.loads(out)["data"][0]
        if only:
            data = {"x": {"org.bluez.Device1": data}}
        devs = {}
        for ifaces in data.values():
            raw = ifaces.get("org.bluez.Device1")
            if not raw:
                continue
            v = {k: x.get("data") for k, x in raw.items()}
            mac = str(v.get("Address", "")).upper()
            uuids = [str(u).lower() for u in v.get("UUIDs") or []]
            cls = int(v.get("Class") or 0)
            audio = (str(v.get("Icon", "")).startswith("audio") or ((cls >> 8) & 0x1F) == 4
                     or any(u.startswith(("0000110b", "0000110d")) for u in uuids))
            devs[mac] = {
                "mac": mac, "name": v.get("Alias") or v.get("Name") or mac, "has_name": "Name" in v,
                "paired": bool(v.get("Paired")), "trusted": bool(v.get("Trusted")),
                "connected": bool(v.get("Connected")), "audio": audio,
                "seen": v.get("RSSI") is not None, "icon": v.get("Icon", ""),
            }
        return devs

    async def info(self, mac):
        d = (await self._objects(mac)).get(mac)
        if not d:
            raise BluetoothError("Gerät nicht gefunden")
        return d

    def _scan_proc(self, seconds):
        """Startet eine Suche nach klassischem Bluetooth (Kopfhörer) im Hintergrund."""
        return asyncio.create_subprocess_exec(
            "bluetoothctl", "--timeout", str(seconds), "scan", self._scan_mode,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL)

    async def _stop(self, proc):
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                proc.kill()

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

    async def _watch_scan(self, seconds, until_mac=None):
        """Sucht und liest dabei mit, welche Geräte auftauchen.
        Liefert {MAC: Name}; mit until_mac endet die Suche, sobald dieses Gerät gesehen wurde."""
        seen = {}
        for mode in (self._scan_mode, "on"):
            proc = await self._scan_proc(seconds + 2)
            invalid = False

            async def reader():
                nonlocal invalid
                async for raw in proc.stdout:
                    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x01|\x02", "", raw.decode("utf-8", "replace"))
                    if "nvalid" in line and "scan" in line.lower():
                        invalid = True
                    m = re.search(r"\[(NEW|CHG)\] Device ([0-9A-F:]{17})\s*(.*)", line)
                    if m:
                        mac, rest = m.group(2), m.group(3).strip()
                        if m.group(1) == "NEW" and rest and not rest.replace("-", ":") == mac:
                            seen[mac] = rest
                        else:
                            seen.setdefault(mac, "")
                        if until_mac and mac == until_mac:
                            return

            task = asyncio.create_task(reader())
            try:
                await asyncio.wait_for(asyncio.shield(task), seconds)
            except asyncio.TimeoutError:
                pass
            await self._stop(proc)
            task.cancel()
            if invalid and mode != "on":
                self._scan_mode = "on"  # ältere BlueZ kennen „scan bredr“ nicht
                continue
            break
        return seen

    async def _names(self):
        """Namen aller bekannten Geräte (ein schneller Aufruf)."""
        try:
            _, out = await self._ctl("devices", timeout=5)
        except BluetoothError:
            return {}
        return {m.group(1): m.group(2).strip()
                for m in re.finditer(r"Device ([0-9A-F:]{17}) (.+)", out)}

    async def scan(self, seconds=12):
        self.busy = True
        try:
            await self.power_on()
            seen = await self._watch_scan(seconds)
            names = await self._names()
            found = []
            for mac, name in list(seen.items())[:30]:
                name = name or names.get(mac, "")
                if not name or name.replace("-", ":") == mac:
                    continue  # namenlose Geräte (meist Telefone, Fernseher im Hintergrund) ausblenden
                try:
                    d = await self.info(mac)
                    d["name"] = d["name"] if d["name"] != mac else name
                except BluetoothError:
                    d = {"mac": mac, "name": name, "paired": False, "connected": False, "audio": False}
                found.append(d)
            found.sort(key=lambda d: (not d["audio"], not d["paired"], d["name"].lower()))
            return found
        finally:
            self.busy = False

    async def pair(self, mac):
        mac = _clean_mac(mac)
        self.busy = True
        try:
            try:
                dev = await self.info(mac)
            except BluetoothError:
                # BlueZ vergisst gefundene Geräte nach etwa 30 s – dann kurz neu suchen
                seen = await self._watch_scan(20, until_mac=mac)
                if mac not in seen:
                    raise BluetoothError("Kopfhörer nicht mehr gefunden – bitte wieder in den Kopplungsmodus versetzen")
                dev = await self.info(mac)
            if not dev["paired"]:
                code, out = await self._ctl("pair", mac, timeout=25, agent=True)
                if "Pairing successful" not in out and "AlreadyExists" not in out:
                    err = re.search(r"Failed to pair: (\S+)", out)
                    raise BluetoothError("Koppeln fehlgeschlagen" + (f" ({err.group(1)})" if err else "")
                                         + " – ist der Kopfhörer im Kopplungsmodus?")
            await self._ctl("trust", mac, timeout=5)
            d = await self.info(mac)
            self.store.update_config({"bluetooth": {"device": mac, "name": d["name"]}})
            try:
                await self._connect(mac)
            except BluetoothError:
                pass  # verbindet sich meist kurz danach von selbst
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
        if not mac and not self.connected:
            return  # kein Kopfhörer gemerkt: nichts abzufragen
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
