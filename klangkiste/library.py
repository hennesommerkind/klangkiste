"""Musikverwaltung: Ordner, Titel, Cover, Uploads."""
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path

AUDIO = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav", ".wma"}
IMAGES = {".jpg", ".jpeg", ".png", ".webp"}
COVER_NAMES = ("cover", "folder", "front", "album")
FIGURE = ".figur.png"   # versteckt: taucht weder als Titelbild noch als Titel auf
FIGSIZE = ".figur.json"  # Größe der Figur auf dem Cover (Faktor, 1 = Standard)
FIG_MIN, FIG_MAX = 0.5, 1.5
CATEGORIES = ("Hörspiele", "Musik")
REVIEW = "_Bitte prüfen"   # Ablage fürs Aufräumen: sichtbar in der Oberfläche, aber nicht Teil der Serie beim Abspielen
ORDER_FILE = ".reihenfolge.json"   # eigene Reihenfolge pro Ordner; wandert mit Sicherung und Umbenennen mit


class LibraryError(ValueError):
    pass


def clean_name(name):
    """Prüft einen einzelnen Datei- oder Ordnernamen."""
    name = re.sub(r"[\x00-\x1f]", "", str(name or "")).strip().strip(".").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise LibraryError("Ungültiger Name")
    return name[:150]


def norm(text):
    """Für die Suche: klein, ohne Akzente/Umlautpunkte, ß→ss („Grüffelo“ ≙ „gruffelo“)."""
    t = unicodedata.normalize("NFKD", str(text).casefold().replace("ß", "ss"))
    return "".join(c for c in t if not unicodedata.combining(c))


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


