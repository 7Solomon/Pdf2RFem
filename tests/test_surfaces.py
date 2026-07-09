"""Tests fuer GeoSurface: Fuellen mit Bogenrand, Commands, JSON, Transferplan."""
import pytest

from pdf2rfem.core.commands import AddSurfaceCmd, CommandStack, DeleteObjectsCmd
from pdf2rfem.core.faces import (boundary_path_points, find_enclosing_cycle,
                                 surface_area, surface_contains,
                                 surface_outline)
from pdf2rfem.core.geometry import (GeoArc, GeometryModel, GeoPoint,
                                    GeoPolyline, GeoSurface)
from pdf2rfem.core.project import Project, View
from pdf2rfem.core.transform import Point2, Workplane
from pdf2rfem.infra.rfem_connector import build_plan


def make_project() -> Project:
    project = Project("x.pdf")
    project.add_view(View(
        id="v1", name="G", page_index=0, scale_denominator=50,
        workplane=Workplane.from_preset("XY (Grundriss)"),
        ref_pdf=Point2(0, 0), ref_target=(0, 0, 0)))
    return project


def add_square(model):
    for pid, x, y in (("a", 0, 0), ("b", 100, 0), ("c", 100, 100),
                      ("d", 0, 100)):
        model.add_point(GeoPoint(pid, "v1", Point2(x, y)))
    for lid, ids in (("l1", ["a", "b"]), ("l2", ["b", "c"]),
                     ("l3", ["c", "d"]), ("l4", ["d", "a"])):
        model.add_line(GeoPolyline(lid, "v1", ids))


def test_fill_result_boundary_ids_ordered():
    project = make_project()
    add_square(project.model)
    res = find_enclosing_cycle(project.model, "v1", Point2(50, 50))
    assert set(res.boundary_ids) == {"l1", "l2", "l3", "l4"}
    assert not res.partial_objects
    pts = boundary_path_points(project.model, res.boundary_ids)
    assert pts is not None and len(pts) == 4


def test_fill_with_arc_boundary():
    """Sehne + Bogen: Flaeche mit Bogenrand ist jetzt erlaubt."""
    model = GeometryModel()
    model.add_point(GeoPoint("a", "v1", Point2(-10, 0)))
    model.add_point(GeoPoint("b", "v1", Point2(10, 0)))
    model.add_line(GeoPolyline("chord", "v1", ["a", "b"]))
    model.add_arc(GeoArc("arc", "v1", ["a", "b"], Point2(0, 10)))
    res = find_enclosing_cycle(model, "v1", Point2(0, 5))
    assert res is not None and res.has_arcs
    assert set(res.boundary_ids) == {"chord", "arc"}
    surface = GeoSurface("s1", "v1", res.boundary_ids)
    model.add_surface(surface)
    assert surface_contains(model, surface, Point2(0, 5))
    assert not surface_contains(model, surface, Point2(0, -5))
    import math
    assert surface_area(model, surface) == pytest.approx(
        math.pi * 100 / 2, rel=0.01)


def test_partial_polyline_flagged():
    """Polylinie ragt ueber den Flaechenrand hinaus -> partial_objects."""
    model = GeometryModel()
    for pid, x, y in (("a", 0, 0), ("b", 100, 0), ("c", 100, 100),
                      ("d", 0, 100), ("e", 200, 0)):
        model.add_point(GeoPoint(pid, "v1", Point2(x, y)))
    model.add_line(GeoPolyline("bottom", "v1", ["a", "b", "e"]))  # ragt raus
    model.add_line(GeoPolyline("l2", "v1", ["b", "c"]))
    model.add_line(GeoPolyline("l3", "v1", ["c", "d"]))
    model.add_line(GeoPolyline("l4", "v1", ["d", "a"]))
    res = find_enclosing_cycle(model, "v1", Point2(50, 50))
    assert res is not None
    assert res.partial_objects == ["bottom"]


def test_surface_commands_and_delete_closure():
    project = make_project()
    add_square(project.model)
    stack = CommandStack(project)
    surface = GeoSurface("s1", "v1", ["l1", "l2", "l3", "l4"])
    stack.push(AddSurfaceCmd(surface))
    assert "s1" in project.model.surfaces

    # Randlinie loeschen zieht die Flaeche mit; Undo stellt beides her
    stack.push(DeleteObjectsCmd(set(), {"l1"}, project.model))
    assert "s1" not in project.model.surfaces
    assert "l1" not in project.model.lines
    stack.undo()
    assert "s1" in project.model.surfaces and "l1" in project.model.lines

    # Punkt loeschen -> Linien weg -> Flaeche weg (transitiv)
    stack.push(DeleteObjectsCmd({"a"}, set(), project.model))
    assert "s1" not in project.model.surfaces
    stack.undo()
    assert "s1" in project.model.surfaces


