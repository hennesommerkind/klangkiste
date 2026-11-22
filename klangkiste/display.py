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


def _render(library, folder, size, bg, spotify=False, name=""):
    """Bild (PIL): Cover (abgerundet) links oben, Figur rechts unten halb darüber – wie in der Weboberfläche."""
    from PIL import Image, ImageDraw, ImageOps

    size = max(24, min(320, int(size)))
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
                im = Image.open(src)
                if im.mode in ("RGBA", "LA", "P"):      # durchsichtige Cover: Hintergrund des Displays scheint durch
                    im = im.convert("RGBA")
                    flat = Image.new("RGB", im.size, bgc)
                    flat.paste(im, (0, 0), im)
                    im = flat
                cover = ImageOps.fit(im.convert("RGB"), (c, c), Image.LANCZOS)
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

    if len(_cache) > 120:
        _cache.clear()
    _cache[key] = canvas
    return canvas


def _jpeg(im, quality=88):
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality)
    return out.getvalue()


def compose(library, folder, size=120, bg="0A1929", spotify=False, name=""):
    return _jpeg(_render(library, folder, size, bg, spotify, name))


STRIP_W, STRIP_H = 320, 132
# (Versatz, x, Kantenlänge) – von außen nach innen gezeichnet, die Mitte liegt obenauf
STRIP = ((-2, 0, 60), (2, 260, 60), (-1, 30, 92), (1, 198, 92), (0, 96, 128))
_strips = {}


def _level(library, current, up=False):
    """(Elternordner, Namen der Einträge, Index des laufenden) für die Ebene, in der geblättert wird."""
    current = (current or "").strip("/")
    if up and current.count("/") >= 2:          # eine Ebene höher: Serie statt Folge
        current = current.rsplit("/", 1)[0]
    parent = current.rsplit("/", 1)[0] if "/" in current else "Hörspiele"
    try:
        names = [d.name for d in library._subdirs(library.path(parent))]
    except (LibraryError, OSError):
        names = []
    leaf = current.rsplit("/", 1)[-1]
    return parent, names, (names.index(leaf) if leaf in names and "/" in current else 0)


def browse(library, current, pos=None, step=0, up=False):
    """Ein Eintrag zum Blättern am Display: die Geschwister des laufenden Ordners – bei einer Serie
    ihre Folgen, sonst (oder mit up) die Ordner der Ebene darüber. Reihenfolge wie in der Oberfläche."""
    parent, names, cur = _level(library, current, up)
    if not names:
        return {"pos": 0, "count": 0, "name": "", "folder": "", "sub": "", "top": ""}
    i = ((cur + int(step)) if pos is None else int(pos)) % len(names)
    return {"pos": i, "count": len(names), "current": cur, "name": names[i], "folder": f"{parent}/{names[i]}",
            "top": parent.split("/")[-1], "sub": f"{i + 1} / {len(names)}"}


def strip(library, current, pos=None, bg="0A1929", spotify=False, name="", up=False, part=""):
    """Karussell als JPEG (320 × 132): in der Mitte groß der gewählte Eintrag, dahinter links und
    rechts je zwei Nachbarn, kleiner und leicht abgedunkelt. Ohne pos steht der laufende Ordner in der Mitte."""
    from PIL import Image

    bgc = _hex(bg)
    parent, names, cur = ("", [], 0) if spotify or not current else _level(library, current, up)
    n = len(names)
    i = (cur if pos is None else int(pos)) % n if n else 0
    shown = [] if not n else [(off, x, size, f"{parent}/{names[(i + off) % n]}") for off, x, size in STRIP
                              if not off or abs(off) <= (n - 1) // 2 + (1 if off > 0 and n % 2 == 0 else 0)]
    key = (bg, spotify, current if not n else None, name if spotify else "",
           tuple((off, f, image_version(library, f)) for off, _, _, f in shown))
    canvas = _strips.get(key)
    if canvas is None:
        canvas = Image.new("RGB", (STRIP_W, STRIP_H), bgc)
        if not n:
            canvas.paste(_render(library, None if spotify else current, 128, bg, spotify, name), (96, 2))
        for off, x, size, folder in shown:
            tile = _render(library, folder, size, bg)
            if off:
                tile = Image.blend(tile, Image.new("RGB", tile.size, bgc), 0.15 if abs(off) == 1 else 0.3)
            # nur was sich vom Hintergrund abhebt, verdeckt die Nachbarn dahinter
            mask = _diff_mask(tile, bgc)
            canvas.paste(tile, (x, (STRIP_H - size) // 2), mask)
        if len(_strips) > 6:
            _strips.clear()
        _strips[key] = canvas
    if part in ("0", "1", "2", "3"):        # vier Streifen je 80 px: kleine Speicherblöcke für den ESP32
        x = int(part) * STRIP_W // 4
        return _jpeg(canvas.crop((x, 0, x + STRIP_W // 4, STRIP_H)), 68)
    return _jpeg(canvas, 82)


def _diff_mask(tile, bgc):
    """Maske der Bildpunkte, die sich vom Hintergrund unterscheiden (Figur, die über das Cover hinausragt)."""
    from PIL import Image, ImageChops
    d = ImageChops.difference(tile, Image.new("RGB", tile.size, bgc)).convert("L")
    return d.point(lambda v: 255 if v > 10 else 0)
