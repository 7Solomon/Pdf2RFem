"""Projekt: ein PDF-Plan, mehrere Ansichten (Views), gemeinsames Objektmodell.

Jede Ansicht hat eigene Zeichenebene, eigenen Referenzpunkt und eigenen
Massstab - so lassen sich Grundriss, Laengsschnitt und Querschnitt desselben
Plans zu einem 3D-Modell kombinieren. Persistiert wird als JSON.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .geometry import GeoArc, GeometryModel, GeoPoint, GeoPolyline, new_id
from .transform import Point2, ViewTransform, Workplane

SCHEMA_VERSION = 1

# Farbpalette fuer Ansichten (kraeftig, auf hellem Plan gut sichtbar)
VIEW_COLORS = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd",
               "#ff7f0e", "#17becf", "#e377c2", "#8c564b"]


@dataclass
class View:
    id: str
    name: str
    page_index: int
    scale_denominator: float
    workplane: Workplane
    ref_pdf: Optional[Point2] = None
    ref_target: Optional[tuple[float, float, float]] = None
    color: str = VIEW_COLORS[0]
    visible: bool = True

    @property
    def is_ready(self) -> bool:
        """Erst mit gesetztem Referenzpunkt darf gezeichnet werden."""
        return self.ref_pdf is not None and self.ref_target is not None

    def transform(self) -> Optional[ViewTransform]:
        if not self.is_ready:
            return None
        return ViewTransform(self.workplane, self.ref_pdf, self.ref_target,
                             self.scale_denominator)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "page_index": self.page_index,
            "scale_denominator": self.scale_denominator,
            "workplane": self.workplane.to_dict(),
            "ref_pdf": [self.ref_pdf.x, self.ref_pdf.y] if self.ref_pdf else None,
            "ref_target": list(self.ref_target) if self.ref_target else None,
            "color": self.color, "visible": self.visible,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "View":
        return cls(
            id=d["id"], name=d["name"], page_index=d["page_index"],
            scale_denominator=d["scale_denominator"],
            workplane=Workplane.from_dict(d["workplane"]),
            ref_pdf=Point2(*d["ref_pdf"]) if d.get("ref_pdf") else None,
            ref_target=tuple(d["ref_target"]) if d.get("ref_target") else None,
            color=d.get("color", VIEW_COLORS[0]),
            visible=d.get("visible", True),
        )


class Project:
    def __init__(self, pdf_path: str) -> None:
        self.pdf_path = pdf_path
        self.views: dict[str, View] = {}
        self.view_order: list[str] = []
        self.model = GeometryModel()
        # Zuordnung interne ID -> RFEM-Objektnummer; macht wiederholte
        # Uebertragungen idempotent (Update statt Duplikat).
        self.rfem_node_map: dict[str, int] = {}
        self.rfem_line_map: dict[str, int] = {}
        self.active_view_id: Optional[str] = None
        self.file_path: Optional[str] = None
        self.dirty = False

    # --- Views --------------------------------------------------------------
    def add_view(self, view: View) -> None:
        self.views[view.id] = view
        self.view_order.append(view.id)
        if self.active_view_id is None:
            self.active_view_id = view.id
        self.dirty = True

    def remove_view(self, view_id: str) -> None:
        """Entfernt Ansicht samt zugehoeriger Objekte und RFEM-Zuordnungen."""
        for line in self.model.lines_in_view(view_id):
            self.model.remove_line(line.id)
            self.rfem_line_map.pop(line.id, None)
        for arc in self.model.arcs_in_view(view_id):
            self.model.remove_arc(arc.id)
            self.rfem_line_map.pop(arc.id, None)
        for p in self.model.points_in_view(view_id):
            self.model.remove_point(p.id)
            self.rfem_node_map.pop(p.id, None)
        del self.views[view_id]
        self.view_order.remove(view_id)
        if self.active_view_id == view_id:
            self.active_view_id = self.view_order[0] if self.view_order else None
        self.dirty = True

    def ordered_views(self) -> list[View]:
        return [self.views[vid] for vid in self.view_order]

    @property
    def active_view(self) -> Optional[View]:
        return self.views.get(self.active_view_id) if self.active_view_id else None

    def next_view_color(self) -> str:
        return VIEW_COLORS[len(self.views) % len(VIEW_COLORS)]

    # --- Serialisierung -------------------------------------------------------
    def to_dict(self) -> dict:
        pdf_path = self.pdf_path
        if self.file_path:
            # PDF-Pfad relativ zur Projektdatei ablegen, wenn moeglich
            try:
                pdf_path = str(Path(self.pdf_path).relative_to(Path(self.file_path).parent))
            except ValueError:
                pass
        return {
            "schema_version": SCHEMA_VERSION,
            "pdf_path": pdf_path,
            "pdf_path_absolute": str(Path(self.pdf_path).resolve()),
            "views": [self.views[vid].to_dict() for vid in self.view_order],
            "active_view_id": self.active_view_id,
            "points": [
                {"id": p.id, "view_id": p.view_id, "pos": [p.pos.x, p.pos.y]}
                for p in self.model.points.values()
            ],
            "lines": [
                {"id": l.id, "view_id": l.view_id,
                 "point_ids": l.point_ids, "closed": l.closed}
                for l in self.model.lines.values()
            ],
            "arcs": [
                {"id": a.id, "view_id": a.view_id, "point_ids": a.point_ids,
                 "control": [a.control.x, a.control.y]}
                for a in self.model.arcs.values()
            ],
            "rfem_node_map": self.rfem_node_map,
            "rfem_line_map": self.rfem_line_map,
        }

    @classmethod
    def from_dict(cls, d: dict, project_dir: Optional[Path] = None) -> "Project":
        if d.get("schema_version", 1) > SCHEMA_VERSION:
            raise ValueError("Projektdatei stammt aus einer neueren Programmversion")
        pdf_path = d["pdf_path"]
        if project_dir and not Path(pdf_path).is_absolute():
            candidate = project_dir / pdf_path
            pdf_path = str(candidate) if candidate.exists() else d.get(
                "pdf_path_absolute", pdf_path)
        elif not Path(pdf_path).exists():
            pdf_path = d.get("pdf_path_absolute", pdf_path)

        project = cls(pdf_path)
        for vd in d.get("views", []):
            project.add_view(View.from_dict(vd))
        project.active_view_id = d.get("active_view_id") or project.active_view_id
        for pd in d.get("points", []):
            project.model.add_point(
                GeoPoint(pd["id"], pd["view_id"], Point2(*pd["pos"])))
        for ld in d.get("lines", []):
            project.model.add_line(
                GeoPolyline(ld["id"], ld["view_id"], ld["point_ids"],
                            ld.get("closed", False)))
        for ad in d.get("arcs", []):
            project.model.add_arc(
                GeoArc(ad["id"], ad["view_id"], ad["point_ids"],
                       Point2(*ad["control"])))
        project.rfem_node_map = dict(d.get("rfem_node_map", {}))
        project.rfem_line_map = dict(d.get("rfem_line_map", {}))
        project.dirty = False
        return project

    def save(self, path: str) -> None:
        self.file_path = path
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        self.dirty = False

    @classmethod
    def load(cls, path: str) -> "Project":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        project = cls.from_dict(d, project_dir=Path(path).parent)
        project.file_path = path
        return project
