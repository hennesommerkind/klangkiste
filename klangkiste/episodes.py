"""Folgen erkennen und aufräumen.

Die Dateien kommen sehr unterschiedlich an: mal eine Datei pro Folge, mal mehrere Dateien pro
Folge, mal mehrere Folgen auf einem Album. Hier wird aus den Titeln abgeleitet, was zusammen
eine Folge (eine Geschichte) ist, und auf Wunsch in echte Folgen-Ordner sortiert:

    Hörspiele/Serie/Folge/Teil 01.mp3

Alles Weitere (Fortsetzen, Reihenfolge, Figur der Serie, Folgensprung der Tasten, Display)
hängt am Ordner und funktioniert danach ohne Sonderfälle.
"""
import os
import re
import shutil
import statistics
from pathlib import Path

from .library import FIGSIZE, FIGURE, IMAGES, REVIEW, LibraryError, _natural, clean_name, norm

SINGLE_MIN = 480                  # ab 8 min Laufzeit gilt eine einzelne Datei als ganze Folge
LONG_MIN, LONG_FACTOR = 600, 5    # „auffällig lang“: über 10 min und fünfmal so lang wie üblich

_DUMMY = re.compile(r"^(untitled|unknown|unbekannt|ohne titel|no title|(track|titel|title|audio ?track)\s*\d*)$", re.I)
_WORD = r"(?:teil|kapitel|kap\.?|part|pt\.?|track|abschnitt)"
# „… - Teil 03“, „… (Kapitel 3 von 10)“, „… Part 2/8“
_PART = re.compile(rf"\s*[-–—:,;|]*\s*[(\[]?\s*{_WORD}\s*(\d+)\s*(?:(?:von|of|/)\s*\d+)?\s*[)\]]?\s*$", re.I)
# „… (2/10)“, „… 2 von 10“
_FRACTION = re.compile(r"\s*[-–—:,;|]*\s*[(\[]?\s*(\d+)\s*(?:/|von|of)\s*\d+\s*[)\]]?\s*$", re.I)
_LEAD_SEP = re.compile(r"^\s*\d{1,3}\s*[-–—._)]+\s*(?=\S)")     # „03 - Titel“, „03. Titel“
_LEAD_NUM = re.compile(r"^\s*\d{1,3}\s+(?=\S)")                 # „03 Titel“ (nur wenn alle so anfangen)
_EDGE = " \t-–—:,;|_."


def _tags(path):
    """(Titel, Album, Sekunden) aus den Tags; fehlt mutagen oder ist die Datei unlesbar: leer."""
    try:
        import mutagen
        m = mutagen.File(path, easy=True)
    except Exception:
        return "", "", 0.0
    if m is None:
        return "", "", 0.0

    def first(key):
        try:
            v = (m.tags or {}).get(key)
        except Exception:
            return ""
        v = v[0] if isinstance(v, (list, tuple)) and v else v
        return str(v or "").strip()

    title = first("title")
    return ("" if _DUMMY.match(title) else title), first("album"), float(getattr(m.info, "length", 0) or 0)


def split_title(title):
    """Zerlegt einen Titel in (Stamm, Teilnummer). Abgeschnitten wird genau eine Endung –
    „Bermuda-Dreieck, Teil 1 - Teil 03“ behält „Teil 1“ im Stamm."""
    t = str(title or "").strip()
    for rx in (_PART, _FRACTION):
        m = rx.search(t)
        if m and m.start() > 0:
            return t[:m.start()].strip(_EDGE), int(m.group(1))
        if m:                                   # der Titel besteht nur aus „Teil 03“
            return "", int(m.group(1))
    return t.strip(_EDGE), None


