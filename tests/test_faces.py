"""Tests fuer das Fuellen-Werkzeug (geschlossenen Linienzug finden)."""
import pytest

from pdf2rfem.core.faces import find_enclosing_cycle
from pdf2rfem.core.geometry import (GeoArc, GeometryModel, GeoPoint,
                                    GeoPolyline)
from pdf2rfem.core.transform import Point2


def _pt(model, pid, x, y, view="v1"):
    model.add_point(GeoPoint(pid, view, Point2(x, y)))


def _line(model, lid, ids, view="v1", closed=False):
    model.add_line(GeoPolyline(lid, view, ids, closed))


def make_square(model):
    """Quadrat (0,0)-(100,100) aus 4 EINZELNEN Linien mit geteilten Knoten."""
    _pt(model, "a", 0, 0)
    _pt(model, "b", 100, 0)
    _pt(model, "c", 100, 100)
    _pt(model, "d", 0, 100)
    _line(model, "l1", ["a", "b"])
    _line(model, "l2", ["b", "c"])
    _line(model, "l3", ["c", "d"])
    _line(model, "l4", ["d", "a"])


def test_square_from_separate_lines():
    model = GeometryModel()
    make_square(model)
    res = find_enclosing_cycle(model, "v1", Point2(50, 50))
    assert res is not None
    assert set(res.node_ids) == {"a", "b", "c", "d"}
    assert len(res.node_ids) == 4
    assert res.area == pytest.approx(100 * 100)
    assert not res.has_arcs


def test_click_outside_returns_none_or_outer():
    model = GeometryModel()
    make_square(model)
    # Punkt ausserhalb: kein umschliessender Zug
    assert find_enclosing_cycle(model, "v1", Point2(300, 300)) is None


def test_open_shape_returns_none():
    model = GeometryModel()
    _pt(model, "a", 0, 0)
    _pt(model, "b", 100, 0)
    _pt(model, "c", 100, 100)
    _line(model, "l1", ["a", "b"])
    _line(model, "l2", ["b", "c"])   # U-Form, nicht geschlossen
    assert find_enclosing_cycle(model, "v1", Point2(50, 50)) is None


def test_adjacent_squares_pick_smallest():
    """Zwei Quadrate mit gemeinsamer Kante: Klick links liefert nur das linke."""
    model = GeometryModel()
    make_square(model)
    _pt(model, "e", 200, 0)
    _pt(model, "f", 200, 100)
    _line(model, "l5", ["b", "e"])
    _line(model, "l6", ["e", "f"])
    _line(model, "l7", ["f", "c"])
    res = find_enclosing_cycle(model, "v1", Point2(50, 50))
    assert set(res.node_ids) == {"a", "b", "c", "d"}
    res = find_enclosing_cycle(model, "v1", Point2(150, 50))
    assert set(res.node_ids) == {"b", "e", "f", "c"}


def test_dangling_edge_is_ignored():
    """Stichleitung ins Innere gehoert nicht zum Rand."""
    model = GeometryModel()
    make_square(model)
    _pt(model, "x", 50, 50)
    _line(model, "spur", ["a", "x"])
    res = find_enclosing_cycle(model, "v1", Point2(70, 30))
    assert res is not None
    assert set(res.node_ids) == {"a", "b", "c", "d"}


def test_closed_polyline_works_directly():
    model = GeometryModel()
    _pt(model, "a", 0, 0)
    _pt(model, "b", 60, 0)
    _pt(model, "c", 30, 60)
    _line(model, "tri", ["a", "b", "c"], closed=True)
    res = find_enclosing_cycle(model, "v1", Point2(30, 20))
    assert res is not None
    assert set(res.node_ids) == {"a", "b", "c"}
    assert res.area == pytest.approx(0.5 * 60 * 60)


def test_face_with_arc_flagged():
    """Halbkreis + Sehne: Flaeche gefunden, Bogen-Flag gesetzt."""
    model = GeometryModel()
    _pt(model, "a", -10, 0)
    _pt(model, "b", 10, 0)
    _line(model, "chord", ["a", "b"])
    model.add_arc(GeoArc("arc", "v1", ["a", "b"], Point2(0, 10)))
    res = find_enclosing_cycle(model, "v1", Point2(0, 5))
    assert res is not None
    assert res.has_arcs
    assert set(res.node_ids) == {"a", "b"}
    # Halbkreisflaeche ~ pi/2 * 100
    assert res.area == pytest.approx(math_pi_half_area(), rel=0.01)


def math_pi_half_area():
    import math
    return math.pi * 10 * 10 / 2
