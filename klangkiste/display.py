"""Kleines Bild für externe Displays (z. B. ESP32 mit ESPHome): Cover mit Figur darauf,
fertig zusammengesetzt, damit der Mikrocontroller nur noch ein JPEG anzeigen muss."""
import hashlib
import io
import zlib
from pathlib import Path

from .library import REVIEW, LibraryError

_cache = {}
_badges = {}
_sigs = {}
ACCENT = (255, 112, 67)


def _cover_sig(library, d):
    """Kennung des Titelbilds eines Ordners – gleiche Bilder (z. B. vom selben Album) ergeben dieselbe."""
    try:
        data, _ = library.cover_bytes(library.rel(d), _depth=3)
    except Exception:
        return None
    if data is None:
        return None
    try:
        if isinstance(data, Path):
            with open(data, "rb") as f:
                data = f.read(65536)
        return zlib.crc32(bytes(data[:65536]))
    except OSError:
        return None


def badge(library, folder):
    """Nummer, die ins Bild einer Folge gehört – oder 0. Folgen bekommen eine Nummer, wenn sie kein
    eigenes Bild haben oder dasselbe wie eine andere Folge der Serie (sonst wären sie am Display
    nicht zu unterscheiden)."""
    folder = (folder or "").strip("/")
    if folder.count("/") < 2:
        return 0
    parent, leaf = folder.rsplit("/", 1)
    try:
        dirs = [d for d in library._subdirs(library.path(parent)) if d.name != REVIEW]
        stamp = tuple((d.name, d.stat().st_mtime_ns) for d in dirs)
    except (LibraryError, OSError):
        return 0
    if len(dirs) < 2:
        return 0
    hit = _badges.get(parent)
    if not hit or hit[0] != stamp:
        sigs = []
        for d, (_, mtime) in zip(dirs, stamp):      # nur geänderte Ordner neu lesen (Tags lesen kostet auf dem Pi Zeit)
            old = _sigs.get(str(d))
            if not old or old[0] != mtime:
                if len(_sigs) > 2000:
                    _sigs.clear()
                old = _sigs[str(d)] = (mtime, _cover_sig(library, d))
            sigs.append(old[1])
        nums = {d.name: i + 1 for i, (d, sig) in enumerate(zip(dirs, sigs)) if sig is None or sigs.count(sig) > 1}
        if len(_badges) > 40:
            _badges.clear()
        hit = _badges[parent] = (stamp, nums)
    return hit[1].get(leaf, 0)


def _font(size):
    from PIL import ImageFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size)      # Pillow ab 10.1
    except TypeError:
        return ImageFont.load_default()


