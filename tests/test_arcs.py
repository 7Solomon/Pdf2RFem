"""Tests fuer Kreisbogen: Geometrie, PDF-Erkennung, Modell, Transferplan."""
import math

import pymupdf
import pytest

from pdf2rfem.core.arcs import (arc_midpoint, arc_sweep,
                                circle_from_3_points, dist_point_arc,
                                sample_arc)
from pdf2rfem.core.commands import AddArcCmd, CommandStack, DeleteObjectsCmd
from pdf2rfem.core.geometry import GeoArc, GeoPoint, new_id
from pdf2rfem.core.project import Project, View
from pdf2rfem.core.transform import Point2, Workplane
from pdf2rfem.infra.pdf_document import PdfDocument
from pdf2rfem.infra.pdf_vector import PdfVectorIndex
from pdf2rfem.infra.rfem_connector import build_plan


# --- Geometrie ---------------------------------------------------------------

def test_circle_from_3_points():
    center, r = circle_from_3_points(Point2(10, 0), Point2(0, 10),
                                     Point2(-10, 0))
    assert center.dist(Point2(0, 0)) < 1e-9
    assert r == pytest.approx(10)
    assert circle_from_3_points(Point2(0, 0), Point2(1, 1),
                                Point2(2, 2)) is None  # kollinear


def test_arc_sweep_direction():
    center = Point2(0, 0)
    # Halbkreis von (10,0) nach (-10,0), Kontrollpunkt "unten" (y>0)
    sweep = arc_sweep(center, Point2(10, 0), Point2(0, 10), Point2(-10, 0))
    assert sweep == pytest.approx(math.pi)
    # gleicher Bogen, Kontrollpunkt "oben" -> andere Richtung
    sweep = arc_sweep(center, Point2(10, 0), Point2(0, -10), Point2(-10, 0))
    assert sweep == pytest.approx(-math.pi)


def test_sample_and_midpoint():
    s, c, e = Point2(10, 0), Point2(0, 10), Point2(-10, 0)
    pts = sample_arc(s, c, e, n=32)
    assert pts[0].dist(s) < 1e-9 and pts[-1].dist(e) < 1e-9
    assert all(abs(p.dist(Point2(0, 0)) - 10) < 1e-9 for p in pts)
    on_circle = 10 / math.sqrt(2)
    mid = arc_midpoint(s, Point2(on_circle, on_circle), e)
    assert mid.dist(Point2(0, 10)) < 1e-9


def test_dist_point_arc():
    s, c, e = Point2(10, 0), Point2(0, 10), Point2(-10, 0)
    assert dist_point_arc(Point2(0, 12), s, c, e) == pytest.approx(2)
    # Punkt auf der "anderen" Seite: Abstand zum naechsten Endpunkt
    assert dist_point_arc(Point2(0, -10), s, c, e) == pytest.approx(
        Point2(0, -10).dist(s))


# --- PDF-Erkennung -------------------------------------------------------------

@pytest.fixture
def arc_pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    page.draw_circle((200, 150), 60, width=1)            # Vollkreis
    page.draw_line((20, 20), (120, 20), width=1)
    path = str(tmp_path / "arc.pdf")
    doc.save(path)
    doc.close()
    return PdfDocument(path)


def test_full_circle_from_beziers(arc_pdf):
    idx = PdfVectorIndex.from_page(arc_pdf.doc[0])
    assert len(idx.arcs) == 1
    arc = idx.arcs[0]
    assert arc.is_full_circle
    assert Point2(arc.cx, arc.cy).dist(Point2(200, 150)) < 0.5
    assert arc.r == pytest.approx(60, abs=0.5)
    # Punkt auf dem Kreis wird gefunden, Punkt daneben nicht
    assert idx.nearest_arc(Point2(200 + 61, 150), 5) is arc
    assert idx.nearest_arc(Point2(200, 150), 5) is None  # Mittelpunkt


def test_arc_trace_geometry(arc_pdf):
    """Vom PlanArc abgeleitete GeoArc-Definition liegt auf dem Kreis."""
    idx = PdfVectorIndex.from_page(arc_pdf.doc[0])
    arc = idx.arcs[0]
    s, e, c = arc.point_at(0.0), arc.point_at(0.5), arc.point_at(0.25)
    fit = circle_from_3_points(s, c, e)
    assert fit is not None
    center, r = fit
    assert center.dist(Point2(200, 150)) < 0.5
    assert r == pytest.approx(60, abs=0.5)


# --- Modell / Commands / Transfer ---------------------------------------------

def make_project() -> Project:
    project = Project("x.pdf")
    project.add_view(View(
        id="v1", name="G", page_index=0, scale_denominator=50,
        workplane=Workplane.from_preset("XY (Grundriss)"),
        ref_pdf=Point2(0, 0), ref_target=(0, 0, 0)))
    return project


def test_arc_commands_and_plan():
    project = make_project()
    stack = CommandStack(project)
    p1 = GeoPoint("p1", "v1", Point2(10, 0))
    p2 = GeoPoint("p2", "v1", Point2(-10, 0))
    arc = GeoArc("a1", "v1", ["p1", "p2"], Point2(0, 10))
    stack.push(AddArcCmd([p1, p2], arc))
    assert "a1" in project.model.arcs

    plan = build_plan(project)
    arcs = [l for l in plan.lines if l.arc_control is not None]
    assert len(arcs) == 1
    # Kontrollpunkt (0,10) pt: PDF-y nach unten -> RFEM-Y negativ
    m = 50 * 25.4 / 72 / 1000
    assert arcs[0].arc_control[1] == pytest.approx(-10 * m)

    # Punkt loeschen zieht den Bogen mit; Undo stellt beides wieder her
    stack.push(DeleteObjectsCmd({"p1"}, set(), project.model))
    assert "a1" not in project.model.arcs
    stack.undo()
    assert "a1" in project.model.arcs and "p1" in project.model.points

    stack.undo()  # AddArc rueckgaengig
    assert project.model.is_empty()


def test_arc_json_roundtrip(tmp_path):
    project = make_project()
    project.model.add_point(GeoPoint("p1", "v1", Point2(10, 0)))
    project.model.add_point(GeoPoint("p2", "v1", Point2(-10, 0)))
    project.model.add_arc(GeoArc("a1", "v1", ["p1", "p2"], Point2(0, 10)))
    path = str(tmp_path / "arc.p2r.json")
    project.save(path)
    loaded = Project.load(path)
    assert loaded.model.arcs["a1"].control == Point2(0, 10)
    assert loaded.model.arcs["a1"].point_ids == ["p1", "p2"]
