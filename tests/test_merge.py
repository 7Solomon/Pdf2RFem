"""Tests fuer Loose-End-Erkennung und Knoten-Zusammenfuehren."""
import pytest

from pdf2rfem.core.commands import CommandStack, MergePointsCmd
from pdf2rfem.core.geometry import (GeoArc, GeometryModel, GeoPoint,
                                    GeoPolyline)
from pdf2rfem.core.project import Project, View
from pdf2rfem.core.transform import Point2, Workplane


def make_project() -> Project:
    project = Project("x.pdf")
    project.add_view(View(
        id="v1", name="G", page_index=0, scale_denominator=50,
        workplane=Workplane.from_preset("XY (Grundriss)"),
        ref_pdf=Point2(0, 0), ref_target=(0, 0, 0)))
    return project


def test_node_degree_and_loose_ends():
    m = GeometryModel()
    for pid, x, y in (("a", 0, 0), ("b", 10, 0), ("c", 20, 0), ("d", 30, 0)):
        m.add_point(GeoPoint(pid, "v1", Point2(x, y)))
    m.add_line(GeoPolyline("l1", "v1", ["a", "b", "c"]))  # b ist Mittelknoten
    m.add_line(GeoPolyline("l2", "v1", ["c", "d"]))
    deg = m.node_degree("v1")
    assert deg == {"a": 1, "b": 2, "c": 2, "d": 1}
    loose = {p.id for p in m.loose_ends("v1")}
    assert loose == {"a", "d"}
    # freier Punkt (Grad 0) zaehlt nicht als loses Ende
    m.add_point(GeoPoint("frei", "v1", Point2(99, 99)))
    assert "frei" not in {p.id for p in m.loose_ends("v1")}


def test_merge_closes_gap():
    """Zwei fast deckungsgleiche Enden zweier Linien -> ein Knoten."""
    project = make_project()
    m = project.model
    for pid, x, y in (("a", 0, 0), ("b", 10, 0), ("c1", 10.0, 2.0),
                      ("d", 20, 2)):
        m.add_point(GeoPoint(pid, "v1", Point2(x, y)))
    m.add_line(GeoPolyline("l1", "v1", ["a", "b"]))
    m.add_line(GeoPolyline("l2", "v1", ["c1", "d"]))
    # b und c1 sind beide lose Enden, 2 pt auseinander
    assert {p.id for p in m.loose_ends("v1")} == {"a", "b", "c1", "d"}
    stack = CommandStack(project)
    stack.push(MergePointsCmd("c1", "b", m))   # c1 -> b
    assert "c1" not in m.points
    assert m.lines["l2"].point_ids == ["b", "d"]
    # jetzt haengen l1 und l2 an b -> b nicht mehr lose
    assert {p.id for p in m.loose_ends("v1")} == {"a", "d"}
    stack.undo()
    assert "c1" in m.points
    assert m.lines["l2"].point_ids == ["c1", "d"]


def test_merge_updates_arc():
    project = make_project()
    m = project.model
    for pid, x, y in (("a", -10, 0), ("b", 10, 0), ("b2", 10.5, 0)):
        m.add_point(GeoPoint(pid, "v1", Point2(x, y)))
    m.add_arc(GeoArc("arc", "v1", ["a", "b"], Point2(0, 10)))
    m.add_line(GeoPolyline("l", "v1", ["b2", "a"]))
    # b (Bogenende) und b2 (Linienende) sind ~0.5 pt auseinander
    stack = CommandStack(project)
    stack.push(MergePointsCmd("b2", "b", m))
    assert m.lines["l"].point_ids == ["b", "a"]
    assert m.arcs["arc"].point_ids == ["a", "b"]
    # Bogen + Linie bilden nun einen geschlossenen Zug (a-b)
    assert {p.id for p in m.loose_ends("v1")} == set()


def test_square_with_gap_closes_after_merge():
    """Quadrat mit einer 2-pt-Luecke: erst offen, nach Merge fuellbar."""
    from pdf2rfem.core.faces import find_enclosing_cycle
    project = make_project()
    m = project.model
    # Ecken; e ist ~2pt neben a (Luecke)
    coords = {"a": (0, 0), "b": (100, 0), "c": (100, 100), "d": (0, 100),
              "e": (0, 2)}
    for pid, (x, y) in coords.items():
        m.add_point(GeoPoint(pid, "v1", Point2(x, y)))
    m.add_line(GeoPolyline("l1", "v1", ["a", "b"]))
    m.add_line(GeoPolyline("l2", "v1", ["b", "c"]))
    m.add_line(GeoPolyline("l3", "v1", ["c", "d"]))
    m.add_line(GeoPolyline("l4", "v1", ["d", "e"]))   # endet bei e, nicht a
    assert find_enclosing_cycle(m, "v1", Point2(50, 50)) is None  # offen
    stack = CommandStack(project)
    stack.push(MergePointsCmd("e", "a", m))           # Luecke schliessen
    res = find_enclosing_cycle(m, "v1", Point2(50, 50))
    assert res is not None
    assert set(res.boundary_ids) == {"l1", "l2", "l3", "l4"}
