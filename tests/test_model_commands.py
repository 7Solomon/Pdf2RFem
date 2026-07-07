"""Tests fuer Geometriemodell, Undo/Redo und Snapping."""
import pytest

from pdf2rfem.core.commands import (AddPointCmd, AddPolylineCmd, CommandStack,
                                    DeleteObjectsCmd, SetReferenceCmd)
from pdf2rfem.core.geometry import GeoPoint, GeoPolyline, new_id
from pdf2rfem.core.project import Project, View
from pdf2rfem.core.snap import SnapEngine
from pdf2rfem.core.transform import Point2, Workplane


def make_project() -> Project:
    project = Project("dummy.pdf")
    view = View(id="v1", name="Grundriss", page_index=0,
                scale_denominator=50,
                workplane=Workplane.from_preset("XY (Grundriss)"),
                ref_pdf=Point2(0, 0), ref_target=(0, 0, 0))
    project.add_view(view)
    return project


def test_add_point_undo_redo():
    project = make_project()
    stack = CommandStack(project)
    p = GeoPoint(new_id(), "v1", Point2(10, 10))
    stack.push(AddPointCmd(p))
    assert p.id in project.model.points
    stack.undo()
    assert p.id not in project.model.points
    stack.redo()
    assert p.id in project.model.points


def test_polyline_reuses_snapped_points():
    """Gesnappte vorhandene Punkte werden referenziert, nicht dupliziert."""
    project = make_project()
    stack = CommandStack(project)
    existing = GeoPoint(new_id(), "v1", Point2(0, 0))
    stack.push(AddPointCmd(existing))

    new_p = GeoPoint(new_id(), "v1", Point2(100, 0))
    line = GeoPolyline(new_id(), "v1", [existing.id, new_p.id])
    stack.push(AddPolylineCmd([new_p], line))
    assert len(project.model.points) == 2
    assert project.model.lines[line.id].point_ids[0] == existing.id

    stack.undo()  # Polylinie weg, neuer Punkt weg, vorhandener bleibt
    assert len(project.model.lines) == 0
    assert list(project.model.points) == [existing.id]


def test_delete_point_removes_dependent_lines():
    project = make_project()
    stack = CommandStack(project)
    p1 = GeoPoint(new_id(), "v1", Point2(0, 0))
    p2 = GeoPoint(new_id(), "v1", Point2(50, 0))
    line = GeoPolyline(new_id(), "v1", [p1.id, p2.id])
    stack.push(AddPolylineCmd([p1, p2], line))

    stack.push(DeleteObjectsCmd({p1.id}, set(), project.model))
    assert p1.id not in project.model.points
    assert line.id not in project.model.lines   # abhaengige Linie mit weg
    assert p2.id in project.model.points        # freier Punkt bleibt

    stack.undo()
    assert p1.id in project.model.points
    assert line.id in project.model.lines


def test_set_reference_undo():
    project = make_project()
    stack = CommandStack(project)
    view = project.views["v1"]
    stack.push(SetReferenceCmd(view, Point2(5, 5), (1.0, 2.0, 3.0)))
    assert view.ref_target == (1.0, 2.0, 3.0)
    stack.undo()
    assert view.ref_target == (0, 0, 0)
    assert view.ref_pdf == Point2(0, 0)


def test_model_guards():
    project = make_project()
    p1 = GeoPoint("p1", "v1", Point2(0, 0))
    p2 = GeoPoint("p2", "v1", Point2(1, 1))
    project.model.add_point(p1)
    project.model.add_point(p2)
    project.model.add_line(GeoPolyline("l1", "v1", ["p1", "p2"]))
    with pytest.raises(ValueError):
        project.model.remove_point("p1")  # noch von l1 verwendet
    with pytest.raises(ValueError):
        project.model.add_line(GeoPolyline("l2", "v1", ["p1", "fehlt"]))


def test_snap_priorities():
    project = make_project()
    model = project.model
    model.add_point(GeoPoint("p1", "v1", Point2(100, 100)))
    engine = SnapEngine()

    # Punkt-Snap gewinnt innerhalb des Radius
    s = engine.snap(model, "v1", Point2(103, 101), radius_pt=5)
    assert s.kind == "point" and s.target_id == "p1"
    # ausserhalb des Radius: frei
    s = engine.snap(model, "v1", Point2(120, 100), radius_pt=5)
    assert s.kind == "free"
    # Ortho: horizontal dominiert bei groesserem dx
    s = engine.snap(model, "v1", Point2(50, 3), radius_pt=1,
                    anchor=Point2(0, 0), ortho=True)
    assert s.kind == "ortho" and s.pos == Point2(50, 0)
    # anderer View: kein Snap
    s = engine.snap(model, "v2", Point2(100, 100), radius_pt=5)
    assert s.kind == "free"


def test_find_line_near():
    project = make_project()
    model = project.model
    model.add_point(GeoPoint("p1", "v1", Point2(0, 0)))
    model.add_point(GeoPoint("p2", "v1", Point2(100, 0)))
    model.add_line(GeoPolyline("l1", "v1", ["p1", "p2"]))
    assert model.find_line_near("v1", Point2(50, 3), 5).id == "l1"
    assert model.find_line_near("v1", Point2(50, 30), 5) is None
