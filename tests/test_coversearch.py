"""Cover-Suche mit nachgebauten Katalogen (die echten sind aus der Testumgebung nicht erreichbar).

    python3 tests/test_coversearch.py
"""
import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import web  # noqa: E402
from PIL import Image  # noqa: E402

from klangkiste import coversearch as cs  # noqa: E402

ITUNES = {"resultCount": 2, "results": [
    {"wrapperType": "collection", "collectionName": "Folge 100: In Italien", "artistName": "Benjamin Blümchen",
     "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Music/v4/aa/bb/cover.jpg/100x100bb.jpg"},
    {"wrapperType": "collection", "collectionName": "Ohne Bild", "artistName": "X"}]}
DEEZER = {"data": [
    {"title": "In Italien", "artist": {"name": "Benjamin Blümchen"},
     "cover_medium": "https://e-cdns-images.dzcdn.net/images/cover/abc/250x250-000000-80-0-0.jpg",
     "cover_xl": "https://e-cdns-images.dzcdn.net/images/cover/abc/1000x1000-000000-80-0-0.jpg"},
    {"title": "Platzhalter", "artist": {"name": "Y"},
     "cover_medium": "https://e-cdns-images.dzcdn.net/images/cover//250x250-000000-80-0-0.jpg",
     "cover_xl": "https://e-cdns-images.dzcdn.net/images/cover//1000x1000-000000-80-0-0.jpg"}], "total": 2}


def png(w, h):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 40, 40)).save(b, "PNG")
    return b.getvalue()


async def main():
    seen = {}

    async def itunes(r):
        seen["itunes"] = dict(r.query)
        return web.json_response(ITUNES, content_type="text/javascript")   # iTunes antwortet so

    async def deezer(r):
        seen["deezer"] = dict(r.query)
        return web.json_response(DEEZER)

    async def broken(r):
        return web.Response(status=500)

    async def image(r):
        return web.Response(body=png(1400, 1000), content_type="image/png")

    async def notimage(r):
        return web.Response(text="<html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/itunes", itunes)
    app.router.add_get("/deezer", deezer)
    app.router.add_get("/broken", broken)
    app.router.add_get("/img.png", image)
    app.router.add_get("/page", notimage)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8771)
    await site.start()
    base = "http://127.0.0.1:8771"
    try:
        cs.ITUNES, cs.DEEZER = base + "/itunes", base + "/deezer"
        r = await cs.search("  Benjamin   Blümchen In Italien ")
        assert seen["itunes"]["term"] == "Benjamin Blümchen In Italien" and seen["itunes"]["entity"] == "album"
        assert seen["deezer"]["q"] == "Benjamin Blümchen In Italien"
        assert r["failed"] == [] and len(r["results"]) == 2, r          # ohne Bild und Platzhalter fallen weg
        a, d = r["results"]
        assert a["source"] == "Apple" and a["url"].endswith("/1000x1000bb.jpg") and a["thumb"].endswith("/300x300bb.jpg")
        assert d["source"] == "Deezer" and "1000x1000" in d["url"]
        assert all(cs.allowed(x["url"]) for x in r["results"])
        # ein Katalog fällt aus: die Treffer des anderen kommen trotzdem
        cs.DEEZER = base + "/broken"
        r = await cs.search("x")
        assert r["failed"] == ["Deezer"] and len(r["results"]) == 1
        # beide fallen aus: verständliche Meldung
        cs.ITUNES = base + "/broken"
        try:
            await cs.search("x")
            raise AssertionError("keine Fehlermeldung")
        except ConnectionError as e:
            assert "Internet" in str(e)
        try:
            await cs.search("   ")
            raise AssertionError("leerer Suchbegriff angenommen")
        except ValueError:
            pass
        # nur Bilder von den Katalog-Servern
        for bad in ("http://is1-ssl.mzstatic.com/x.jpg", "https://evil.example/x.jpg", "https://mzstatic.com.evil.example/x.jpg",
                    "file:///etc/passwd", "https://192.168.178.1/x.jpg"):
            assert not cs.allowed(bad), bad
            try:
                await cs.download(bad)
                raise AssertionError("fremde Adresse geladen: " + bad)
            except ValueError:
                pass
        # Laden und quadratisch zuschneiden (Prüfung der Adresse hier abgeschaltet)
        real = cs.allowed
        cs.allowed = lambda u: True
        data = await cs.download(base + "/img.png")
        im = Image.open(io.BytesIO(data))
        assert im.format == "JPEG" and im.size == (800, 800), (im.format, im.size)
        try:
            await cs.download(base + "/page")
            raise AssertionError("HTML als Bild angenommen")
        except ValueError:
            pass
        try:
            await cs.download("http://127.0.0.1:1/nix.jpg")
            raise AssertionError("Verbindungsfehler nicht gemeldet")
        except ConnectionError:
            pass
        cs.allowed = real
        small = cs.square_jpeg(png(300, 500))
        assert Image.open(io.BytesIO(small)).size == (300, 300)          # kleine Bilder werden nicht vergrößert
    finally:
        await runner.cleanup()
    print("OK – Cover-Suche bestanden")


if __name__ == "__main__":
    asyncio.run(main())