def test_surface_json_and_plan(tmp_path):
    project = make_project()
    add_square(project.model)
    project.model.add_surface(GeoSurface("s1", "v1",
                                         ["l1", "l2", "l3", "l4"]))
    project.rfem_surface_map["s1"] = 3
    path = str(tmp_path / "s.p2r.json")
    project.save(path)
    loaded = Project.load(str(path))
    assert loaded.model.surfaces["s1"].boundary_ids == ["l1", "l2", "l3", "l4"]
    assert loaded.rfem_surface_map == {"s1": 3}

    plan = build_plan(loaded)
    assert len(plan.surfaces) == 1
    assert plan.surfaces[0].no == 3
    assert plan.surfaces[0].boundary_ids == ["l1", "l2", "l3", "l4"]
    assert "Flaechen" in plan.summary()


def add_inner_square(model, prefix, x0, y0, x1, y1):
    """Rechteck aus 4 Einzellinien mit eindeutigen IDs."""
    ids = []
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for i, (x, y) in enumerate(pts):
        pid = f"{prefix}p{i}"
        model.add_point(GeoPoint(pid, "v1", Point2(x, y)))
        ids.append(pid)
    for i in range(4):
        model.add_line(GeoPolyline(f"{prefix}l{i}", "v1",
                                   [ids[i], ids[(i + 1) % 4]]))


def test_fill_detects_hole():
    """Grosses Quadrat mit kleinem inneren Quadrat: Klick im Ring -> Loch."""
    model = GeometryModel()
    add_inner_square(model, "out", 0, 0, 100, 100)
    add_inner_square(model, "in", 30, 30, 70, 70)
    # Klick in den Materialring (zwischen aussen und innen)
    res = find_enclosing_cycle(model, "v1", Point2(10, 50))
    assert res is not None
    assert set(res.boundary_ids) == {"outl0", "outl1", "outl2", "outl3"}
    assert len(res.holes) == 1
    assert set(res.holes[0].boundary_ids) == {"inl0", "inl1", "inl2", "inl3"}
    # Netto = 100*100 - 40*40
    assert res.area == pytest.approx(100 * 100 - 40 * 40)


def test_fill_click_in_hole_gives_hole():
    """Klick INS Loch liefert das kleine Quadrat ohne weiteres Loch."""
    model = GeometryModel()
    add_inner_square(model, "out", 0, 0, 100, 100)
    add_inner_square(model, "in", 30, 30, 70, 70)
    res = find_enclosing_cycle(model, "v1", Point2(50, 50))
    assert res is not None
    assert set(res.boundary_ids) == {"inl0", "inl1", "inl2", "inl3"}
    assert res.holes == []


def test_surface_with_hole_area_and_contains():
    model = GeometryModel()
    add_inner_square(model, "out", 0, 0, 100, 100)
    add_inner_square(model, "in", 30, 30, 70, 70)
    surface = GeoSurface("s1", "v1",
                         ["outl0", "outl1", "outl2", "outl3"],
                         [["inl0", "inl1", "inl2", "inl3"]])
    model.add_surface(surface)
    assert surface_area(model, surface) == pytest.approx(100 * 100 - 40 * 40)
    assert surface_contains(model, surface, Point2(10, 50))       # Wand
    assert not surface_contains(model, surface, Point2(50, 50))   # im Loch


def test_surface_hole_json_and_transfer(tmp_path):
    project = make_project()
    add_inner_square(project.model, "out", 0, 0, 100, 100)
    add_inner_square(project.model, "in", 30, 30, 70, 70)
    project.model.add_surface(GeoSurface(
        "s1", "v1", ["outl0", "outl1", "outl2", "outl3"],
        [["inl0", "inl1", "inl2", "inl3"]]))
    path = str(tmp_path / "hole.p2r.json")
    project.save(path)
    loaded = Project.load(path)
    assert loaded.model.surfaces["s1"].opening_ids == [
        ["inl0", "inl1", "inl2", "inl3"]]
    plan = build_plan(loaded)
    assert len(plan.surfaces) == 1
    assert len(plan.openings) == 1
    assert plan.openings[0].boundary_ids == ["inl0", "inl1", "inl2", "inl3"]
    assert "Aussparungen" in plan.summary()


def test_delete_opening_line_removes_surface():
    project = make_project()
    add_inner_square(project.model, "out", 0, 0, 100, 100)
    add_inner_square(project.model, "in", 30, 30, 70, 70)
    stack = CommandStack(project)
    surface = GeoSurface("s1", "v1", ["outl0", "outl1", "outl2", "outl3"],
                         [["inl0", "inl1", "inl2", "inl3"]])
    stack.push(AddSurfaceCmd(surface))
    # Loeschen einer LOCH-Randlinie zieht die Flaeche mit
    stack.push(DeleteObjectsCmd(set(), {"inl0"}, project.model))
    assert "s1" not in project.model.surfaces
    stack.undo()
    assert "s1" in project.model.surfaces


def test_boundary_path_none_if_object_missing():
    model = GeometryModel()
    add_square(model)
    surface = GeoSurface("s1", "v1", ["l1", "l2", "l3", "fehlt"])
    assert boundary_path_points(model, surface.boundary_ids) is None
