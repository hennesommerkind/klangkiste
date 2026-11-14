"""Musikverwaltung: Ordner, Titel, Cover, Uploads."""
import os
import re
import shutil
from pathlib import Path

AUDIO = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav", ".wma"}
IMAGES = {".jpg", ".jpeg", ".png", ".webp"}
COVER_NAMES = ("cover", "folder", "front", "album")


class LibraryError(ValueError):
    pass


def clean_name(name):
    """Prüft einen einzelnen Datei- oder Ordnernamen."""
    name = re.sub(r"[\x00-\x1f]", "", str(name or "")).strip().strip(".").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise LibraryError("Ungültiger Name")
    return name[:150]


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


class Library:
    def __init__(self, root):
        self.root = Path(root).resolve()

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

    def _audio_files(self, d):
        return sorted((f for f in d.iterdir() if f.is_file() and f.suffix.lower() in AUDIO),
                      key=lambda f: _natural(f.name))

    def find_cover(self, d):
        imgs = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in IMAGES]
        if not imgs:
            return None
        imgs.sort(key=lambda f: (f.stem.lower() not in COVER_NAMES, f.name.lower()))
        return imgs[0]

    def folders(self):
        """Alle Ordner (auch leere und verschachtelte), mit Anzahl direkter Titel."""
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            d = Path(dirpath)
            if d == self.root:
                continue
            tracks = [f for f in filenames if Path(f).suffix.lower() in AUDIO]
            size = sum((d / f).stat().st_size for f in tracks)
            out.append({
                "name": self.rel(d),
                "tracks": len(tracks),
                "size": size,
                "cover": self.find_cover(d) is not None or bool(tracks),
                "subfolders": len(dirnames),
            })
        out.sort(key=lambda f: _natural(f["name"]))
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
        dst = src.parent / clean_name(new_name)
        if dst.exists():
            raise LibraryError("Diesen Namen gibt es schon")
        src.rename(dst)
        return self.rel(dst)

    def delete_folder(self, folder):
        p = self.path(folder)
        if p == self.root or not p.is_dir():
            raise LibraryError("Ordner nicht gefunden")
        shutil.rmtree(p)

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

    def upload_target(self, folder, filename):
        """Zielpfad für einen Upload; erlaubt Audio- und Bilddateien."""
        d = self.path(folder)
        if not d.is_dir() or d == self.root:
            raise LibraryError("Ordner nicht gefunden")
        name = clean_name(Path(str(filename).replace("\\", "/")).name)
        if Path(name).suffix.lower() not in AUDIO | IMAGES:
            raise LibraryError(f"Dateityp nicht unterstützt: {name}")
        return d / name

    def cover_bytes(self, folder):
        """Cover als (bytes|Path, mime). Erst Bilddatei, dann eingebettetes Bild."""
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

    def disk(self):
        u = shutil.disk_usage(self.root)
        return {"total": u.total, "free": u.free}
