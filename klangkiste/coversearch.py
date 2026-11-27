"""Fehlende Titelbilder im Netz suchen.

Gefragt werden zwei offene Kataloge ohne Schlüssel: der iTunes-Katalog von Apple und Deezer. Die Box
sucht selbst (die Kataloge erlauben keine Abfrage direkt aus dem Browser) und lädt das gewählte Bild
nur von deren Bild-Servern – keine beliebigen Adressen.
"""
import asyncio
import io
import logging
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger("klangkiste.cover")

UA = "Klangkiste/1.0 (+https://github.com/hennesommerkind/klangkiste)"
TIMEOUT = aiohttp.ClientTimeout(total=10, sock_connect=5)
MAX_BYTES = 12_000_000
SIZE = 800                       # wie beim Hochladen in der Oberfläche: quadratisch, 800 px
# Bilder nur von den Servern der Kataloge laden
HOSTS = (".mzstatic.com", ".dzcdn.net")
ITUNES = "https://itunes.apple.com/search"
DEEZER = "https://api.deezer.com/search/album"


async def _itunes(session, q):
    params = {"term": q, "media": "music", "entity": "album", "country": "DE", "limit": "16"}
    async with session.get(ITUNES, params=params) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    out = []
    for it in data.get("results", []):
        art = it.get("artworkUrl100") or ""
        if "100x100" not in art:
            continue
        out.append({"source": "Apple", "title": it.get("collectionName") or "", "artist": it.get("artistName") or "",
                    "thumb": art.replace("100x100", "300x300"), "url": art.replace("100x100", "1000x1000")})
    return out


async def _deezer(session, q):
    async with session.get(DEEZER, params={"q": q, "limit": "16"}) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    if isinstance(data.get("error"), dict):
        raise RuntimeError(data["error"].get("message") or "Deezer-Fehler")
    out = []
    for it in data.get("data", []):
        full = it.get("cover_xl") or it.get("cover_big")
        if not full or "/images/cover//" in full:     # Deezer liefert für Alben ohne Bild einen Platzhalter
            continue
        out.append({"source": "Deezer", "title": it.get("title") or "", "artist": (it.get("artist") or {}).get("name") or "",
                    "thumb": it.get("cover_medium") or full, "url": full})
    return out


async def search(q):
    """Treffer beider Kataloge, abwechselnd gemischt. Fällt einer aus, kommen die des anderen."""
    q = " ".join(str(q or "").split())[:120]
    if not q:
        raise ValueError("Bitte einen Suchbegriff eingeben")
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers={"User-Agent": UA}) as s:
        results = await asyncio.gather(_itunes(s, q), _deezer(s, q), return_exceptions=True)
    lists, failed = [], []
    for name, r in zip(("Apple", "Deezer"), results):
        if isinstance(r, Exception):
            log.warning("Cover-Suche %s: %s", name, r)
            failed.append(name)
        else:
            lists.append(r)
    if len(failed) == 2:
        raise ConnectionError("Die Box erreicht die Bilderkataloge gerade nicht – ist sie mit dem Internet verbunden?")
    merged, seen = [], set()
    for i in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if i < len(x) and x[i]["url"] not in seen:
                seen.add(x[i]["url"])
                merged.append(x[i])
    return {"results": merged, "failed": failed}


def allowed(url):
    p = urlparse(str(url or ""))
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host.endswith(h) for h in HOSTS)


def square_jpeg(data, size=SIZE):
    """Mittig quadratisch zuschneiden, auf höchstens size px verkleinern, als JPEG."""
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB")
    side = min(im.size)
    left, top = (im.width - side) // 2, (im.height - side) // 2
    im = im.crop((left, top, left + side, top + side))
    if side > size:
        im = im.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=86, optimize=True)
    return buf.getvalue()


async def download(url):
    if not allowed(url):
        raise ValueError("Bilder werden nur aus den Suchergebnissen übernommen")
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT, headers={"User-Agent": UA}) as s:
            async with s.get(url) as r:
                r.raise_for_status()
                if not (r.content_type or "").startswith("image/"):
                    raise ValueError("Unter dieser Adresse liegt kein Bild")
                data = await r.content.read(MAX_BYTES + 1)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("Cover laden %s: %s", url, e)
        raise ConnectionError("Das Bild konnte nicht geladen werden – bitte ein anderes wählen") from e
    if len(data) > MAX_BYTES:
        raise ValueError("Das Bild ist zu groß")
    try:
        return await asyncio.to_thread(square_jpeg, data)
    except ImportError:              # ohne Pillow das Bild unverändert nehmen
        return data
    except Exception as e:
        raise ValueError("Das Bild ist beschädigt – bitte ein anderes wählen") from e
