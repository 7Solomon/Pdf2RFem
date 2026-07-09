"""Verbindungs- und Geometrie-Smoke-Test gegen ein laufendes RFEM 6.

Erzeugt ein eigenes Wegwerf-Modell 'PDF2RFEM_SmokeTest', uebertraegt darin
eine Mini-Geometrie ueber den echten RfemConnector-Codepfad und schliesst
das Modell OHNE zu speichern. Ein bereits geoeffnetes Nutzer-Modell wird
nicht angefasst (nur kurz die Aktivierung gewechselt).

Aufruf:  python scripts/rfem_smoke_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf2rfem.core.geometry import GeoArc, GeoPoint, GeoPolyline, GeoSurface
from pdf2rfem.core.project import Project, View
from pdf2rfem.core.transform import Point2, Workplane
from pdf2rfem.infra.rfem_connector import RfemConnector, build_plan


def main() -> int:
    connector = RfemConnector()
    print("Verbinde mit RFEM ...")
    print("  ", connector.connect())
    app = connector.app

    print("Lege Testmodell 'PDF2RFEM_SmokeTest' an ...")
    model_id = app.create_model(name="PDF2RFEM_SmokeTest")

    # Mini-Projekt: Rechteck 5 x 3 m im Grundriss, Massstab 1:50
    project = Project("dummy.pdf")
    view = View(id="v1", name="Grundriss", page_index=0, scale_denominator=50,
                workplane=Workplane.from_preset("XY (Grundriss)"),
                ref_pdf=Point2(0, 0), ref_target=(0.0, 0.0, 0.0))
    project.add_view(view)
    m_per_pt = 50 * 25.4 / 72 / 1000
    w_pt, h_pt = 5.0 / m_per_pt, 3.0 / m_per_pt   # 5 m breit, 3 m hoch
    pts = [GeoPoint(f"p{i}", "v1", pos) for i, pos in enumerate([
        Point2(0, 0), Point2(w_pt, 0), Point2(w_pt, -h_pt), Point2(0, -h_pt)])]
    for p in pts:
        project.model.add_point(p)
    project.model.add_line(
        GeoPolyline("l1", "v1", [p.id for p in pts], closed=True))

    # Dazu ein Halbkreisbogen (R = 1 m) neben dem Rechteck
    r_pt = 1.0 / m_per_pt
    a1 = GeoPoint("pa1", "v1", Point2(8.0 / m_per_pt - r_pt, 0))
    a2 = GeoPoint("pa2", "v1", Point2(8.0 / m_per_pt + r_pt, 0))
    project.model.add_point(a1)
    project.model.add_point(a2)
    project.model.add_arc(GeoArc(
        "arc1", "v1", ["pa1", "pa2"], Point2(8.0 / m_per_pt, -r_pt)))

    # Inneres 1x1-m-Rechteck als Aussparung (Cutout)
    h = 0.5 / m_per_pt
    cx, cy = 2.5 / m_per_pt, -1.5 / m_per_pt  # Mitte des 5x3-Rechtecks
    hole_pts = [GeoPoint(f"h{i}", "v1", pos) for i, pos in enumerate([
        Point2(cx - h, cy - h), Point2(cx + h, cy - h),
        Point2(cx + h, cy + h), Point2(cx - h, cy + h)])]
    for p in hole_pts:
        project.model.add_point(p)
    project.model.add_line(GeoPolyline(
        "lhole", "v1", [p.id for p in hole_pts], closed=True))

    # Flaeche: Rechteck mit Aussparung (Dicke spaeter in RFEM)
    project.model.add_surface(GeoSurface("s1", "v1", ["l1"], [["lhole"]]))

    plan = build_plan(project)
    print("Transferplan:", plan.summary().replace("\n", " | "))
    print("Uebertrage ...")
    print("  ", connector.transfer(project, plan))
    print("   Knoten-Zuordnung:", project.rfem_node_map)
    print("   Linien-Zuordnung:", project.rfem_line_map)

    # Zweite Uebertragung muss aktualisieren statt duplizieren
    plan2 = build_plan(project)
    print("Zweiter Durchlauf (Idempotenz):",
          connector.transfer(project, plan2))

    # Kontrolle: was ist im Modell angekommen?
    from dlubal.api import rfem
    nodes = app.get_object_id_list(object_type=rfem.OBJECT_TYPE_NODE)
    lines = app.get_object_id_list(object_type=rfem.OBJECT_TYPE_LINE)
    surfaces = app.get_object_id_list(object_type=rfem.OBJECT_TYPE_SURFACE)
    openings = app.get_object_id_list(object_type=rfem.OBJECT_TYPE_OPENING)
    print(f"Im Modell: {len(nodes.object_id)} Knoten, "
          f"{len(lines.object_id)} Linien, "
          f"{len(surfaces.object_id)} Flaechen, "
          f"{len(openings.object_id)} Aussparungen")
    assert len(nodes.object_id) == 10, "erwartet: 10 Knoten"
    assert len(lines.object_id) == 3, "erwartet: 3 Linien (Polygon+Bogen+Loch)"
    assert len(surfaces.object_id) == 1, "erwartet: 1 Flaeche"
    assert len(openings.object_id) == 1, "erwartet: 1 Aussparung"

    surf_no = project.rfem_surface_map["s1"]
    surf = app.get_object(rfem.structure_core.Surface(no=surf_no))
    print(f"Flaeche {surf_no}: A = {surf.area:.6f} m2 (erwartet 14.0 = 15 - 1)")
    assert abs(surf.area - 14.0) < 1e-3, "Flaecheninhalt weicht ab"

    # Bogen zurücklesen und Typ/Laenge pruefen (Halbkreis R=1m -> pi m)
    arc_no = project.rfem_line_map["arc1"]
    arc_line = app.get_object(rfem.structure_core.Line(no=arc_no))
    print(f"Bogen-Linie {arc_no}: Typ={arc_line.type}, "
          f"Laenge={arc_line.length:.6f} m (erwartet {3.141593:.6f})")
    assert arc_line.type == rfem.structure_core.Line.TYPE_ARC
    assert abs(arc_line.length - 3.141592653589793) < 1e-4

    print("Schliesse Testmodell ohne zu speichern ...")
    app.close_model(save_changes=False, model_id=model_id)
    connector.close()
    print("RFEM-SMOKE-TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
