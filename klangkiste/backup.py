"""Sicherung auf IONOS HiDrive (WebDAV über rclone) und als Download."""
import asyncio
import datetime as dt
import io
import json
import logging
import os
import shutil
import tarfile
import tempfile

log = logging.getLogger("backup")
FILES = ("config.json", "cards.json", "resume.json")
PREFIX = "klangkiste-"


class BackupError(RuntimeError):
    pass


class Backup:
    def __init__(self, store, on_restored=None, on_status=None):
        self.store = store
        self.on_restored = on_restored or (lambda: None)
        self.on_status = on_status or (lambda s: None)
        self._lock = asyncio.Lock()
        self._state_file = store.dir / "backup-state.json"
        try:
            self.status = json.loads(self._state_file.read_text())
        except (OSError, ValueError):
            self.status = {}
        self.status.update({"running": False, "step": ""})

    # ---------- Archiv mit Einstellungen und Karten ----------
    def make_archive(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name in FILES:
                p = self.store.dir / name
                if p.exists():
                    tar.add(p, arcname=name)
        return buf.getvalue()

    def apply_archive(self, data):
        found = 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for m in tar.getmembers():
                name = os.path.basename(m.name)
                if not m.isfile() or name not in FILES or m.size > 20_000_000:
                    continue
                raw = tar.extractfile(m).read()
                json.loads(raw)  # nur gültiges JSON übernehmen
                tmp = self.store.dir / (name + ".restore")
                tmp.write_bytes(raw)
                os.replace(tmp, self.store.dir / name)
                found += 1
        if not found:
            raise BackupError("Das Archiv enthält keine Klangkiste-Sicherung")
        self.store.reload()
        self.on_restored()

    # ---------- rclone ----------
    @property
    def cfg(self):
        return self.store.config["backup"]

    def configured(self):
        c = self.cfg
        return bool(c["url"] and c["username"] and c["password"])

    async def _run(self, *args, env=None, input=None, timeout=None):
        if not shutil.which("rclone"):
            raise BackupError("rclone ist nicht installiert")
        proc = await asyncio.create_subprocess_exec(
            "rclone", *args, env=env, stdin=asyncio.subprocess.PIPE if input else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(input), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise BackupError("Zeitüberschreitung bei der Verbindung zu HiDrive")
        if proc.returncode != 0:
            msg = err.decode("utf-8", "replace").strip().splitlines()
            raise BackupError(self._friendly(msg[-1] if msg else "rclone-Fehler"))
        return out.decode("utf-8", "replace")

    @staticmethod
    def _friendly(msg):
        low = msg.lower()
        if "401" in low or "unauthorized" in low:
            return "Anmeldung abgelehnt – Benutzername/Passwort prüfen und WebDAV in HiDrive aktivieren"
        if "no such host" in low or "dial tcp" in low:
            return "HiDrive ist nicht erreichbar – Internetverbindung und Adresse prüfen"
        return msg[-300:]

    async def _env(self):
        if not self.configured():
            raise BackupError("Bitte erst Benutzername und Passwort für HiDrive eintragen")
        c = self.cfg
        obscured = (await self._run("obscure", "-", input=c["password"].encode())).strip()
        env = dict(os.environ)
        env.update({
            "RCLONE_CONFIG": str(self.store.dir / "rclone.conf"),
            "RCLONE_CONFIG_KK_TYPE": "webdav",
            "RCLONE_CONFIG_KK_URL": c["url"],
            "RCLONE_CONFIG_KK_VENDOR": "other",
            "RCLONE_CONFIG_KK_USER": c["username"],
            "RCLONE_CONFIG_KK_PASS": obscured,
        })
        return env

    def _remote(self, *parts):
        base = self.cfg["remote_path"].strip("/") or "Klangkiste"
        return "kk:" + "/".join([base, *parts])

    def _set(self, **kw):
        self.status.update(kw)
        try:
            self.on_status(self.status)
        except Exception:
            pass

    def _persist(self):
        keep = {k: v for k, v in self.status.items() if k not in ("running", "step")}
        try:
            self._state_file.write_text(json.dumps(keep, ensure_ascii=False))
        except OSError:
            pass

    async def test(self):
        env = await self._env()
        await self._run("mkdir", self._remote(), env=env, timeout=60)
        await self._run("lsf", self._remote(), "--max-depth", "1", env=env, timeout=60)
        return True

    async def run(self):
        """Sichert Einstellungen/Karten (datiert) und spiegelt die Musik."""
        if self._lock.locked():
            raise BackupError("Es läuft bereits eine Sicherung")
        async with self._lock:
            now = dt.datetime.now()
            self._set(running=True, step="Verbinde mit HiDrive …", error="")
            try:
                env = await self._env()
                name = f"{PREFIX}{now:%Y-%m-%d_%H%M}.tar.gz"
                with tempfile.TemporaryDirectory() as tmp:
                    p = os.path.join(tmp, name)
                    with open(p, "wb") as f:
                        f.write(self.make_archive())
                    self._set(step="Sichere Einstellungen und Karten …")
                    await self._run("copyto", p, self._remote("einstellungen", name), env=env, timeout=600)
                if self.cfg["include_music"]:
                    self._set(step="Sichere Musik …")
                    await self._run("sync", str(self.store.music), self._remote("musik"),
                                    "--transfers", "2", "--exclude", ".*", env=env, timeout=6 * 3600)
                names = await self._list(env)
                for old in names[int(self.cfg["keep"]):]:
                    await self._run("deletefile", self._remote("einstellungen", old), env=env, timeout=120)
                self._set(running=False, step="", ok=True, last=now.isoformat(timespec="minutes"),
                          last_ok=now.isoformat(timespec="minutes"), music=bool(self.cfg["include_music"]))
            except Exception as e:
                log.warning("Sicherung fehlgeschlagen: %s", e)
                self._set(running=False, step="", ok=False, error=str(e),
                          last=now.isoformat(timespec="minutes"))
                self._persist()
                raise BackupError(str(e)) from e
            self._persist()

    async def _list(self, env):
        try:
            out = await self._run("lsf", self._remote("einstellungen"), env=env, timeout=120)
        except BackupError as e:
            if "not found" in str(e).lower():
                return []
            raise
        return sorted((n for n in out.split() if n.startswith(PREFIX)), reverse=True)

    async def list(self):
        return await self._list(await self._env())

    async def restore(self, name, music=True):
        if self._lock.locked():
            raise BackupError("Es läuft bereits eine Sicherung")
        if "/" in name or not name.startswith(PREFIX):
            raise BackupError("Unbekannte Sicherung")
        async with self._lock:
            self._set(running=True, step="Hole Sicherung …", error="")
            try:
                env = await self._env()
                with tempfile.TemporaryDirectory() as tmp:
                    p = os.path.join(tmp, name)
                    await self._run("copyto", self._remote("einstellungen", name), p, env=env, timeout=600)
                    with open(p, "rb") as f:
                        data = f.read()
                if music:
                    self._set(step="Hole Musik zurück …")
                    await self._run("copy", self._remote("musik"), str(self.store.music),
                                    "--transfers", "2", env=env, timeout=6 * 3600)
                # Zugangsdaten der laufenden Box behalten, sonst sperrt man sich aus
                creds = {k: self.cfg[k] for k in ("url", "username", "password", "remote_path")}
                self.apply_archive(data)
                self.store.update_config({"backup": creds})
                self._set(running=False, step="")
            except Exception as e:
                self._set(running=False, step="", error=str(e))
                raise BackupError(str(e)) from e

    # ---------- Zeitplan ----------
    def due(self, now=None):
        c = self.cfg
        if c["schedule"] not in ("daily", "weekly") or not self.configured():
            return False
        now = now or dt.datetime.now()
        if now.hour != int(c["hour"]):
            return False
        if c["schedule"] == "weekly" and now.weekday() != int(c["weekday"]):
            return False
        return not str(self.status.get("last", "")).startswith(now.strftime("%Y-%m-%d"))

    async def scheduler(self):
        while True:
            await asyncio.sleep(60)
            try:
                if self.due():
                    await self.run()
            except BackupError:
                pass
            except Exception:
                log.exception("Zeitplan")
