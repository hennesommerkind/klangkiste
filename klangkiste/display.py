"""Kleines Bild für externe Displays (z. B. ESP32 mit ESPHome): Cover mit Figur darauf,
fertig zusammengesetzt, damit der Mikrocontroller nur noch ein JPEG anzeigen muss."""
import hashlib
import io
import zlib
from pathlib import Path

from .library import LibraryError

_cache = {}


def image_version(library, folder, spotify=False):
    """Kurze Kennung, die sich ändert, sobald sich das Bild ändern würde."""
    if spotify:
        return "spotify"
    if not folder:
        return "idle"
    parts = [folder]
    try:
        img = library.find_cover(library.path(folder))
        parts.append(str(int(img.stat().st_mtime)) if img else "-")
    except (LibraryError, OSError):
        parts.append("?")
    fig, v, sc = library.figure_for(folder)
    parts += [str(fig), str(v), str(sc)]
    return format(zlib.crc32("|".join(parts).encode()), "08x")


def _hex(c, default=(10, 25, 41)):
    try:
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return default


def _placeholder(Image, ImageDraw, size, name, spotify=False):
    if spotify:
        col = (30, 185, 84)
    else:
        h = hashlib.md5((name or "").encode()).digest()
        palette = [(201, 79, 61), (224, 163, 58), (79, 143, 91), (61, 116, 184), (164, 107, 184), (91, 107, 115)]
        col = palette[h[0] % len(palette)]
    im = Image.new("RGB", (size, size), col)
    d = ImageDraw.Draw(im)
    # schlichte Note als Symbol
    s = size / 100
    w = (255, 255, 255)
    d.ellipse((30 * s, 60 * s, 46 * s, 74 * s), fill=w)
    d.ellipse((58 * s, 54 * s, 74 * s, 68 * s), fill=w)
    d.rectangle((42 * s, 28 * s, 46 * s, 68 * s), fill=w)
    d.rectangle((70 * s, 22 * s, 74 * s, 62 * s), fill=w)
    d.polygon([(42 * s, 28 * s), (74 * s, 22 * s), (74 * s, 30 * s), (42 * s, 36 * s)], fill=w)
    return im


def compose(library, folder, size=120, bg="0A1929", spotify=False, name=""):
    """JPEG: Cover (abgerundet) links oben, Figur rechts unten halb darüber – wie in der Weboberfläche."""
    from PIL import Image, ImageDraw, ImageOps

    size = max(48, min(320, int(size)))
    key = (image_version(library, folder, spotify), size, bg)
    if key in _cache:
        return _cache[key]

    bgc = _hex(bg)
    canvas = Image.new("RGB", (size, size), bgc)
    c = round(size * 0.86)

    cover = None
    if folder and not spotify:
        try:
            data, _ = library.cover_bytes(folder)
            if data is not None:
                src = io.BytesIO(data.read_bytes() if isinstance(data, Path) else data)
                cover = ImageOps.fit(Image.open(src).convert("RGB"), (c, c), Image.LANCZOS)
        except Exception:
            cover = None
    if cover is None:
        cover = _placeholder(Image, ImageDraw, c, folder or name, spotify)
    mask = Image.new("L", (c, c), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, c - 1, c - 1), radius=round(c * 0.08), fill=255)
    canvas.paste(cover, (0, 0), mask)

    if folder and not spotify:
        fig, v, sc = library.figure_for(folder)
        f = library.figure_file(fig) if fig else None
        if f:
            try:
                im = Image.open(f).convert("RGBA")
                bbox = im.getbbox()
                if bbox:
                    im = im.crop(bbox)
                h_max, w_max = c * 0.74 * sc, c * 0.64 * sc
                r = min(h_max / im.height, w_max / im.width)
                im = im.resize((max(1, round(im.width * r)), max(1, round(im.height * r))), Image.LANCZOS)
                right, bottom = min(size, round(c * 1.09)), min(size, round(c * 1.05))
                x, y = max(0, right - im.width), max(0, bottom - im.height)
                # leichter Schatten
                sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
                sh.putalpha(im.getchannel("A").point(lambda a: a * 0.35))
                canvas.paste((0, 0, 0), (x + max(1, size // 60), y + max(1, size // 40)), sh.getchannel("A"))
                canvas.paste(im, (x, y), im)
            except Exception:
                pass

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=88)
    data = out.getvalue()
    if len(_cache) > 20:
        _cache.clear()
    _cache[key] = data
    return data


def browse(library, current, pos=None, step=0):
    """Ein Eintrag zum Blättern am Display: die Geschwister des laufenden Ordners
    (Folgen einer Serie, sonst die Ordner derselben Ebene), in der Reihenfolge der Oberfläche."""
    current = (current or "").strip("/")
    parent = current.rsplit("/", 1)[0] if "/" in current else "Hörspiele"
    try:
        names = [d.name for d in library._subdirs(library.path(parent))]
    except (LibraryError, OSError):
        names = []
    if not names:
        return {"pos": 0, "count": 0, "name": "", "folder": "", "sub": "", "top": ""}
    leaf = current.rsplit("/", 1)[-1]
    cur = names.index(leaf) if leaf in names and "/" in current else 0
    i = (cur + int(step)) if pos is None else int(pos)
    i %= len(names)
    series = parent.count("/") >= 1
    return {"pos": i, "count": len(names), "current": cur, "name": names[i], "folder": f"{parent}/{names[i]}",
            "top": parent.split("/")[-1],
            "sub": f"{'Folge ' if series else ''}{i + 1} von {len(names)}"}
