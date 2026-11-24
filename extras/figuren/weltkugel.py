"""Figur „Weltkugel“ auf Henriks Figurensockel (Phoniesockel4.stl, Ø 30 × 7,2 mm, Taschen für
RFID-Aufkleber und Magnet sind darin schon enthalten). Einfarbig: die Kontinente stehen als Relief
0,9 mm vor, zum späteren Bemalen. Die Kugel sitzt in einem Kelch, der sie bis 45° stützt – so
druckt sie stehend ohne Stützen.

Braucht numpy, scipy, Pillow, geopandas 0.14 (bringt die Küstenlinien „Natural Earth“ mit).
Aufruf: python3 weltkugel.py → out/weltkugel.3mf und out/weltkugel.stl
"""
import struct
import warnings
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter

HERE = Path(__file__).parent
R = 21.0            # Radius der Kugel (Meer)
RELIEF = 0.9        # Höhe der Kontinente
LON0 = 15.0         # dieser Längengrad zeigt nach vorn (Europa/Afrika)
NLON, NLAT = 600, 300
NECK_R, NECK_H = 9.0, 1.5


def land_height():
    """Höhenkarte (lat × lon, 0…1): 1 = Land, weiche Küste."""
    import geopandas as gpd
    warnings.filterwarnings("ignore")
    world = gpd.read_file(gpd.datasets.get_path("naturalearth_lowres"))
    W, H = 2880, 1440
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    for geom in world.geometry:
        for poly in getattr(geom, "geoms", [geom]):
            d.polygon([((x + 180) / 360 * W, (90 - y) / 180 * H) for x, y in poly.exterior.coords], fill=255)
    a = gaussian_filter(np.asarray(im, float) / 255, sigma=(2.2, 2.2), mode=("nearest", "wrap"))
    return np.clip((a - 0.25) / 0.5, 0, 1)      # steilere, aber nicht senkrechte Küste


def globe(center):
    hmap = land_height()
    H, W = hmap.shape
    lat = np.linspace(-90, 90, NLAT + 1)[1:-1]
    lon = np.linspace(-180, 180, NLON, endpoint=False)
    LO, LA = np.meshgrid(lon, lat)
    px = ((LO + 180) / 360 * W).astype(int) % W
    py = np.clip(((90 - LA) / 180 * H).astype(int), 0, H - 1)
    r = R + RELIEF * hmap[py, px]
    la, lo = np.radians(LA), np.radians(LO - LON0)
    v = np.stack([r * np.cos(la) * np.sin(lo), -r * np.cos(la) * np.cos(lo), r * np.sin(la)], -1).reshape(-1, 3)
    south, north = len(v), len(v) + 1
    v = np.vstack([v, [0, 0, -R], [0, 0, R + RELIEF * hmap[2, 0]]]) + center
    n = NLAT - 1
    i, j = np.meshgrid(np.arange(n - 1), np.arange(NLON), indexing="ij")
    a, b = i * NLON + j, i * NLON + (j + 1) % NLON
    c, d = a + NLON, b + NLON
    t = np.concatenate([np.stack([a, b, d], -1).reshape(-1, 3), np.stack([a, d, c], -1).reshape(-1, 3)])
    j = np.arange(NLON)
    bot = np.stack([np.full(NLON, south), (j + 1) % NLON, j], -1)
    top0 = (n - 1) * NLON
    top = np.stack([np.full(NLON, north), top0 + j, top0 + (j + 1) % NLON], -1)
    return v, np.concatenate([t, bot, top])


def revolve(profile, center, seg=120):
    """Geschlossener Rotationskörper aus (r, z)-Punkten von unten nach oben (r > 0), mit Deckeln."""
    ang = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    v = [[r * np.cos(a), r * np.sin(a), z] for r, z in profile for a in ang]
    lo, hi = len(v), len(v) + 1
    v += [[0, 0, profile[0][1]], [0, 0, profile[-1][1]]]
    t = []
    for k in range(len(profile) - 1):
        for j in range(seg):
            a, b = k * seg + j, k * seg + (j + 1) % seg
            t += [[a, b, b + seg], [a, b + seg, a + seg]]
    top0 = (len(profile) - 1) * seg
    for j in range(seg):
        t += [[lo, (j + 1) % seg, j], [hi, top0 + j, top0 + (j + 1) % seg]]
    return np.array(v, float) + [center[0], center[1], 0], np.array(t)


def read_stl(path):
    b = path.read_bytes()
    n = struct.unpack("<I", b[80:84])[0]
    a = np.frombuffer(b, dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]), count=n, offset=84)
    pts = a["v"].reshape(-1, 3).astype(float)
    uniq, inv = np.unique(pts.round(4), axis=0, return_inverse=True)
    return uniq, inv.reshape(-1, 3)


def write_stl(path, parts):
    with open(path, "wb") as f:
        f.write(b"Weltkugel".ljust(80, b"\0") + struct.pack("<I", sum(len(t) for _, t in parts)))
        for v, t in parts:
            tri = v[t]
            nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
            rec = np.zeros(len(t), dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]))
            rec["n"], rec["v"] = nrm, tri.reshape(-1, 9)
            f.write(rec.tobytes())


def write_3mf(path, parts, name):
    res = []
    for i, (pname, (v, t)) in enumerate(parts, 1):
        vs = "".join(f'<vertex x="{x:.3f}" y="{y:.3f}" z="{z:.3f}"/>' for x, y, z in v)
        ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in t)
        res.append(f'<object id="{i}" type="model" name="{pname}"><mesh><vertices>{vs}</vertices>'
                   f'<triangles>{ts}</triangles></mesh></object>')
    top = len(parts) + 1
    comps = "".join(f'<component objectid="{i}"/>' for i in range(1, top))
    res.append(f'<object id="{top}" type="model" name="{name}"><components>{comps}</components></object>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="de" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources>{"".join(res)}</resources><build><item objectid="{top}"/></build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model)


def build():
    sv, st = read_stl(HERE / "Phoniesockel4.stl")
    cx, cy = (sv[:, 0].min() + sv[:, 0].max()) / 2, (sv[:, 1].min() + sv[:, 1].max()) / 2
    top = sv[:, 2].max()
    # Kelch: kurzer Hals, dann 45°-Kegel bis an die Kugel bei 45° südlicher Breite
    rim_r = R * np.cos(np.radians(45)) - 0.3
    z0 = top + NECK_H
    rim_z = z0 + (rim_r - NECK_R)
    zc = rim_z + R * np.sin(np.radians(45))
    cup = revolve([(NECK_R, top - 0.6), (NECK_R, z0), (rim_r, rim_z)], (cx, cy))
    return [("Sockel", (sv, st)), ("Kelch", cup), ("Weltkugel", globe(np.array([cx, cy, zc])))], zc


if __name__ == "__main__":
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    parts, zc = build()
    allv = np.vstack([v for _, (v, _) in parts])
    print("Größe", (allv.max(0) - allv.min(0)).round(1), "Kugelmitte z", round(zc, 1),
          "Dreiecke", sum(len(t) for _, (_, t) in parts))
    write_3mf(out / "weltkugel.3mf", parts, "Weltkugel")
    write_stl(out / "weltkugel.stl", [p for _, p in parts])
