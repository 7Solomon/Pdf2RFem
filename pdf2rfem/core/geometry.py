"""Geometriemodell: Punkte und Polylinien in Plan-Koordinaten (PDF-Punkte).

Objekte speichern bewusst Plan-Koordinaten plus View-Referenz, keine fertigen
RFEM-Koordinaten: aendert sich Referenzpunkt oder Massstab einer Ansicht,
rechnen sich alle abgeleiteten RFEM-Koordinaten automatisch neu.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .transform import Point2


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class GeoPoint:
    id: str
    view_id: str
    pos: Point2


@dataclass
class GeoPolyline:
    id: str
    view_id: str
    point_ids: list[str]
    closed: bool = False


@dataclass
class GeoArc:
    """Kreisbogen: Start-/Endpunkt sind geteilte Modellpunkte (RFEM-Knoten),
    der Kontrollpunkt liegt AUF dem Bogen und ist reine Koordinate -
    exakt die Arc-Definition von RFEM (arc_control_point)."""
    id: str
    view_id: str
    point_ids: list[str]      # [start_id, end_id]
    control: Point2


@dataclass
class GeoSurface:
    """Flaeche, definiert ueber ihre Randobjekte (Linien/Boegen) in
    Umlaufreihenfolge - exakt die Surface-Definition von RFEM
    (boundary_lines). Traegt keine eigene Geometrie; der Umriss wird aus
    den referenzierten Objekten rekonstruiert. opening_ids: je Aussparung
    ein Randzug (wird in RFEM zu einem Opening-Objekt)."""
    id: str
    view_id: str
    boundary_ids: list[str]   # GeoPolyline-/GeoArc-IDs in Umlaufreihenfolge
    opening_ids: list[list[str]] = field(default_factory=list)

    def all_object_ids(self) -> list[str]:
        ids = list(self.boundary_ids)
        for hole in self.opening_ids:
            ids.extend(hole)
        return ids


class GeometryModel:
    """Zentrale Objektverwaltung. Qt-frei; GUI haengt sich als Listener an."""

    def __init__(self) -> None:
        self.points: dict[str, GeoPoint] = {}
        self.lines: dict[str, GeoPolyline] = {}
        self.arcs: dict[str, GeoArc] = {}
        self.surfaces: dict[str, GeoSurface] = {}
        self._listeners: list[Callable[[], None]] = []

    # --- Listener ---------------------------------------------------------
    def add_listener(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def notify(self) -> None:
        for fn in self._listeners:
            fn()

    # --- Mutationen (werden von Commands aufgerufen, nicht direkt vom GUI) -
    def add_point(self, p: GeoPoint) -> None:
        self.points[p.id] = p

    def remove_point(self, pid: str) -> None:
        if self.lines_using_point(pid) or self.arcs_using_point(pid):
            raise ValueError(f"Punkt {pid} wird noch von Linien/Boegen verwendet")
        del self.points[pid]

    def add_line(self, line: GeoPolyline) -> None:
        for pid in line.point_ids:
            if pid not in self.points:
                raise ValueError(f"Linie referenziert unbekannten Punkt {pid}")
        self.lines[line.id] = line

    def remove_line(self, lid: str) -> None:
        if self.surfaces_using_object(lid):
            raise ValueError(f"Linie {lid} wird noch von Flaechen verwendet")
        del self.lines[lid]

    def add_surface(self, surface: GeoSurface) -> None:
        for oid in surface.all_object_ids():
            if oid not in self.lines and oid not in self.arcs:
                raise ValueError(
                    f"Flaeche referenziert unbekanntes Randobjekt {oid}")
        self.surfaces[surface.id] = surface

    def remove_surface(self, sid: str) -> None:
        del self.surfaces[sid]

    def add_arc(self, arc: GeoArc) -> None:
        for pid in arc.point_ids:
            if pid not in self.points:
                raise ValueError(f"Bogen referenziert unbekannten Punkt {pid}")
        self.arcs[arc.id] = arc

    def remove_arc(self, aid: str) -> None:
        if self.surfaces_using_object(aid):
            raise ValueError(f"Bogen {aid} wird noch von Flaechen verwendet")
        del self.arcs[aid]

    # --- Abfragen -----------------------------------------------------------
    def lines_using_point(self, pid: str) -> list[GeoPolyline]:
        return [l for l in self.lines.values() if pid in l.point_ids]

    def arcs_using_point(self, pid: str) -> list[GeoArc]:
        return [a for a in self.arcs.values() if pid in a.point_ids]

    def surfaces_using_object(self, obj_id: str) -> list[GeoSurface]:
        return [s for s in self.surfaces.values()
                if obj_id in s.all_object_ids()]

    def surfaces_in_view(self, view_id: str) -> list[GeoSurface]:
        return [s for s in self.surfaces.values() if s.view_id == view_id]

    def node_degree(self, view_id: str) -> dict[str, int]:
        """Anzahl anhaengender Linien-Segmente + Boegen je Knoten."""
        deg: dict[str, int] = {}
        for line in self.lines_in_view(view_id):
            ids = line.point_ids
            pairs = list(zip(ids, ids[1:]))
            if line.closed and len(ids) > 2:
                pairs.append((ids[-1], ids[0]))
            for a, b in pairs:
                deg[a] = deg.get(a, 0) + 1
                deg[b] = deg.get(b, 0) + 1
        for arc in self.arcs_in_view(view_id):
            for pid in arc.point_ids:
                deg[pid] = deg.get(pid, 0) + 1
        return deg

    def loose_ends(self, view_id: str) -> list[GeoPoint]:
        """Punkte mit genau einer anhaengenden Linie/Bogen (Grad 1) -
        moegliche Luecken. Punkte ganz ohne Linie (Grad 0) zaehlen NICHT."""
        deg = self.node_degree(view_id)
        return [self.points[pid] for pid, d in deg.items() if d == 1]

    def points_in_view(self, view_id: str) -> list[GeoPoint]:
        return [p for p in self.points.values() if p.view_id == view_id]

    def lines_in_view(self, view_id: str) -> list[GeoPolyline]:
        return [l for l in self.lines.values() if l.view_id == view_id]

    def arcs_in_view(self, view_id: str) -> list[GeoArc]:
        return [a for a in self.arcs.values() if a.view_id == view_id]

    def find_point_near(self, view_id: str, pos: Point2,
                        radius: float) -> Optional[GeoPoint]:
        """Naechster Punkt der Ansicht innerhalb des Radius (in PDF-Punkten)."""
        best: Optional[GeoPoint] = None
        best_d = radius
        for p in self.points_in_view(view_id):
            d = p.pos.dist(pos)
            if d <= best_d:
                best, best_d = p, d
        return best

    def find_arc_near(self, view_id: str, pos: Point2,
                      radius: float) -> Optional[GeoArc]:
        from .arcs import dist_point_arc
        best: Optional[GeoArc] = None
        best_d = radius
        for arc in self.arcs_in_view(view_id):
            s, e = (self.points[pid].pos for pid in arc.point_ids)
            d = dist_point_arc(pos, s, arc.control, e)
            if d <= best_d:
                best, best_d = arc, d
        return best

    def find_line_near(self, view_id: str, pos: Point2,
                       radius: float) -> Optional[GeoPolyline]:
        """Naechste Polylinie, deren Segment dem Punkt naeher als radius ist."""
        best: Optional[GeoPolyline] = None
        best_d = radius
        for line in self.lines_in_view(view_id):
            pts = [self.points[pid].pos for pid in line.point_ids]
            segs = list(zip(pts, pts[1:]))
            if line.closed and len(pts) > 2:
                segs.append((pts[-1], pts[0]))
            for a, b in segs:
                d = _dist_point_segment(pos, a, b)
                if d <= best_d:
                    best, best_d = line, d
        return best

    def is_empty(self) -> bool:
        return (not self.points and not self.lines and not self.arcs
                and not self.surfaces)


def _dist_point_segment(p: Point2, a: Point2, b: Point2) -> float:
    ax, ay, bx, by = a.x, a.y, b.x, b.y
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return p.dist(a)
    t = max(0.0, min(1.0, ((p.x - ax) * dx + (p.y - ay) * dy) / seg_len_sq))
    return p.dist(Point2(ax + t * dx, ay + t * dy))
