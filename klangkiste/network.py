"""WLAN über NetworkManager (nmcli): Status, Netz wechseln, eigenes Not-WLAN.

Findet die Box nach dem Start kein bekanntes Netz, öffnet sie ein eigenes WLAN
(„Hotspot“). Darin ist sie unter 10.42.0.1 erreichbar, und man kann ihr ein
neues WLAN beibringen. Solange niemand die Oberfläche benutzt, schaut sie alle
paar Minuten nach, ob ein bekanntes Netz wieder erreichbar ist.
"""
import asyncio
import logging
import shutil
import time

log = logging.getLogger("network")
HOTSPOT = "klangkiste-hotspot"
HOTSPOT_IP = "10.42.0.1"


class NetworkError(RuntimeError):
    pass


def _split(line):
    """nmcli -t trennt mit „:“, maskiert aber „\\:“ in Werten."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


class Network:
    BOOT_GRACE = 60       # Sekunden ohne Netz (nach dem Start oder im Betrieb), bevor das Not-WLAN aufgeht
    CHECK = 15
    RETRY_IDLE = 300      # im Not-WLAN: so lange ohne Bedienung, dann bekannte Netze erneut versuchen

    def __init__(self, store, on_event, last_activity):
        self.store = store
        self.on_event = on_event            # on_event(art, info): „connected“, „hotspot“
        self.last_activity = last_activity  # Zeitpunkt der letzten Bedienung (monotonic)
        self.available = bool(shutil.which("nmcli"))
        self.hotspot = False
        self.connected = False
        self.ssid = ""
        self.cached_networks = []
        self._task = None
        self._lock = asyncio.Lock()

    @property
    def cfg(self):
        return self.store.config["wifi"]

    async def _nm(self, *args, timeout=20, check=True):
        if not self.available:
            raise NetworkError("NetworkManager ist auf dieser Box nicht vorhanden")
        proc = await asyncio.create_subprocess_exec(
            "nmcli", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise NetworkError("NetworkManager antwortet nicht")
        if check and proc.returncode != 0:
            msg = err.decode("utf-8", "replace").strip() or out.decode("utf-8", "replace").strip()
            if "Secrets were required" in msg or "802-1X" in msg or "password" in msg.lower():
                msg = "Das Kennwort stimmt nicht"
            elif "No network with SSID" in msg:
                msg = "Dieses WLAN ist gerade nicht zu finden"
            elif "Not authorized" in msg or "Insufficient privileges" in msg:
                msg = "Keine Berechtigung – bitte einmal das Update/install.sh ausführen"
            raise NetworkError(msg[-200:])
        return out.decode("utf-8", "replace")

    # ---------- Zustand ----------
    async def refresh(self):
        out = await self._nm("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", check=False)
        connected, ssid, hotspot = False, "", False
        for line in out.splitlines():
            dev, typ, state, con = (_split(line) + ["", "", "", ""])[:4]
            if not state.startswith("connected") or typ == "loopback":
                continue
            if con == HOTSPOT:
                hotspot = True
            elif typ in ("wifi", "ethernet"):
                connected = True
                if typ == "wifi":
                    ssid = con
                elif not ssid:
                    ssid = "Netzwerkkabel"
        self.connected, self.ssid, self.hotspot = connected, ssid, hotspot
        return connected

    async def status(self):
        from .system import ip_address
        if self.available:
            try:
                await self.refresh()
            except NetworkError:
                pass
        saved = []
        if self.available:
            try:
                out = await self._nm("-t", "-f", "NAME,TYPE", "connection", "show", check=False)
                saved = [n for n, t in (_split(l)[:2] for l in out.splitlines() if l)
                         if t == "802-11-wireless" and n != HOTSPOT]
            except NetworkError:
                pass
        c = self.cfg
        return {"available": self.available, "connected": self.connected, "ssid": self.ssid,
                "hotspot": self.hotspot, "ip": HOTSPOT_IP if self.hotspot else ip_address(),
                "saved": saved, "hotspot_enabled": c["hotspot"], "hotspot_ssid": c["hotspot_ssid"],
                "hotspot_password_set": bool(c["hotspot_password"])}

    async def scan(self):
        """Sichtbare WLANs. Im Not-WLAN kann der Pi nicht neu suchen – dann gilt die letzte Liste."""
        if self.hotspot:
            return self.cached_networks
        out = await self._nm("-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "device", "wifi", "list",
                             "--rescan", "yes", timeout=30, check=False)
        best = {}
        for line in out.splitlines():
            ssid, signal, sec, inuse = (_split(line) + ["", "0", "", ""])[:4]
            if not ssid:
                continue
            sig = int(signal or 0)
            if ssid not in best or sig > best[ssid]["signal"]:
                best[ssid] = {"ssid": ssid, "signal": sig, "secure": bool(sec and sec != "--"),
                              "active": inuse.strip() == "*"}
        self.cached_networks = sorted(best.values(), key=lambda n: -n["signal"])
        return self.cached_networks

    # ---------- Aktionen ----------
    async def connect(self, ssid, password):
        """Verbindet mit einem WLAN. Klappt es nicht, geht das Not-WLAN wieder auf."""
        async with self._lock:
            was_hotspot = self.hotspot
            if was_hotspot:
                await self._nm("connection", "down", HOTSPOT, check=False)
                await asyncio.sleep(3)
            args = ["--wait", "40", "device", "wifi", "connect", ssid]
            if password:
                args += ["password", password]
            try:
                await self._nm(*args, timeout=50)
            except NetworkError:
                await self.refresh()
                if was_hotspot and not self.connected:
                    await self._start_hotspot()
                raise
            await self.refresh()
            self.on_event("connected", self.ssid)

    async def forget(self, name):
        if name == HOTSPOT:
            raise NetworkError("Das eigene WLAN kann hier nicht gelöscht werden")
        await self._nm("connection", "delete", "id", name)

    async def _start_hotspot(self):
        c = self.cfg
        pw = c["hotspot_password"]
        if len(pw) < 8:
            raise NetworkError("Das Kennwort für das eigene WLAN braucht mindestens 8 Zeichen")
        await self._nm("connection", "delete", "id", HOTSPOT, check=False)
        await self._nm("connection", "add", "type", "wifi", "ifname", "wlan0", "con-name", HOTSPOT,
                       "autoconnect", "no", "ssid", c["hotspot_ssid"] or "Klangkiste",
                       "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg", "802-11-wireless.channel", "6",
                       "ipv4.method", "shared", "ipv6.method", "disabled",
                       "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp",
                       "wifi-sec.group", "ccmp", "wifi-sec.pmf", "disable", "wifi-sec.psk", pw)
        await self._nm("connection", "up", HOTSPOT, timeout=30)
        await self.refresh()
        log.info("Eigenes WLAN „%s“ geöffnet", c["hotspot_ssid"])
        self.on_event("hotspot", c["hotspot_ssid"])

    async def stop_hotspot(self):
        await self._nm("connection", "down", HOTSPOT, check=False)
        await self.refresh()

    # ---------- Hintergrund ----------
    def start(self):
        if self.available:
            self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        started = time.monotonic()
        lost_since = started   # seit wann ohne Netz (beim Start: seit dem Start)
        was_connected = None
        while True:
            try:
                async with self._lock:
                    await self.refresh()
                    if self.connected and was_connected is not True:
                        self.on_event("connected", self.ssid)
                    if not self.connected and was_connected:
                        lost_since = time.monotonic()  # z. B. Router startet neu: erst einmal abwarten
                    was_connected = self.connected
                    if not self.connected and not self.hotspot and self.cfg["hotspot"] \
                            and time.monotonic() - lost_since > self.BOOT_GRACE:
                        try:
                            await self.scan()  # Liste merken, solange der Pi noch suchen kann
                        except NetworkError:
                            pass
                        await self._start_hotspot()
                    elif self.hotspot and time.monotonic() - self.last_activity() > self.RETRY_IDLE \
                            and time.monotonic() - started > self.RETRY_IDLE:
                        # Niemand nutzt das Not-WLAN: bekannte Netze noch einmal probieren
                        log.info("Versuche bekannte WLANs erneut")
                        await self._nm("connection", "down", HOTSPOT, check=False)
                        for _ in range(9):
                            await asyncio.sleep(5)
                            if await self.refresh():
                                break
                        if not self.connected:
                            await self._start_hotspot()
                        started = time.monotonic() - self.RETRY_IDLE + 1  # nächster Versuch in 5 min
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("WLAN-Prüfung: %s", e)
            await asyncio.sleep(self.CHECK)