def _undouble(stem):
    """„Das Gepäck ist weg!Das Gepäck ist weg!“ → „Das Gepäck ist weg!“ (doppelt getaggt)."""
    n = len(stem)
    if n >= 8 and n % 2 == 0 and stem[:n // 2].strip() == stem[n // 2:].strip():
        return stem[:n // 2].strip(), True
    return stem, False


def _folder_name(stem):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", stem)
    name = re.sub(r"\s+", " ", name).strip().strip(".").strip()
    return name[:120].strip()


def _key(stem):
    return re.sub(r"[^a-z0-9]+", " ", norm(stem)).strip()


class Planner:
    """Plan fürs Aufräumen eines Ordners: was wird eine Folge, was fällt auf."""

    def __init__(self, library, folder):
        self.lib = library
        rel = str(folder or "").replace("\\", "/").strip("/")
        self.start = library.path(rel)
        parts = rel.split("/")
        if not self.start.is_dir() or len(parts) < 2 or library.is_music(rel):
            raise LibraryError("Aufräumen geht nur in einem Hörspiel-Ordner")
        self.series = library.path("/".join(parts[:2]))     # Folgen liegen direkt unter der Serie
        self.series_rel = library.rel(self.series)
        self.used = {}        # Name (klein) -> Ordner, dem er gehört; verhindert doppelte Folgen-Ordner
        self.moves = []       # (Quelle Path, Zielordner-Name)
        self.episodes = []    # Vorschau
        self.warnings = []
        self.review = []      # Dateien für „_Bitte prüfen“
        self.sequence = {}    # direkter Unterordner der Serie -> Folgen in Reihenfolge
        self.loose = []       # Folgen aus losen Dateien der Serie
        self.sources = []     # Ordner, aus denen Dateien wegziehen
        self.files = 0
        self._build()

    # ---------- Einlesen ----------
    def _dirs(self):
        out = []

        def walk(d):
            if d.name == REVIEW:
                return
            if self.lib._audio_files(d):
                out.append(d)
            for sub in self.lib._subdirs(d):
                walk(sub)

        walk(self.start)
        return out

    def _read(self, d):
        """Dateien eines Ordners mit Stamm, Teilnummer und Laufzeit; Gruppen in Reihenfolge."""
        files = self.lib._audio_files(d)
        raw = []
        for f in files:
            title, album, secs = _tags(f)
            raw.append({"path": f, "title": title or f.stem, "album": album, "secs": secs, "tagged": bool(title)})
        # Führende Titelnummern: „03 - …“ immer weg, „03 …“ nur, wenn alle Titel so beginnen
        all_numbered = len(raw) > 1 and all(_LEAD_NUM.match(r["title"]) or _LEAD_SEP.match(r["title"]) for r in raw)
        groups = {}
        for r in raw:
            t = _LEAD_SEP.sub("", r["title"], 1)
            if all_numbered and t == r["title"]:
                t = _LEAD_NUM.sub("", t, 1)
            stem, part = split_title(t)
            by_album = False
            if not stem or stem.isdigit() or _DUMMY.match(stem):
                # Titel sagt nichts („Track 03“, „07“): dann entscheidet das Album
                stem = r["album"] if r["album"] and not _DUMMY.match(r["album"]) else ""
                by_album = bool(stem)
            stem, doubled = _undouble(stem)
            r.update(stem=stem, part=part, doubled=doubled, marker=part is not None or by_album)
            groups.setdefault(_key(stem), []).append(r)
        return raw, groups

    # ---------- Planen ----------
    def _claim(self, name, home):
        """Freier Ordnername für eine Folge. home = Ordner, in dem die Dateien schon liegen."""
        name = _folder_name(name) or home.name
        target = self.series / name
        if home != self.series and target == home:
            self.used[name.casefold()] = home
            return name, True                       # die Dateien liegen schon richtig
        base, n = name, 1
        while True:
            owner = self.used.get(name.casefold())
            taken = owner is not None or (self.series / name).exists()
            if not taken:
                break
            n += 1
            name = f"{base} ({n})"
        if n > 1:
            self.warnings.append(f"„{base}“ gibt es mehrfach – die weitere Folge heißt „{name}“.")
        self.used[name.casefold()] = home
        return name, False

    def _build(self):
        has_subs = bool(self.lib._subdirs(self.series))
        for d in self._dirs():
            raw, groups = self._read(d)
            self.files += len(raw)
            top = self._top(d)
            if len(groups) > 1 and not any(r["marker"] for r in raw):
                # Lauter verschiedene Titel ohne „Teil“: Kapitel eines Hörspiels oder ganze Folgen?
                # Kurze Dateien sind Kapitel – der Ordner bleibt eine Folge.
                secs = [r["secs"] for r in raw if r["secs"] > 0]
                if len(secs) < len(raw) or statistics.median(secs) < SINGLE_MIN:
                    groups = {"": raw}
            single = len(groups) == 1
            if single and (d != self.series or not has_subs):
                self._note(top, d.name if d != self.series else None)
                self._check(d.name, raw, d)
                continue                            # ein Ordner = eine Folge: nichts zu tun
            self.sources.append(d)
            for key, items in groups.items():
                stem = next((r["stem"] for r in items if r["stem"]), "")
                if len(items) == 1 and not items[0]["marker"]:
                    # Eine Datei = eine Folge: der Dateiname trägt oft mehr als der Titel („Serie - 038 - Titel“).
                    # Dann heißt die Folge wie die Datei – so bleibt die Folgennummer erhalten und die
                    # Reihenfolge stimmt von selbst (wie bei „Jede Datei als eigene Folge“ beim Hochladen).
                    file_stem = items[0]["path"].stem.strip()
                    if stem and _key(stem) in _key(file_stem):
                        stem = file_stem
                name, stays = self._claim(stem or d.name, d)
                if any(r["doubled"] for r in items):
                    self.warnings.append(f"Doppelter Titel in den Dateien – als „{name}“ einsortiert.")
                items = self._check(name, items, d)
                self.episodes.append({"name": name, "files": len(items), "seconds": round(sum(r["secs"] for r in items)),
                                      "from": self.lib.rel(d), "stays": stays})
                self._note(top, name)
                if not stays:
                    self.moves += [(r["path"], name) for r in items]

    def _top(self, d):
        """Direkter Unterordner der Serie, unter dem d liegt (None für die Serie selbst)."""
        rel = d.relative_to(self.series).parts
        return rel[0] if rel else None

    def _note(self, top, name):
        if name is None:
            return
        seq = self.loose if top is None else self.sequence.setdefault(top, [])
        if name not in seq:
            seq.append(name)

    def _check(self, name, items, d):
        """Meldet Auffälliges in einer Folge; auffällig lange Teile gehen in die Prüfliste."""
        parts = sorted(r["part"] for r in items if r["part"] is not None)
        if parts:
            missing = sorted(set(range(1, parts[-1] + 1)) - set(parts))
            if missing:
                self.warnings.append(f"„{name}“ ist unvollständig: "
                                     + (f"Teil {missing[0]} fehlt." if len(missing) == 1 else f"Teile {_ranges(missing)} fehlen."))
        marked = [r for r in items if r["marker"] and r["secs"] > 0]
        if len(marked) >= 3:
            med = statistics.median(r["secs"] for r in marked)
            long = [r for r in marked if r["secs"] > max(LONG_MIN, LONG_FACTOR * med)]
            for r in long:
                self.review.append({"file": self.lib.rel(r["path"]), "seconds": round(r["secs"]),
                                    "why": f"auffällig lang für „{name}“ (die anderen Teile dauern rund {_dur(med)})"})
            items = [r for r in items if r not in long]
        return items

    # ---------- Ergebnis ----------
    def preview(self):
        return {"series": self.series_rel, "files": self.files, "moves": len(self.moves) + len(self.review),
                "episodes": [e for e in self.episodes], "kept": self.files - len(self.moves) - len(self.review),
                "warnings": list(dict.fromkeys(self.warnings)), "review": self.review, "review_folder": REVIEW}

    def apply(self):
        """Führt den Plan aus. Liefert die aufgelösten Ordner (für Karten) und Zahlen für die Meldung."""
        lib, series = self.lib, self.series
        if not self.moves and not self.review:
            return {"moved": 0, "episodes": 0, "removed": [], "review": 0, "series": self.series_rel}
        old_order = [d.name for d in lib._subdirs(series)]
        # Kamen alle neuen Folgen aus losen Dateien der Serie und gab es keine eigene Reihenfolge, bleibt es
        # bei der natürlichen Sortierung (Folge 5 vor Folge 38) – sonst stünden die neuen vor den alten.
        natural = not lib._order(series).get("folders") and all(src.parent == series for src, _ in self.moves)
        made, track_order, covers = [], {}, {}
        plan = [(src, name) for src, name in self.moves] + [(lib.path(r["file"]), REVIEW) for r in self.review]
        for src, name in plan:
            dst_dir = series / clean_name(name)
            if not dst_dir.exists():
                dst_dir.mkdir()
                made.append(name)
                cover = lib.find_cover(src.parent)
                if cover and name != REVIEW:
                    covers[dst_dir] = cover
            dst = dst_dir / src.name
            n = 1
            while dst.exists():
                n += 1
                dst = dst_dir / f"{src.stem} ({n}){src.suffix}"
            os.replace(src, dst)
            track_order.setdefault(dst_dir, []).append(dst.name)
        for d, cover in covers.items():
            _inherit_cover(lib, d, cover)
        for d, names in track_order.items():          # Reihenfolge wie vorher, falls die Namen sie nicht hergeben
            if d.name != REVIEW and names != sorted(names, key=_natural):
                existing = [f.name for f in lib._audio_files(d) if f.name not in names]
                data = lib._order(d)
                data["tracks"] = existing + names
                lib._write_order(d, data)
        removed = []
        for d in sorted(self.sources, key=lambda p: len(p.parts), reverse=True):
            # Ein Album-Ordner verschwindet nur, wenn außer Bildern und versteckten Hilfsdateien nichts
            # mehr darin liegt – alles andere (Texte, Unterordner, Titel) bleibt unangetastet.
            if d == series or any(x.is_dir() or not (x.name.startswith(".") or x.suffix.lower() in IMAGES)
                                  for x in d.iterdir()):
                continue
            if (d / FIGURE).is_file() and not (series / FIGURE).exists():
                for f in (FIGURE, FIGSIZE):             # die Figur des Albums gehört jetzt der Serie
                    if (d / f).is_file():
                        os.replace(d / f, series / f)
            removed.append(lib.rel(d))
            shutil.rmtree(d)
        # Reihenfolge der Folgen: an der Stelle des alten Albums, in der Reihenfolge der Titel
        now = {d.name for d in lib._subdirs(series)}
        order = [n for n in self.loose if n in now]
        for name in old_order:
            for n in ([name] if name in now and name not in self.sequence.get(name, []) else []) + self.sequence.get(name, []):
                if n in now and n not in order:
                    order.append(n)
        order += [n for n in sorted(now - set(order) - {REVIEW}, key=_natural)] + ([REVIEW] if REVIEW in now else [])
        if not natural:
            data = lib._order(series)
            data["folders"] = order
            lib._write_order(series, data)
        return {"moved": len(plan), "episodes": len([m for m in made if m != REVIEW]), "removed": removed,
                "review": len(self.review), "series": self.series_rel}


def _inherit_cover(library, d, cover):
    """Gibt einer neuen Folge die Bilddatei ihres bisherigen Ordners mit – aber nur, wenn ihre Titel kein
    eigenes Bild in den Tags tragen. Eine Bilddatei im Ordner hat Vorrang vor den Tags und würde das
    eigene Cover der Folge sonst verdecken."""
    try:
        if library.find_cover(d) or library.cover_bytes(library.rel(d))[0] is not None:
            return
        shutil.copy2(cover, d / cover.name)
    except OSError:
        pass


def _dur(s):
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def _ranges(nums):
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(out)


# ---------- Korrektur von Hand ----------
def merge(library, folders):
    """Fasst Folgen zusammen: die Titel aller weiteren Ordner ziehen in den ersten, in dieser Reihenfolge."""
    dirs = [library.path(f) for f in dict.fromkeys(str(f).strip("/") for f in folders)]
    if len(dirs) < 2:
        raise LibraryError("Bitte mindestens zwei Folgen auswählen")
    if len({d.parent for d in dirs}) != 1 or any(not d.is_dir() or library.is_category(library.rel(d)) for d in dirs):
        raise LibraryError("Zusammenfassen geht nur mit Folgen derselben Serie")
    if any(library._subdirs(d) for d in dirs):
        raise LibraryError("Ordner mit Unterordnern lassen sich nicht zusammenfassen")
    target = dirs[0]
    names = [f.name for f in library._audio_files(target)]
    removed = []
    for d in dirs[1:]:
        for f in library._audio_files(d):
            dst = target / f.name
            if dst.exists():
                dst = target / f"{d.name} - {f.name}"
            n = 1
            while dst.exists():
                n += 1
                dst = target / f"{d.name} - {f.stem} ({n}){f.suffix}"
            os.replace(f, dst)
            names.append(dst.name)
        cover = library.find_cover(d)
        if cover:
            _inherit_cover(library, target, cover)
        removed.append(library.rel(d))
        shutil.rmtree(d)
    data = library._order(target)
    data["tracks"] = names
    library._write_order(target, data)
    return library.rel(target), removed


def split(library, folder, files, name):
    """Trennt Titel als eigene Folge ab: sie ziehen in einen neuen Ordner direkt hinter der bisherigen Folge."""
    rel = str(folder or "").strip("/")
    d = library.path(rel)
    if not d.is_dir() or rel.count("/") < 2:
        raise LibraryError("Abtrennen geht nur in einer Folge einer Serie")
    all_files = library._audio_files(d)
    chosen = [f for f in all_files if f.name in set(files)]
    if not chosen or len(chosen) == len(all_files):
        raise LibraryError("Bitte einen Teil der Titel auswählen, nicht alle")
    dst = d.parent / clean_name(name)
    if dst.exists():
        raise LibraryError("Diesen Namen gibt es schon")
    siblings = [x.name for x in library._subdirs(d.parent)]
    dst.mkdir()
    for f in chosen:
        os.replace(f, dst / f.name)
    cover = library.find_cover(d)
    if cover:
        _inherit_cover(library, dst, cover)
    names = [f.name for f in chosen]
    if names != sorted(names, key=_natural):
        library._write_order(dst, {"tracks": names})
    siblings.insert(siblings.index(d.name) + 1, dst.name)
    data = library._order(d.parent)
    data["folders"] = siblings
    library._write_order(d.parent, data)
    return library.rel(dst)
