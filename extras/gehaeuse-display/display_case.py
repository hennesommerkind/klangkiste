"""Gehäuse für das Klangkiste-Display (CYD ESP32-2432S028, 86 × 50 mm) zum Einhängen ins IKEA Skådis.
Eulen-Look passend zur Klangkiste: zwei Federohren, zwei Füße, Flügelbögen neben dem Display.

Teile: Rahmen (Sichtseite nach unten drucken), Deckel (Außenseite nach unten), 2 × Haken (liegend).
Aufruf: python3 display_case.py  →  STL/STEP je Teil und eine 3MF-Druckplatte im Ordner out/
Koordinaten: y nach oben, z = 0 Sichtseite und wächst nach hinten; von vorn gesehen zeigt +x nach links.
"""
import math
import zipfile
from pathlib import Path

import cadquery as cq

# ---- Maße der Platine (aus Datenblatt und Fotos, vor dem Druck nachmessen) ----
PCB_W, PCB_H, PCB_T = 86.0, 50.0, 1.6
GLASS_W = 69.2          # Breite des Display-Moduls (so hoch wie die Platine)
BACK_ROOM = 8.5         # Platz hinter der Platine für Bauteile und Stecker
USB_TOP, USB_BOTTOM = 9.5, 29.5   # Kerbe für die Stecker, ab Oberkante der Platine: Micro-USB (Mitte ca. 16 mm,
                                  # der wird für den Strom gebraucht) und USB-C darunter (Mitte 23 mm, gemessen)
# Display: sichtbar bleibt nur die Bildfläche, der Rahmen überdeckt den schwarzen Rand des Moduls
ACTIVE_W, ACTIVE_H = 57.6, 43.2
ACTIVE_LEFT = 9.0       # Abstand Bildfläche – linke Modulkante, von vorn gesehen (USB-Seite); rechts bleiben ca. 2,5 mm
WINDOW_MARGIN = 1.0     # so viel Rand bleibt rund um die Bildfläche offen
MODULE_T = 4.4          # Dicke des Display-Moduls über der Platine (geschätzt – bitte nachmessen)
LIP = 1.2               # Dicke der Blende über dem Displayrand
LIP_GAP = 0.4           # Luft zwischen Blende und Glas, damit der Touch nicht dauernd gedrückt wird

# ---- Gehäuse ----
GAP = 0.3               # Luft rund um die Platine
FRONT = LIP + MODULE_T + LIP_GAP   # Sichtseite bis zur Auflage der Platine
OUT_W, OUT_H, R = 100.0, 62.0, 6.0
COVER_T = 3.0
SCREW = (46.3, 27.3)    # 4 × M3 × 12 Senkkopf von hinten
POST = (39.0, 21.0)     # Stützen des Deckels drücken die Platine an den Ecken nach vorn
OWL = True

# ---- Skådis: Schlitze 5 × 15 mm, Raster 40 mm, Platte 5 mm dick ----
HOOK_X = 20.0           # zwei Haken, 40 mm Abstand
HOOK_T = 4.4            # Dicke des Hakens (Schlitzbreite 5)
PEG_Y0, PEG_H = 14.0, 8.0
BOARD = 5.4
TAB_DOWN, TAB_T = 6.0, 2.6
FLANGE_T = 1.5

DEPTH = FRONT + PCB_T + BACK_ROOM   # Tiefe des Rahmens


def rrect(w, h, r, t):
    return cq.Workplane("XY").rect(w, h).extrude(t).edges("|Z").fillet(r)


