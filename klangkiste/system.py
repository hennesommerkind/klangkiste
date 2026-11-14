"""Systemfunktionen: Infos, Neustart, Ausschalten, Update."""
import asyncio
import shutil
import socket
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(*cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


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
        "revision": _run("git", "-C", str(REPO), "rev-parse", "--short", "HEAD"),
        "hostname": socket.gethostname(),
        "ip": ip_address(),
        "temperature": temp,
        "model": model,
        "spotify": bool(shutil.which("librespot")),
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


async def update(mock=False):
    """Holt die neueste Version von GitHub und startet den Dienst neu."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(REPO), "pull", "--ff-only",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    text = out.decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        raise RuntimeError(text[-400:] or "Update fehlgeschlagen")
    changed = "Already up to date" not in text and "Bereits aktuell" not in text
    if changed and not mock:
        asyncio.get_running_loop().call_later(1.0, lambda: asyncio.ensure_future(power("restart")))
    return {"changed": changed, "log": text[-400:]}
