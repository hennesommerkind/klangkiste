"""Startton und Sprachansagen (z. B. die IP-Adresse).

Abgespielt wird mit aplay über das ALSA-Standardgerät, also gemischt mit der
Musik. Weil die Soundkarte keinen eigenen Lautstärkeregler hat, werden die
Töne vor dem Abspielen auf die eingestellte Lautstärke heruntergerechnet.
"""
import array
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
import wave

log = logging.getLogger("sounds")
_lock = threading.Lock()  # nie zwei Ansagen gleichzeitig


def _play(path):
    if not shutil.which("aplay"):
        return False
    try:
        subprocess.run(["aplay", "-q", path], timeout=60, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Abspielen: %s", e)
        return False


def _gain(volume):
    # Lautstärke 0–100 wie in der Oberfläche, leicht gebogen wie das Ohr hört
    return max(0.0, min(1.0, volume / 100)) ** 1.6


def chime(volume=40):
    """Kurzer, freundlicher Zweiklang (etwa 0,6 Sekunden)."""
    rate, g = 44100, _gain(volume) * 0.6
    samples = array.array("h")
    for freq, dur in ((784.0, 0.18), (1046.5, 0.42)):   # G5 → C6
        n = int(rate * dur)
        for i in range(n):
            t = i / rate
            env = min(1.0, t / 0.008) * math.exp(-t * 6)  # weicher Anschlag, ausklingend
            v = math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(4 * math.pi * freq * t)
            samples.append(int(32767 * g * env * v / 1.25))
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(samples.tobytes())
        with _lock:
            return _play(path)
    finally:
        os.unlink(path)


def tts_engine():
    if shutil.which("pico2wave"):
        return "pico"
    if shutil.which("espeak-ng"):
        return "espeak"
    return None


def say(text, volume=40):
    """Spricht einen deutschen Text. Gibt False zurück, wenn keine Sprachausgabe da ist."""
    engine = tts_engine()
    if not engine:
        log.info("Keine Sprachausgabe installiert: %s", text)
        return False
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        if engine == "pico":
            cmd = ["pico2wave", "-l", "de-DE", "-w", path, text]
        else:
            cmd = ["espeak-ng", "-v", "de", "-s", "140", "-w", path, text]
        subprocess.run(cmd, timeout=30, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _scale(path, _gain(volume) * 1.6)
        with _lock:
            return _play(path)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Sprachausgabe: %s", e)
        return False
    finally:
        os.unlink(path)


def _scale(path, factor):
    """16-Bit-WAV in der Lautstärke anpassen (ohne Zusatzprogramme)."""
    factor = min(factor, 1.0)
    with wave.open(path, "rb") as r:
        params = r.getparams()
        frames = r.readframes(r.getnframes())
    if params.sampwidth != 2:
        return
    a = array.array("h", frames)
    for i in range(len(a)):
        a[i] = int(a[i] * factor)
    with wave.open(path, "wb") as w:
        w.setparams(params)
        w.writeframes(a.tobytes())


def speakable_ip(ip):
    """„192.168.178.57“ → „192 Punkt 168 Punkt 178 Punkt 57“ (klarer als die Zahl am Stück)."""
    return " Punkt ".join(ip.split("."))
