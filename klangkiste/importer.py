"""Übernahme von Musik und Karten einer alten Phoniebox (Version 2.x).

Erwartet ein tar-Archiv, das die Ordner `shortcuts` und `audiofolders`
aus `RPi-Jukebox-RFID/shared` enthält.
"""
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from .library import AUDIO, IMAGES, clean_name, LibraryError


def import_legacy(archive_path, store):
    music = Path(store.music).resolve()
    stats = {"files": 0, "cards": 0, "skipped": 0, "missing": []}
    shortcuts = {}
    with tarfile.open(archive_path, mode="r:*") as tar:
        for m in tar:
            if not m.isfile():
                continue
            parts = [p for p in PurePosixPath(m.name).parts if p not in (".", "/")]
            if "audiofolders" in parts:
                rel = parts[parts.index("audiofolders") + 1:]
                if not rel or Path(rel[-1]).suffix.lower() not in AUDIO | IMAGES:
                    stats["skipped"] += 1
                    continue
                try:
                    rel = [clean_name(p) for p in rel]
                except LibraryError:
                    stats["skipped"] += 1
                    continue
                if len(rel) == 1:  # lose Datei ohne Ordner
                    rel = ["Ohne Ordner", rel[0]]
                dest = music.joinpath(*rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                stats["files"] += 1
            elif "shortcuts" in parts:
                rel = parts[parts.index("shortcuts") + 1:]
                if len(rel) != 1 or rel[0].startswith(".") or m.size > 4096:
                    continue
                target = tar.extractfile(m).read().decode("utf-8", "replace").strip()
                if target and target != rel[0]:  # Platzhalter (ID = Inhalt) überspringen
                    shortcuts[rel[0]] = target.strip("/")
    for card_id, folder in shortcuts.items():
        if not (music / folder).is_dir():
            stats["missing"].append(folder)
        store.cards[card_id] = {"type": "folder", "target": folder, "shuffle": False, "legacy": True}
        stats["cards"] += 1
    store.save_cards()
    return stats
