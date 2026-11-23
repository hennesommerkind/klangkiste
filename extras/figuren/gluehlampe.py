"""Figur „Glühlampe“ für die Serie „Wieso? Weshalb? Warum?“ – passend zu Henriks Figurensockel:
unten die Tasche für den RFID-Aufkleber (Ø 26 × 0,5 mm, 0,5 mm über dem Boden) und darüber die für den
Magneten (Ø 8,5 × 3,6 mm), Maße aus seinem „Phoniesockel_Negativteil.stl“.

Drei Teile für drei Farben: Sockel (grau), Glas (gelb), Glühfaden (dunkel, 0,7 mm erhaben auf der Vorderseite).
Stehend drucken, ohne Stützen; bei 1,0 mm und bei 5,1 mm Höhe pausieren und Aufkleber bzw. Magnet einlegen.
Aufruf: python3 gluehlampe.py → out/gluehlampe.3mf (ein Objekt mit drei Teilen) und STL je Teil
"""
import math
import zipfile
from pathlib import Path

import cadquery as cq

R_BASE, H_BASE = 15.0, 13.0
GLASS = [(12.6, H_BASE), (13.0, 17), (16.3, 24), (19.3, 31), (20.5, 37.5), (19.2, 44.5), (15.0, 50.5), (8.2, 54.6),
         (0.0, 56.0)]
RELIEF = 0.7


def revolve(pts, grow=0.0):
    p = [(r + (grow if r > 0 else 0), z + (grow if r == 0 else 0)) for r, z in pts]
    w = (cq.Workplane("XZ").moveTo(0, p[0][1]).lineTo(*p[0]).spline(p[1:], includeCurrent=True).close())
    return w.revolve(360, (0, 0, 0), (0, 1, 0))


def base():
    b = cq.Workplane("XY").circle(R_BASE).extrude(H_BASE).edges("<Z").chamfer(0.6)
    for z in (3.4, 6.0, 8.6, 11.2):       # Gewindegänge
        ring = (cq.Workplane("XZ").center(R_BASE, z).circle(0.9).revolve(360, (-R_BASE, 0, 0), (-R_BASE, 1, 0)))
        b = b.union(ring)
    b = b.cut(cq.Workplane("XY").workplane(offset=0.5).circle(13.0).extrude(0.5))     # RFID-Aufkleber
    b = b.cut(cq.Workplane("XY").workplane(offset=1.5).circle(4.25).extrude(3.6))     # Magnet 8 × 3
    return b


def glass():
    # unten glatt am Sockel abschließen (keine Überlappung der Teile)
    return revolve(GLASS).cut(cq.Workplane("XY").workplane(offset=-5).circle(40).extrude(5 + H_BASE))


def filament(g):
    pts = [(-5.5, 21.5), (-5.5, 30), (-2.8, 39), (0, 31.5), (2.8, 39), (5.5, 30), (5.5, 21.5)]
    wire = None
    for (x0, z0), (x1, z1) in zip(pts, pts[1:]):
        L = math.hypot(x1 - x0, z1 - z0)
        seg = (cq.Workplane("XY").box(L + 1.6, 30, 1.6, centered=(True, False, True))
               .rotate((0, 0, 0), (0, 1, 0), -math.degrees(math.atan2(z1 - z0, x1 - x0)))
               .translate(((x0 + x1) / 2, -30, (z0 + z1) / 2)))
        wire = seg if wire is None else wire.union(seg)
    shell = revolve(GLASS, RELIEF).cut(g)
    return wire.intersect(shell)


def mesh(shape, tol=0.04):
    v, t = shape.val().tessellate(tol, 0.15)
    return [(p.x, p.y, p.z) for p in v], t


def write_3mf(path, parts, name):
    """Ein Objekt aus mehreren Teilen (3MF-Komponenten) – der Slicer lädt es als eine Figur mit einfärbbaren Teilen."""
    res = []
    for i, (pname, shp) in enumerate(parts, 1):
        v, t = mesh(shp)
        vs = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in v)
        ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in t)
        res.append(f'<object id="{i}" type="model" name="{pname}"><mesh><vertices>{vs}</vertices>'
                   f'<triangles>{ts}</triangles></mesh></object>')
    top = len(parts) + 1
    comps = "".join(f'<component objectid="{i}"/>' for i in range(1, top))
    res.append(f'<object id="{top}" type="model" name="{name}"><components>{comps}</components></object>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="de" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources>{"".join(res)}</resources><build><item objectid="{top}" '
             'transform="1 0 0 0 1 0 0 0 1 90 90 0"/></build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model)


if __name__ == "__main__":
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    g = glass()
    parts = [("Sockel (grau)", base()), ("Glas (gelb)", g), ("Glühfaden (dunkel)", filament(g))]
    for (n, s), f in zip(parts, ("sockel", "glas", "faden")):
        cq.exporters.export(s, str(out / f"gluehlampe_{f}.stl"), tolerance=0.04)
        bb = s.val().BoundingBox()
        print(f"{n}: {bb.xlen:.1f} × {bb.ylen:.1f} × {bb.zlen:.1f} mm, z {bb.zmin:.1f}–{bb.zmax:.1f}, {s.val().Volume() / 1000:.1f} cm³")
    write_3mf(out / "gluehlampe.3mf", parts, "Glühlampe")
