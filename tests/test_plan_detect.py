"""Tests fuer Vektor-Snap (get_drawings), Flaechenerkennung und Prioritaeten."""
import pymupdf
import pytest

from pdf2rfem.core.geometry import GeoPoint
from pdf2rfem.core.project import Project, View
from pdf2rfem.core.snap import PlanCandidate, SnapEngine
from pdf2rfem.core.transform import Point2, Workplane
from pdf2rfem.infra.edge_detect import detect_region
from pdf2rfem.infra.pdf_document import PdfDocument
from pdf2rfem.infra.pdf_vector import PdfVectorIndex, Segment, seg_intersection


@pytest.fixture
def plan_pdf(tmp_path):
    """Synthetischer Plan: zwei sich kreuzende Linien + grau gefuellte Flaeche."""
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=400)
    page.draw_line((100, 100), (300, 100), width=1)   # horizontal
    page.draw_line((200, 50), (200, 200), width=1)    # vertikal, kreuzt bei (200,100)
    page.draw_rect((320, 220, 420, 320), color=(0, 0, 0),
                   fill=(0.8, 0.8, 0.8), width=1)
    path = str(tmp_path / "plan.pdf")
    doc.save(path)
    doc.close()
    return PdfDocument(path)


def test_seg_intersection():
    a = Segment(0, 0, 10, 0)
    b = Segment(5, -5, 5, 5)
    p = seg_intersection(a, b)
    assert p == Point2(5, 0)
    assert seg_intersection(a, Segment(0, 1, 10, 1)) is None  # parallel
    assert seg_intersection(a, Segment(20, -5, 20, 5)) is None  # ausserhalb


def test_vector_index_endpoints_and_intersections(plan_pdf):
    idx = PdfVectorIndex.from_page(plan_pdf.doc[0])
    assert idx.has_content

    ends = idx.endpoints_near(Point2(101, 99), 5)
    assert any(p.dist(Point2(100, 100)) < 1e-6 for p in ends)

    isects = idx.intersections_near(Point2(198, 102), 5)
    assert any(p.dist(Point2(200, 100)) < 1e-6 for p in isects)

    seg = idx.nearest_segment(Point2(150, 103), 5)
    assert seg is not None
    assert seg.closest_point(Point2(150, 103)).dist(Point2(150, 100)) < 1e-6

    # Rechteck liefert 4 Kanten -> Ecke (320,220) als Endpunkt findbar
    corner = idx.endpoints_near(Point2(321, 221), 4)
    assert any(p.dist(Point2(320, 220)) < 1e-6 for p in corner)


def test_detect_region_gray_rectangle(plan_pdf):
    result = detect_region(plan_pdf, 0, Point2(370, 270),
                           clip=(0, 0, 500, 400), tolerance=12)
    assert result is not None
    assert not result.touched_border
    assert 3 <= len(result.polygon) <= 8
    # Alle 4 Rechteck-Ecken muessen (bis auf Rasterungenauigkeit) dabei sein
    for corner in (Point2(320, 220), Point2(420, 220),
                   Point2(420, 320), Point2(320, 320)):
        assert min(p.dist(corner) for p in result.polygon) < 2.0


def test_detect_region_background_touches_border(plan_pdf):
    result = detect_region(plan_pdf, 0, Point2(50, 300),
                           clip=(0, 0, 500, 400), tolerance=12)
    assert result is not None
    assert result.touched_border


class FakeProvider:
    def __init__(self, candidates):
        self.candidates = candidates

    def query(self, pos, radius):
        return self.candidates


def _model_with_point():
    project = Project("x.pdf")
    view = View(id="v1", name="V", page_index=0, scale_denominator=50,
                workplane=Workplane.from_preset("XY (Grundriss)"),
                ref_pdf=Point2(0, 0), ref_target=(0, 0, 0))
    project.add_view(view)
    return project.model


def test_snap_plan_priorities():
    model = _model_with_point()
    engine = SnapEngine()
    engine.provider = FakeProvider([
        PlanCandidate(Point2(10, 1), "vend"),
        PlanCandidate(Point2(10, 2), "isect"),   # weiter weg, gewinnt trotzdem
        PlanCandidate(Point2(10, 0), "online"),
    ])
    s = engine.snap(model, "v1", Point2(10, 0.5), radius_pt=5)
    assert s.kind == "isect"

    # eigener Modellpunkt schlaegt Plan-Kandidaten
    model.add_point(GeoPoint("p1", "v1", Point2(10, 3)))
    s = engine.snap(model, "v1", Point2(10, 0.5), radius_pt=5)
    assert s.kind == "point" and s.target_id == "p1"

    # online kommt erst nach Ortho
    model.points.clear()
    engine.provider = FakeProvider([PlanCandidate(Point2(10, 0), "online")])
    s = engine.snap(model, "v1", Point2(10, 0.5), radius_pt=5,
                    anchor=Point2(0, 0.2), ortho=True)
    assert s.kind == "ortho"
    s = engine.snap(model, "v1", Point2(10, 0.5), radius_pt=5)
    assert s.kind == "online" and s.pos == Point2(10, 0)

    # Plan-Snap abschaltbar
    engine.plan_snap_enabled = False
    s = engine.snap(model, "v1", Point2(10, 0.5), radius_pt=5)
    assert s.kind == "free"