def frame():
    body = rrect(OUT_W, OUT_H, R, DEPTH)
    if OWL:
        for s in (-1, 1):
            # Federohr: nach außen geneigtes Dreieck
            ear = (cq.Workplane("XY").polyline([(s * 16, OUT_H / 2 - 5), (s * 44, OUT_H / 2 - 5),
                                                (s * 47, OUT_H / 2 + 12), (s * 41, OUT_H / 2 + 19)])
                   .close().extrude(6.0).edges("|Z").fillet(2.2))
            body = body.union(ear)
            # Fuß: drei Zehen
            for dx in (-5.5, 0, 5.5):
                toe = cq.Workplane("XY").center(s * 24 + dx, -OUT_H / 2 - 0.8).circle(3.3).extrude(5.0)
                body = body.union(toe)
    # Platinentasche und Durchblick für das Display
    body = body.cut(cq.Workplane("XY").workplane(offset=FRONT).rect(PCB_W + 2 * GAP, PCB_H + 2 * GAP)
                    .extrude(DEPTH))
    # Tasche für das Display-Modul hinter der Blende
    body = body.cut(cq.Workplane("XY").workplane(offset=LIP).rect(GLASS_W + 1.4, PCB_H + 2 * GAP).extrude(FRONT))
    # Fenster nur über der Bildfläche; von vorn gesehen sitzt sie nach rechts versetzt (= −x, weil z nach hinten zeigt)
    shift = ACTIVE_LEFT + ACTIVE_W / 2 - GLASS_W / 2
    win = (cq.Workplane("XY").center(-shift, 0).rect(ACTIVE_W + 2 * WINDOW_MARGIN, ACTIVE_H + 2 * WINDOW_MARGIN)
           .extrude(LIP).edges("|Z").fillet(1.5))
    body = body.cut(win)
    # Fase außen am Fenster
    body = body.cut(cq.Workplane("XY").center(-shift, 0)
                    .rect(ACTIVE_W + 2 * WINDOW_MARGIN + 1.6, ACTIVE_H + 2 * WINDOW_MARGIN + 1.6)
                    .workplane(offset=0.8).rect(ACTIVE_W + 2 * WINDOW_MARGIN, ACTIVE_H + 2 * WINDOW_MARGIN).loft())
    # Schraublöcher (Kernloch für M3, von hinten)
    for sx in (-1, 1):
        for sy in (-1, 1):
            body = body.cut(cq.Workplane("XY").workplane(offset=DEPTH - 10.5)
                            .center(sx * SCREW[0], sy * SCREW[1]).circle(1.3).extrude(11))
    # USB: Kerbe in der von vorn gesehen linken Wand (= +x, weil z nach hinten zeigt), nach hinten offen
    uy = PCB_H / 2 - (USB_TOP + USB_BOTTOM) / 2
    z0 = FRONT + PCB_T - 2.6
    body = body.cut(cq.Workplane("XY").workplane(offset=z0)
                    .center((OUT_W / 2 + PCB_W / 2) / 2 + 1, uy).rect(OUT_W / 2 - PCB_W / 2 + 4, USB_BOTTOM - USB_TOP)
                    .extrude(DEPTH))
    if OWL:
        # Flügelbögen neben dem Display, 0,5 mm tief graviert
        band = cq.Workplane("XY").rect(OUT_W - 3, OUT_H - 8).extrude(0.5).cut(
            cq.Workplane("XY").rect(GLASS_W + 6, OUT_H).extrude(0.5))
        for s in (-1, 1):
            for r in (11.0, 15.5, 20.0):
                ring = (cq.Workplane("XY").center(s * (OUT_W / 2 + 6), -4).circle(r + 0.5).circle(r - 0.5)
                        .extrude(0.5))
                body = body.cut(ring.intersect(band))
    return body


