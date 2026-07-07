"""Roundtrip-Tests der Projekt-Serialisierung und des Transferplans."""
from pdf2rfem.core.geometry import GeoPoint, GeoPolyline
from pdf2rfem.core.project import Project, View
from pdf2rfem.core.transform import Point2, Workplane
from pdf2rfem.infra.rfem_connector import build_plan


def make_project() -> Project:
    project = Project("plan.pdf")
    v1 = View(id="v1", name="Grundriss", page_index=0, scale_denominator=50,
              workplane=Workplane.from_preset("XY (Grundriss)"),
              ref_pdf=Point2(100, 100), ref_target=(0, 0, 0), color="#d62728")
    v2 = View(id="v2", name="Schnitt", page_index=1, scale_denominator=25,
              workplane=Workplane.from_preset("XZ (Ansicht/Laengsschnitt)"))
    project.add_view(v1)
    project.add_view(v2)
    project.model.add_point(GeoPoint("p1", "v1", Point2(100, 100)))
    project.model.add_point(GeoPoint("p2", "v1", Point2(200, 100)))
    project.model.add_line(GeoPolyline("l1", "v1", ["p1", "p2"], closed=False))
    project.model.add_point(GeoPoint("p3", "v2", Point2(0, 0)))  # nicht bereit
    project.rfem_node_map["p1"] = 7
    return project


def test_json_roundtrip(tmp_path):
    project = make_project()
    path = tmp_path / "test.p2r.json"
    project.save(str(path))
    loaded = Project.load(str(path))

    assert loaded.view_order == ["v1", "v2"]
    assert loaded.views["v1"].ref_target == (0, 0, 0)
    assert loaded.views["v2"].ref_pdf is None
    assert loaded.views["v1"].workplane.axis_v == 1
    assert set(loaded.model.points) == {"p1", "p2", "p3"}
    assert loaded.model.lines["l1"].point_ids == ["p1", "p2"]
    assert loaded.rfem_node_map == {"p1": 7}
    assert loaded.dirty is False


def test_build_plan_skips_views_without_reference():
    project = make_project()
    plan = build_plan(project)
    assert {n.point_id for n in plan.nodes} == {"p1", "p2"}
    assert plan.skipped_views == ["Schnitt"]
    # p1 hat bereits RFEM-Nr. 7 -> Update, p2 neu
    by_id = {n.point_id: n for n in plan.nodes}
    assert by_id["p1"].no == 7
    assert by_id["p2"].no is None
    assert plan.new_node_count == 1
    assert len(plan.lines) == 1 and plan.lines[0].no is None
    # Referenzpunkt liegt auf p1 -> Koordinate = Ziel
    assert by_id["p1"].coords == (0.0, 0.0, 0.0)


def test_remove_view_cleans_objects():
    project = make_project()
    project.remove_view("v1")
    assert "p1" not in project.model.points
    assert "l1" not in project.model.lines
    assert "p1" not in project.rfem_node_map
    assert "p3" in project.model.points