def _draw_badge(Image, ImageDraw, canvas, c, number, bgc, right=False):
    """Runde Marke mit der Folgennummer oben links auf dem Cover (right: oben rechts)."""
    text = str(number)
    r = round(c * (0.19 if len(text) < 3 else 0.22))
    k = 3                                         # dreifach groß zeichnen und verkleinern: glatte Kanten
    m = Image.new("RGBA", (2 * r * k, 2 * r * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(m)
    ring = tuple(min(255, v + 18) for v in bgc)   # nicht genau die Hintergrundfarbe: die gilt im Karussell als durchsichtig
    d.ellipse((0, 0, 2 * r * k - 1, 2 * r * k - 1), fill=ring + (255,))
    e = round(r * k * 0.12)
    d.ellipse((e, e, 2 * r * k - 1 - e, 2 * r * k - 1 - e), fill=ACCENT + (255,))
    font = _font(round(r * k * (1.25 if len(text) == 1 else 1.0 if len(text) == 2 else 0.78)))
    try:
        d.text((r * k, r * k), text, font=font, fill=(255, 255, 255, 255), anchor="mm")
    except (ValueError, TypeError):               # Ersatzschrift ohne Ankerpunkte
        d.text((r * k * 0.6, r * k * 0.6), text, font=font, fill=(255, 255, 255, 255))
    m = m.resize((2 * r, 2 * r), Image.LANCZOS)
    off = round(c * 0.03)
    canvas.paste(m, (c - 2 * r - off if right else off, off), m)


def _idle(Image, ImageDraw, size, bgc):
    """Ruhebild: eine schlafende Eule (passend zum Gehäuse), solange keine Figur aufliegt."""
    k = 3
    n = size * k
    im = Image.new("RGB", (n, n), bgc)
    d = ImageDraw.Draw(im)
    u = n / 100
    body, belly, dark, beak = (38, 70, 83), (52, 96, 110), (16, 36, 50), ACCENT
    d.polygon([(22 * u, 30 * u), (27 * u, 8 * u), (44 * u, 24 * u)], fill=body)      # Federohren
    d.polygon([(78 * u, 30 * u), (73 * u, 8 * u), (56 * u, 24 * u)], fill=body)
    d.ellipse((16 * u, 16 * u, 84 * u, 94 * u), fill=body)                           # Körper
    d.ellipse((30 * u, 52 * u, 70 * u, 92 * u), fill=belly)
    for cx in (37, 63):                                                              # geschlossene Augen
        d.ellipse(((cx - 14) * u, 30 * u, (cx + 14) * u, 58 * u), fill=belly)
        d.arc(((cx - 8) * u, 36 * u, (cx + 8) * u, 52 * u), 20, 160, fill=dark, width=max(2, round(2.4 * u)))
    d.polygon([(46 * u, 52 * u), (54 * u, 52 * u), (50 * u, 62 * u)], fill=beak)      # Schnabel
    for x, y, r in ((84, 20, 3.2), (90, 11, 2.2), (95, 4, 1.4)):                     # Schlaf-Pünktchen
        d.ellipse(((x - r) * u, (y - r) * u, (x + r) * u, (y + r) * u), fill=belly)
    return im.resize((size, size), Image.LANCZOS)


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
    parts += [str(fig), str(v), str(sc), str(badge(library, folder))]
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


def _render(library, folder, size, bg, spotify=False, name="", figure=True, idle=False, badge_right=False):
    """Bild (PIL): Cover (abgerundet) links oben, Figur rechts unten halb darüber – wie in der Weboberfläche."""
    from PIL import Image, ImageDraw, ImageOps

    size = max(24, min(320, int(size)))
    bgc = _hex(bg)
    if idle:
        key = ("ruhe", size, bg)
        if key not in _cache:
            _cache[key] = _idle(Image, ImageDraw, size, bgc)
        return _cache[key]
    key = (image_version(library, folder, spotify), size, bg, figure, badge_right)
    if key in _cache:
        return _cache[key]

    canvas = Image.new("RGB", (size, size), bgc)
    c = round(size * 0.86) if figure else size     # ohne Figur füllt das Cover die ganze Kachel

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
    number = badge(library, folder) if folder and not spotify else 0
    if number:
        _draw_badge(Image, ImageDraw, canvas, c, number, bgc, badge_right)

    if figure and folder and not spotify:
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


def compose(library, folder, size=120, bg="0A1929", spotify=False, name="", idle=False):
    return _jpeg(_render(library, folder, size, bg, spotify, name, idle=idle))


STRIP_W = 320
_strips = {}


def _layout(h, w=STRIP_W, count=5):
    """(Versatz, x, Kantenlänge) der Kacheln für Bildhöhe h und -breite w – von außen nach innen
    gezeichnet, die Mitte liegt obenauf. count = 5 (je zwei Nachbarn) oder 3 (je einer, dafür größer)."""
    c = h - 4
    cx = (w - c) // 2
    if count <= 3:
        n = round(h * 0.76)
        nl = max(0, cx - round(n * 0.62))
        return ((-1, nl, n), (1, w - nl - n, n), (0, cx, c))
    n, f = round(h * 0.7), round(h * 0.46)
    nl = cx - round(n * 0.68)
    fl = max(0, nl - round(f * 0.5))
    return ((-2, fl, f), (2, w - fl - f, f), (-1, nl, n), (1, w - nl - n, n), (0, cx, c))


def _level(library, current, up=False):
    """(Elternordner, Namen der Einträge, Index des laufenden) für die Ebene, in der geblättert wird."""
    current = (current or "").strip("/")
    if up and current.count("/") >= 2:          # eine Ebene höher: Serie statt Folge
        current = current.rsplit("/", 1)[0]
    if not up and current.count("/") < 2:       # einzelnes Hörspiel oder Album: nichts zu blättern
        return current.rsplit("/", 1)[0] if "/" in current else "", [], 0
    parent = current.rsplit("/", 1)[0] if "/" in current else "Hörspiele"
    try:
        names = [d.name for d in library._subdirs(library.path(parent)) if d.name != REVIEW]
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


def strip(library, current, pos=None, bg="0A1929", spotify=False, name="", up=False, h=100, w=STRIP_W, count=5,
          figure=False, idle=False):
    """Karussell als ein JPEG (320 × h): in der Mitte groß der gewählte Eintrag, dahinter links und
    rechts je zwei Nachbarn, kleiner und leicht abgedunkelt. Ohne pos steht der laufende Ordner in der Mitte.
    Die Höhe bestimmt das Display – ein ESP32 ohne Zusatzspeicher schafft etwa 100 bis 120."""
    from PIL import Image

    h = max(60, min(160, int(h)))
    bgc = _hex(bg)
    w = max(120, min(STRIP_W, int(w)))
    layout = _layout(h, w, int(count))
    parent, names, cur = ("", [], 0) if spotify or idle or not current else _level(library, current, up)
    n = len(names)
    i = (cur if pos is None else int(pos)) % n if n else 0
    shown = [] if not n else [(off, x, size, f"{parent}/{names[(i + off) % n]}") for off, x, size in layout
                              if not off or abs(off) <= (n - 1) // 2 + (1 if off > 0 and n % 2 == 0 else 0)]
    key = (bg, h, w, int(count), figure, spotify, idle, current if not n else None, name if spotify else "",
           tuple((off, f, image_version(library, f)) for off, _, _, f in shown))
    data = _strips.get(key)
    if data is None:
        canvas = Image.new("RGB", (w, h), bgc)
        if not n:
            c = layout[-1][2]
            canvas.paste(_render(library, None if spotify else current, c, bg, spotify, name, figure, idle),
                         (layout[-1][1], 2))
        for off, x, size, folder in shown:
            # rechte Nachbarn tragen die Nummer rechts oben – links läge sie hinter dem mittleren Bild
            tile = _render(library, folder, size, bg, figure=figure, badge_right=off > 0)
            if off:
                tile = Image.blend(tile, Image.new("RGB", tile.size, bgc), 0.15 if abs(off) == 1 else 0.3)
            # nur was sich vom Hintergrund abhebt, verdeckt die Nachbarn dahinter
            canvas.paste(tile, (x, (h - size) // 2), _diff_mask(tile, bgc))
        data = _jpeg(canvas, 78)
        if len(_strips) > 8:
            _strips.clear()
        _strips[key] = data
    return data


def _diff_mask(tile, bgc):
    """Maske der Bildpunkte, die sich vom Hintergrund unterscheiden (Figur, die über das Cover hinausragt)."""
    from PIL import Image, ImageChops
    d = ImageChops.difference(tile, Image.new("RGB", tile.size, bgc)).convert("L")
    return d.point(lambda v: 255 if v > 10 else 0)
