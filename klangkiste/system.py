"""Systemfunktionen: Infos, Neustart, Ausschalten, Update."""
import asyncio
import shutil
import datetime as dt
import json
import re
import socket
import subprocess
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UPDATE_LOG = Path("/var/lib/klangkiste/update.log")


def _git(*args):
    # Das Programmverzeichnis gehört root; safe.directory erlaubt das Lesen.
    return _run("git", "-c", f"safe.directory={REPO}", "-C", str(REPO), *args)


def _run(*cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def booted_recently(seconds=240):
    try:
        return float(Path("/proc/uptime").read_text().split()[0]) < seconds
    except (OSError, ValueError, IndexError):
        return False


def ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


_running = None


def running_revision():
    """Stand des laufenden Programms – einmal beim ersten Aufruf gelesen. Während eines
    Updates liegt auf der Karte schon der neue Stand, es läuft aber noch der alte."""
    global _running
    if _running is None:
        _running = (_git("rev-parse", "--short", "HEAD"),
                    _git("log", "-1", "--format=%cd", "--date=format:%d.%m.%Y"))
    return _running


def info(version):
    temp = ""
    try:
        temp = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        pass
    model = ""
    try:
        model = Path("/proc/device-tree/model").read_text().strip("\x00\n")
    except OSError:
        pass
    return {
        "version": version,
        "revision": running_revision()[0],
        "revision_date": running_revision()[1],
        "hostname": socket.gethostname(),
        "ip": ip_address(),
        "temperature": temp,
        "model": model,
        "update_log": _tail(UPDATE_LOG, 12),
    }


async def _sudo(*cmd):
    proc = await asyncio.create_subprocess_exec("sudo", "-n", *cmd,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode, out.decode("utf-8", "replace").strip()


async def power(action, mock=False):
    if mock:
        return
    await _sudo("systemctl", {"reboot": "reboot", "shutdown": "poweroff", "restart": "restart"}[action],
                *(["klangkiste"] if action == "restart" else []))


def _tail(path, n):
    try:
        return "\n".join(Path(path).read_text("utf-8", "replace").strip().splitlines()[-n:])
    except OSError:
        return ""


def _github_repo():
    url = _git("config", "--get", "remote.origin.url")
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", url)
    return (m.group(1), m.group(2)) if m else (None, None)


def _compare(owner, repo, head):
    req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo}/compare/{head}...main",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "klangkiste"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


async def check_updates():
    """Fragt GitHub, ob es neuere Versionen gibt, und liefert die Änderungen."""
    head = _git("rev-parse", "HEAD")
    owner, repo = _github_repo()
    if not head or not owner:
        raise RuntimeError("Kein Git-Repository gefunden")
    d = await asyncio.to_thread(_compare, owner, repo, head)
    commits = d.get("commits", [])
    changes = [c["commit"]["message"].splitlines()[0] for c in reversed(commits)]
    return {"available": d.get("ahead_by", 0) > 0, "count": d.get("ahead_by", 0), "changes": changes[:15],
            "checked": dt.datetime.now().isoformat(timespec="minutes")}


async def restart_spotify(mock=False):
    if mock:
        return
    code, out = await _sudo("systemctl", "restart", "go-librespot")
    if code != 0:
        raise RuntimeError(out[-200:] or "Spotify-Dienst ließ sich nicht neu starten")


async def start_update(mock=False):
    """Startet den Update-Dienst (läuft als root, holt die neue Version und
    spielt auch Systemänderungen ein; die Klangkiste startet danach neu)."""
    if mock:
        return
    code, out = await _sudo("systemctl", "start", "--no-block", "klangkiste-update.service")
    if code != 0:
        raise RuntimeError("Update konnte nicht gestartet werden – bitte einmal install.sh erneut ausführen. " + out[-200:])


# ---------- Netzlaufwerk (Samba) ----------
SHARE_SCRIPT = "/opt/klangkiste/system/share.sh"
_mock_share = {"enabled": False}


async def share_status(mock=False):
    """Ist die Freigabe der Musik im Heimnetz möglich und eingeschaltet?"""
    ip = ip_address()
    out = {"available": True, "enabled": False, "user": "klangkiste",
           "windows": f"\\\\{ip}\\Klangkiste" if ip else "", "mac": f"smb://{ip}/Klangkiste" if ip else ""}
    if mock:
        out["enabled"] = _mock_share["enabled"]
        return out
    if not shutil.which("smbd"):
        out["available"] = False
        return out
    proc = await asyncio.create_subprocess_exec("systemctl", "is-active", "smbd",
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    res, _ = await proc.communicate()
    out["enabled"] = res.decode().strip() == "active"
    return out


async def share_set(enabled, password="", mock=False):
    if enabled and password and len(password) < 8:
        raise RuntimeError("Das Kennwort braucht mindestens 8 Zeichen")
    if "\n" in password or "\r" in password:
        raise RuntimeError("Das Kennwort darf keinen Zeilenumbruch enthalten")
    if mock:
        if enabled and not password and not _mock_share.get("pw"):
            raise RuntimeError("Bitte ein Kennwort vergeben")
        _mock_share.update(enabled=bool(enabled), pw=bool(password) or _mock_share.get("pw", False))
        return
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", SHARE_SCRIPT, "on" if enabled else "off",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate((password + "\n").encode())
    if proc.returncode != 0:
        raise RuntimeError(out.decode("utf-8", "replace").strip()[-200:] or "Die Freigabe ließ sich nicht ändern")