class Library:
    def __init__(self, root):
        self.root = Path(root).resolve()
        for c in CATEGORIES:
            (self.root / c).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def is_category(rel):
        return str(rel or "").strip("/") in CATEGORIES

    @staticmethod
    def is_music(rel):
        rel = str(rel or "").strip("/")
        return rel == "Musik" or rel.startswith("Musik/")

    def path(self, rel, must_exist=True):
        """Löst einen relativen Pfad sicher innerhalb des Musikordners auf."""
        rel = str(rel or "").replace("\\", "/").strip("/")
        parts = [clean_name(p) for p in rel.split("/") if p]
        p = self.root.joinpath(*parts).resolve()
        if p != self.root and self.root not in p.parents:
            raise LibraryError("Pfad liegt außerhalb der Musik")
        if must_exist and not p.exists():
            raise LibraryError("Nicht gefunden")
        return p

    def rel(self, p):
        return str(Path(p).relative_to(self.root)).replace(os.sep, "/")

    # ---------- Reihenfolge ----------
    @staticmethod
    def _order(d):
        try:
            data = json.loads((d / ORDER_FILE).read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_order(self, d, data):
        data = {k: v for k, v in data.items() if v}
        p = d / ORDER_FILE
        if not data:
            p.unlink(missing_ok=True)
            return
        tmp = d / (ORDER_FILE + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, p)

    def _sorted(self, d, items, kind):
        """Eigene Reihenfolge zuerst, alles Übrige natürlich sortiert dahinter."""
        idx = {n: i for i, n in enumerate(self._order(d).get(kind) or [])}
        return sorted(items, key=lambda x: (0, idx[x.name]) if x.name in idx else (1, _natural(x.name)))

    def _audio_files(self, d):
        return self._sorted(d, [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in AUDIO], "tracks")

    def _subdirs(self, d):
        return self._sorted(d, [y for y in d.iterdir() if y.is_dir() and not y.name.startswith(".")], "folders")

    def set_order(self, folder, kind, names):
        if kind not in ("tracks", "folders"):
            raise LibraryError("Unbekannte Art")
        d = self.path(folder)
        if not d.is_dir():
            raise LibraryError("Ordner nicht gefunden")
        existing = {x.name for x in (self._audio_files(d) if kind == "tracks" else self._subdirs(d))}
        names = [n for n in dict.fromkeys(str(n) for n in names) if n in existing]
        data = self._order(d)
        data[kind] = names
        self._write_order(d, data)

    def _rename_in_order(self, d, kind, old, new):
        data = self._order(d)
        if old in (data.get(kind) or []):
            data[kind] = [new if n == old else n for n in data[kind]]
            self._write_order(d, data)

    def find_cover(self, d):
        imgs = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in IMAGES and not f.name.startswith(".")]
        if not imgs:
            return None
        imgs.sort(key=lambda f: (f.stem.lower() not in COVER_NAMES, f.name.lower()))
        return imgs[0]

    def folders(self):
        """Alle Ordner in Abspielreihenfolge (Eltern vor Kindern), mit Titelzahlen."""
        out = []

        def walk(d):
            for sub in self._subdirs(d):
                rel = self.rel(sub)
                files = [f for f in sub.iterdir() if f.is_file()]
                tracks = [f for f in files if f.suffix.lower() in AUDIO]
                img = self.find_cover(sub)
                if not self.is_category(rel):
                    out.append({
                        "name": rel,
                        "tracks": len(tracks),
                        "size": sum(f.stat().st_size for f in tracks),
                        "cover": True,
                        "cover_v": int(img.stat().st_mtime) if img else 0,
                        "custom_cover": bool(img and img.stem.lower() == "cover"),
                        "figure_v": self._figure_v(sub),
                        "figure_s": self._figure_s(sub),
                        "subfolders": sum(1 for y in sub.iterdir() if y.is_dir() and not y.name.startswith(".")),
                    })
                walk(sub)

        walk(self.root)
        for f in out:  # Titel einschließlich Unterordnern
            pre = f["name"] + "/"
            f["total_tracks"] = f["tracks"] + sum(g["tracks"] for g in out if g["name"].startswith(pre))
        return out

    def signature(self):
        """Billiger Fingerabdruck des Musikordners: Anzahl, Gesamtgröße, jüngste Änderung."""
        n = size = 0
        newest = 0.0
        for base, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            newest = max(newest, os.stat(base).st_mtime)
            for f in files:
                if f.startswith("."):
                    continue
                try:
                    st = os.stat(os.path.join(base, f))
                except OSError:
                    continue
                n, size, newest = n + 1, size + st.st_size, max(newest, st.st_mtime)
        return n, size, newest

    def all_files(self, folder):
        """Alle Titel eines Ordners samt Unterordnern in natürlicher Reihenfolge."""
        d = self.path(folder)
        out = []

        def walk(x):
            out.extend(self.rel(f) for f in self._audio_files(x))
            for sub in self._subdirs(x):
                if sub.name != REVIEW:      # Dateien zum Prüfen laufen nicht mit der Serie mit
                    walk(sub)

        walk(d)
        return out

    def tracks(self, folder):
        d = self.path(folder)
        return [{"file": f.name, "size": f.stat().st_size} for f in self._audio_files(d)]

    def create(self, name):
        p = self.path(name, must_exist=False)
        if p == self.root:
            raise LibraryError("Ungültiger Name")
        p.mkdir(parents=True, exist_ok=True)
        return self.rel(p)

    def rename(self, folder, new_name):
        src = self.path(folder)
        if src == self.root or not src.is_dir():
            raise LibraryError("Ordner nicht gefunden")
        if self.is_category(self.rel(src)):
            raise LibraryError("„Hörspiele“ und „Musik“ können nicht umbenannt werden")
        dst = src.parent / clean_name(new_name)
        if dst.exists():
            raise LibraryError("Diesen Namen gibt es schon")
        src.rename(dst)
        self._rename_in_order(src.parent, "folders", src.name, dst.name)
        return self.rel(dst)

    def delete_folder(self, folder):
        p = self.path(folder)
        if p == self.root or not p.is_dir():
            raise LibraryError("Ordner nicht gefunden")
        if self.is_category(self.rel(p)):
            raise LibraryError("„Hörspiele“ und „Musik“ können nicht gelöscht werden")
        shutil.rmtree(p)

    def move(self, folder, parent):
        """Verschiebt einen Ordner in einen anderen (z. B. Folge in eine Serie)."""
        src = self.path(folder)
        if src == self.root or not src.is_dir() or self.is_category(self.rel(src)):
            raise LibraryError("Dieser Ordner kann nicht verschoben werden")
        dst_parent = self.path(parent)
        if not dst_parent.is_dir() or dst_parent == self.root:
            raise LibraryError("Ziel nicht gefunden")
        if dst_parent == src or src in dst_parent.parents:
            raise LibraryError("Ein Ordner kann nicht in sich selbst verschoben werden")
        dst = dst_parent / src.name
        if dst == src:
            return self.rel(src)
        if dst.exists():
            raise LibraryError("Im Ziel gibt es schon einen Ordner mit diesem Namen")
        src.rename(dst)
        return self.rel(dst)

    def delete_file(self, folder, file):
        p = self.path(folder) / clean_name(file)
        if not p.is_file():
            raise LibraryError("Datei nicht gefunden")
        p.unlink()

    def rename_file(self, folder, file, new_name):
        src = self.path(folder) / clean_name(file)
        if not src.is_file():
            raise LibraryError("Datei nicht gefunden")
        new_name = clean_name(new_name)
        if not Path(new_name).suffix:
            new_name += src.suffix
        dst = src.parent / new_name
        if dst.exists():
            raise LibraryError("Diesen Namen gibt es schon")
        src.rename(dst)
        self._rename_in_order(src.parent, "tracks", src.name, dst.name)

    def upload_target(self, folder, filename, episode=False):
        """Zielpfad für einen Upload; erlaubt Audio- und Bilddateien.
        episode=True: jede Audiodatei bekommt einen eigenen Unterordner (eine Folge),
        benannt nach dem Dateinamen ohne Endung. Liefert (Pfad, neu_angelegter_Ordner)."""
        d = self.path(folder)
        if not d.is_dir() or d == self.root:
            raise LibraryError("Ordner nicht gefunden")
        name = clean_name(Path(str(filename).replace("\\", "/")).name)
        suffix = Path(name).suffix.lower()
        if suffix not in AUDIO | IMAGES:
            raise LibraryError(f"Dateityp nicht unterstützt: {name}")
        if episode and suffix in AUDIO:
            sub = d / clean_name(Path(name).stem)
            if sub.exists() and not sub.is_dir():
                raise LibraryError(f"„{sub.name}“ gibt es schon als Datei")
            created = not sub.exists()
            sub.mkdir(exist_ok=True)
            return sub / name, created
        return d / name, False

    def cover_bytes(self, folder, _depth=0):
        """Cover als (bytes|Path, mime). Erst Bilddatei, dann eingebettetes Bild,
        bei Serien ohne eigenes Bild das Cover der ersten Folge."""
        d = self.path(folder)
        img = self.find_cover(d)
        if img:
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp"}[img.suffix.lower().lstrip(".")]
            return img, mime
        try:
            import mutagen
        except ImportError:
            return None, None
        for f in self._audio_files(d)[:3]:
            try:
                m = mutagen.File(f)
                if m is None:
                    continue
                if getattr(m, "pictures", None):
                    return m.pictures[0].data, m.pictures[0].mime
                tags = m.tags or {}
                for k in tags.keys():
                    if str(k).startswith("APIC"):
                        return tags[k].data, tags[k].mime
                if "covr" in tags and tags["covr"]:
                    c = tags["covr"][0]
                    return bytes(c), "image/png" if getattr(c, "imageformat", 13) == 14 else "image/jpeg"
            except Exception:
                continue
        if _depth < 3:
            for sub in self._subdirs(d)[:3]:
                data, mime = self.cover_bytes(self.rel(sub), _depth + 1)
                if data is not None:
                    return data, mime
        return None, None

    def set_cover(self, folder, data, suffix):
        d = self.path(folder)
        suffix = suffix.lower()
        if suffix not in IMAGES:
            raise LibraryError("Bitte ein JPG-, PNG- oder WebP-Bild wählen")
        for f in d.iterdir():
            if f.is_file() and f.stem.lower() == "cover" and f.suffix.lower() in IMAGES:
                f.unlink()
        (d / f"cover{suffix}").write_bytes(data)

    # ---------- 3D-Figur (aus einer STL im Browser gerendert, als PNG gespeichert) ----------
    @staticmethod
    def _figure_v(d):
        f = d / FIGURE
        try:
            return int(f.stat().st_mtime_ns // 1_000_000)
        except OSError:
            return 0

    @staticmethod
    def _figure_s(d):
        try:
            v = float(json.loads((d / FIGSIZE).read_text()).get("scale", 1))
        except (OSError, ValueError, AttributeError, TypeError):
            return 1.0
        return round(min(FIG_MAX, max(FIG_MIN, v)), 2)

    def set_figure_scale(self, folder, scale):
        d = self.path(folder)
        if not (d / FIGURE).is_file():
            raise LibraryError("Der Ordner hat keine Figur")
        scale = round(min(FIG_MAX, max(FIG_MIN, float(scale))), 2)
        if scale == 1:
            (d / FIGSIZE).unlink(missing_ok=True)
        else:
            (d / FIGSIZE).write_text(json.dumps({"scale": scale}))
        return scale

    def figure_file(self, folder):
        f = self.path(folder) / FIGURE
        return f if f.is_file() else None

    def figure_for(self, folder):
        """Figur des Ordners oder des nächsten übergeordneten (eine Folge zeigt die Figur der Serie)."""
        if not folder:
            return None, 0, 1.0
        parts = str(folder).split("/")
        while len(parts) > 1:
            rel = "/".join(parts)
            try:
                v = self._figure_v(self.path(rel))
            except LibraryError:
                v = 0
            if v:
                return rel, v, self._figure_s(self.path(rel))
            parts.pop()
        return None, 0, 1.0

    def set_figure(self, folder, data):
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise LibraryError("Die Figur muss als PNG ankommen")
        d = self.path(folder)
        if d == self.root or self.is_category(folder):
            raise LibraryError("Ordner nicht gefunden")
        tmp = d / (FIGURE + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, d / FIGURE)

    def remove_figure(self, folder):
        d = self.path(folder)
        (d / FIGURE).unlink(missing_ok=True)
        (d / FIGSIZE).unlink(missing_ok=True)

    def search_tracks(self, query, category=None, limit=60):
        """Titel, deren Name alle Suchwörter enthält (Ordnerpfad zählt mit)."""
        terms = norm(query).split()
        if not terms:
            return []
        base = self.path(category) if category else self.root
        out = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if not x.startswith(".")]
            d = Path(dirpath)
            rel = self.rel(d) if d != self.root else ""
            nrel = norm(rel)
            for f in filenames:
                if Path(f).suffix.lower() not in AUDIO:
                    continue
                nf = norm(Path(f).stem)
                hay = nrel + " " + nf
                if all(t in hay for t in terms) and any(t in nf for t in terms):
                    out.append({"file": f, "folder": rel})
                    if len(out) >= limit:
                        return out
        out.sort(key=lambda x: (_natural(x["folder"]), _natural(x["file"])))
        return out

    def remove_cover(self, folder):
        d = self.path(folder)
        for f in d.iterdir():
            if f.is_file() and f.stem.lower() == "cover" and f.suffix.lower() in IMAGES:
                f.unlink()

    def _dir_size(self, d):
        total = 0
        for dirpath, _, filenames in os.walk(d):
            for f in filenames:
                try:
                    total += os.lstat(os.path.join(dirpath, f)).st_size
                except OSError:
                    pass
        return total

    def usage(self):
        """Speicheraufteilung: System, Hörspiele, Musik, frei (in Bytes)."""
        u = shutil.disk_usage(self.root)
        sizes = {c: self._dir_size(self.root / c) for c in CATEGORIES}
        other = self._dir_size(self.root) - sum(sizes.values())  # Ordner ohne Kategorie zählen zu Hörspiele
        h, m = sizes["Hörspiele"] + max(0, other), sizes["Musik"]
        return {"total": u.total, "free": u.free, "hoerspiele": h, "musik": m,
                "system": max(0, u.total - u.free - h - m)}

    def disk(self):
        u = shutil.disk_usage(self.root)
        return {"total": u.total, "free": u.free}