def cover():
    c = rrect(OUT_W, OUT_H, R, COVER_T)
    for sx in (-1, 1):
        for sy in (-1, 1):
            # Stütze
            c = c.union(cq.Workplane("XY").workplane(offset=COVER_T).center(sx * POST[0], sy * POST[1])
                        .circle(3.0).extrude(BACK_ROOM - 0.1))
            # Senkkopfloch (Kopf außen = Druckbettseite)
            c = c.cut(cq.Workplane("XY").center(sx * SCREW[0], sy * SCREW[1]).circle(1.7).extrude(COVER_T))
            c = c.cut(cq.Solid.makeCone(3.3, 1.7, 1.7, pnt=cq.Vector(sx * SCREW[0], sy * SCREW[1], 0)))
    for s in (-1, 1):
        # Schlitz für den Haken und Tasche für dessen Flansch (innen)
        c = c.cut(cq.Workplane("XY").center(s * HOOK_X, PEG_Y0 + PEG_H / 2).rect(HOOK_T + 0.4, PEG_H + 1.8)
                  .extrude(COVER_T))
        c = c.cut(cq.Workplane("XY").workplane(offset=COVER_T - FLANGE_T - 0.1)
                  .center(s * HOOK_X, PEG_Y0 + PEG_H / 2).rect(HOOK_T + 0.8, PEG_H + 6.6).extrude(FLANGE_T + 0.1))
    # Lüftungsschlitze unten
    for i in range(-2, 3):
        c = c.cut(cq.Workplane("XY").center(i * 9, -18).rect(2.2, 12).extrude(COVER_T).edges("|Z").fillet(1.0))
    return c


def hook():
    """Profil in (y, z): Flansch innen im Deckel, Hals durch den Deckel, Zapfen durch die Platte, Nase nach unten."""
    f0 = -(COVER_T)                 # Innenseite des Deckels
    pts = [(-3, f0), (PEG_H + 3, f0), (PEG_H + 3, f0 + FLANGE_T), (PEG_H, f0 + FLANGE_T),
           (PEG_H, BOARD + TAB_T), (-TAB_DOWN + 1.2, BOARD + TAB_T), (-TAB_DOWN, BOARD + TAB_T - 1.2),
           (-TAB_DOWN, BOARD), (0, BOARD), (0, f0 + FLANGE_T), (-3, f0 + FLANGE_T)]
    return cq.Workplane("XY").polyline(pts).close().extrude(HOOK_T)


# ---------- Ausgabe ----------
def mesh(shape, tol=0.05):
    v, t = shape.val().tessellate(tol, 0.2)
    return [(p.x, p.y, p.z) for p in v], t


def write_3mf(path, objects):
    """objects: [(name, shape, dx, dy)] – jedes Teil in Drucklage, auf der Platte verteilt."""
    res, items = [], []
    for i, (name, shp, dx, dy) in enumerate(objects, 1):
        v, t = mesh(shp)
        vs = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in v)
        ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in t)
        res.append(f'<object id="{i}" type="model" name="{name}"><mesh><vertices>{vs}</vertices>'
                   f'<triangles>{ts}</triangles></mesh></object>')
        items.append(f'<item objectid="{i}" transform="1 0 0 0 1 0 0 0 1 {dx} {dy} 0"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="de" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources>{"".join(res)}</resources><build>{"".join(items)}</build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model)


if __name__ == "__main__":
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    parts = {"display_rahmen": frame(), "display_deckel": cover(), "display_haken": hook()}
    for name, shp in parts.items():
        cq.exporters.export(shp, str(out / f"{name}.stl"), tolerance=0.05)
        cq.exporters.export(shp, str(out / f"{name}.step"))
        bb = shp.val().BoundingBox()
        print(f"{name}: {bb.xlen:.1f} × {bb.ylen:.1f} × {bb.zlen:.1f} mm, Volumen {shp.val().Volume() / 1000:.1f} cm³")
    # Druckplatte (A1 mini: 180 × 180): Rahmen oben, Deckel unten, Haken daneben
    hk = parts["display_haken"]
    write_3mf(out / "klangkiste_display_gehaeuse.3mf", [
        ("Rahmen", parts["display_rahmen"], 90, 125),
        ("Deckel", parts["display_deckel"], 90, 45),
        ("Haken 1", hk, 150, 30), ("Haken 2", hk, 165, 30),
    ])
