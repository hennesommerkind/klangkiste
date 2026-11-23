"""Folgen erkennen und aufräumen – Test mit erzeugten MP3-Dateien.

    python3 tests/test_episodes.py        (braucht ffmpeg und mutagen)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mutagen.easyid3 import EasyID3  # noqa: E402

from klangkiste import episodes  # noqa: E402
from klangkiste.library import Library  # noqa: E402

BASE = {}


def base(seconds, tmp):
    if seconds not in BASE:
        f = Path(tmp) / f"base{seconds}.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", str(seconds),
                        "-b:a", "8k", "-y", str(f)], check=True)
        BASE[seconds] = f
    return BASE[seconds]


def mp3(path, tmp, title=None, album=None, seconds=75):
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(base(seconds, tmp), path)
    tags = EasyID3()
    if title:
        tags["title"] = title
    if album:
        tags["album"] = album
    tags.save(path)


def names(d):
    return sorted(p.name for p in d.iterdir() if not p.name.startswith("."))


def main():
    tmp = tempfile.mkdtemp()
    root = Path(tmp) / "music"
    lib = Library(root)
    h = root / "Hörspiele"

    # 1) Mehrere Folgen auf einem Album, mehrere Dateien pro Folge, ein Ausreißer, eine unvollständige Folge
    a1 = h / "Super Wings" / "Album 1"
    n = 0
    for story in ("Sheriff Susannah", "Jet und der Wal", "Verschwunden im Bermuda-Dreieck, Teil 1",
                  "Verschwunden im Bermuda-Dreieck, Teil 2"):
        for i in range(1, 11):
            n += 1
            long = story == "Sheriff Susannah" and i == 6
            mp3(a1 / f"{n:03d} {story} - Teil {i:02d}.mp3", tmp, f"{story} - Teil {i:02d}", "Album 1", 1460 if long else 75)
    (a1 / "cover.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    a2 = h / "Super Wings" / "Album 2"
    for i in range(7, 11):
        mp3(a2 / f"{i:02d}.mp3", tmp, f"Die Suche nach der Meerjungfrau - Teil {i:02d}", "Album 2")
    for i in range(1, 5):
        mp3(a2 / f"{i + 20:02d}.mp3", tmp, f"Das Gepäck ist weg!Das Gepäck ist weg! - Teil {i:02d}", "Album 2", 185)
    # schon richtig: ein Ordner = eine Folge
    for i in range(1, 4):
        mp3(h / "Super Wings" / "Der Riesen-Burrito" / f"Der Riesen-Burrito - Teil {i:02d}.mp3", tmp,
            f"Der Riesen-Burrito - Teil {i:02d}", seconds=185)

    p = episodes.Planner(lib, "Hörspiele/Super Wings")
    pv = p.preview()
    got = [e["name"] for e in pv["episodes"]]
    assert got == ["Sheriff Susannah", "Jet und der Wal", "Verschwunden im Bermuda-Dreieck, Teil 1",
                   "Verschwunden im Bermuda-Dreieck, Teil 2", "Die Suche nach der Meerjungfrau",
                   "Das Gepäck ist weg!"], got
    assert [r["file"].rsplit("/", 1)[-1] for r in pv["review"]] == ["006 Sheriff Susannah - Teil 06.mp3"], pv["review"]
    assert any("Meerjungfrau" in w and "Teile 1–6 fehlen" in w for w in pv["warnings"]), pv["warnings"]
    assert any("Doppelter Titel" in w for w in pv["warnings"]), pv["warnings"]
    assert not any("Sheriff" in w and "unvollständig" in w for w in pv["warnings"]), pv["warnings"]
    assert pv["files"] == 51 and pv["moves"] == 48, pv
    r = p.apply()
    assert sorted(r["removed"]) == ["Hörspiele/Super Wings/Album 1", "Hörspiele/Super Wings/Album 2"], r
    assert r["episodes"] == 6 and r["review"] == 1
    assert names(h / "Super Wings") == sorted(got + ["Der Riesen-Burrito", episodes.REVIEW]), names(h / "Super Wings")
    assert len(names(h / "Super Wings" / "Sheriff Susannah")) == 10      # 9 Teile + cover.jpg
    order = [d.name for d in lib._subdirs(h / "Super Wings")]
    assert order == got + ["Der Riesen-Burrito", episodes.REVIEW], order
    # Abspielreihenfolge: folgenweise, Teile in Reihenfolge
    files = lib.all_files("Hörspiele/Super Wings")
    assert files[0].endswith("Sheriff Susannah/001 Sheriff Susannah - Teil 01.mp3"), files[0]
    assert files[9].endswith("Jet und der Wal/011 Jet und der Wal - Teil 01.mp3"), files[9]
    # zweiter Lauf: nichts mehr zu tun
    again = episodes.Planner(lib, "Hörspiele/Super Wings").preview()
    assert again["moves"] == 0 and not again["episodes"], again

    # 2) Eine Datei pro Folge (lange Dateien, verschiedene Titel) in einem Sammelordner
    for i, t in enumerate(("Der Hexenbesen", "Kartoffelbrei", "Die Kuh im Schlafzimmer"), 1):
        mp3(h / "Bibi" / "Sammlung" / f"{i:02d} - {t}.mp3", tmp, None, None, 1200)
    pv = episodes.Planner(lib, "Hörspiele/Bibi").preview()
    assert [e["name"] for e in pv["episodes"]] == ["Der Hexenbesen", "Kartoffelbrei", "Die Kuh im Schlafzimmer"], pv
    r = episodes.Planner(lib, "Hörspiele/Bibi").apply()
    assert r["removed"] == ["Hörspiele/Bibi/Sammlung"] and names(h / "Bibi") == ["Der Hexenbesen", "Die Kuh im Schlafzimmer",
                                                                                 "Kartoffelbrei"]
    assert [d.name for d in lib._subdirs(h / "Bibi")] == ["Der Hexenbesen", "Kartoffelbrei", "Die Kuh im Schlafzimmer"]

    # 3) Einzelnes Hörspiel mit benannten, kurzen Kapiteln: bleibt, wie es ist
    for i, t in enumerate(("Der Wald", "Die Maus", "Der Fuchs", "Die Eule"), 1):
        mp3(h / "Der Grüffelo" / f"{i:02d} {t}.mp3", tmp, t, "Der Grüffelo", 75)
    pv = episodes.Planner(lib, "Hörspiele/Der Grüffelo").preview()
    assert pv["moves"] == 0 and not pv["episodes"], pv

    # 4) Unbrauchbare Titel („Track 01“), aber zwei Alben in einem Ordner: Album-Tag entscheidet
    for alb in ("Folge 1 - Als Wetterelefant", "Folge 2 - Rettet den Zoo"):
        for i in range(1, 4):
            mp3(h / "Benjamin" / f"{int(alb[6]) * 10 + i:02d}.mp3", tmp, f"Track {i:02d}", alb, 185)
    pv = episodes.Planner(lib, "Hörspiele/Benjamin").preview()
    assert [e["name"] for e in pv["episodes"]] == ["Folge 1 - Als Wetterelefant", "Folge 2 - Rettet den Zoo"], pv
    assert all(e["files"] == 3 for e in pv["episodes"])

    # 5) Ganz ohne brauchbare Angaben: der Ordner ist eine Folge
    for i in range(1, 4):
        mp3(h / "Unbekannt" / f"Track {i:02d}.mp3", tmp, None, None, 185)
    assert episodes.Planner(lib, "Hörspiele/Unbekannt").preview()["moves"] == 0

    # 6) Von Hand: zusammenfassen und abtrennen
    target, removed = episodes.merge(lib, ["Hörspiele/Super Wings/Verschwunden im Bermuda-Dreieck, Teil 1",
                                           "Hörspiele/Super Wings/Verschwunden im Bermuda-Dreieck, Teil 2"])
    assert len(lib.tracks(target)) == 20 and len(removed) == 1
    t = [x["file"] for x in lib.tracks(target)]
    assert "Teil 1 - Teil 01" in t[0] and "Teil 2 - Teil 10" in t[-1], (t[0], t[-1])
    new = episodes.split(lib, target, t[10:], "Bermuda-Dreieck 2")
    assert len(lib.tracks(new)) == 10 and len(lib.tracks(target)) == 10
    order = [d.name for d in lib._subdirs(h / "Super Wings")]
    assert order.index("Bermuda-Dreieck 2") == order.index("Verschwunden im Bermuda-Dreieck, Teil 1") + 1, order

    # 7) Liegt im Album noch etwas anderes als Bilder, bleibt der Ordner stehen (nichts wird gelöscht)
    for st in ("Conni backt", "Conni im Zoo"):
        for i in range(1, 4):
            mp3(h / "Conni" / "CD 1" / f"{st} - Teil {i}.mp3", tmp, f"{st} - Teil {i}", None, 185)
    (h / "Conni" / "CD 1" / "Booklet.txt").write_text("Text")
    r = episodes.Planner(lib, "Hörspiele/Conni").apply()
    assert r["episodes"] == 2 and r["removed"] == [] and (h / "Conni" / "CD 1" / "Booklet.txt").exists(), r
    assert lib.all_files("Hörspiele/Conni")[0].endswith("Conni backt/Conni backt - Teil 1.mp3")

    # 8) Titel mit eigenem Bild in den Tags: die Bilddatei des Serienordners wird NICHT in die Folgen kopiert
    #    (sie hätte Vorrang und würde das eigene Cover jeder Folge verdecken)
    from mutagen.id3 import APIC, ID3
    for st in ("Als Pirat", "Als Ritter"):
        f = h / "Benjamin Blümchen" / f"{st}.mp3"
        mp3(f, tmp, st, None, 1200)
        tags = ID3(f)
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=b"\xff\xd8" + st.encode() + b"\xff\xd9"))
        tags.save(f)
    mp3(h / "Benjamin Blümchen" / "Ohne Bild.mp3", tmp, "Ohne Bild", None, 1200)
    (h / "Benjamin Blümchen" / "cover.jpg").write_bytes(b"\xff\xd8SERIE\xff\xd9")
    r = episodes.Planner(lib, "Hörspiele/Benjamin Blümchen").apply()
    assert r["episodes"] == 3, r
    assert not (h / "Benjamin Blümchen" / "Als Pirat" / "cover.jpg").exists()
    assert b"Als Pirat" in lib.cover_bytes("Hörspiele/Benjamin Blümchen/Als Pirat")[0]
    assert (h / "Benjamin Blümchen" / "Ohne Bild" / "cover.jpg").exists()      # ohne Tag-Bild erbt die Folge das Serienbild

    # „_Bitte prüfen“ läuft nicht mit der Serie mit
    assert not any(episodes.REVIEW in f for f in lib.all_files("Hörspiele/Super Wings"))

    # Titel zerlegen
    for title, want in (("Sheriff Susannah - Teil 01", ("Sheriff Susannah", 1)), ("Bibi (Kapitel 3 von 12)", ("Bibi", 3)),
                        ("Conni 2/8", ("Conni", 2)), ("Bibi Blocksberg Folge 3", ("Bibi Blocksberg Folge 3", None)),
                        ("Teil 04", ("", 4)), ("101 Dalmatiner", ("101 Dalmatiner", None))):
        assert episodes.split_title(title) == want, (title, episodes.split_title(title))

    shutil.rmtree(tmp)
    print("OK – alle Fälle bestanden")


if __name__ == "__main__":
    main()
