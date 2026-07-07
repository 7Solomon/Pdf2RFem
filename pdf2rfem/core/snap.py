"""Snapping: liefert zum Cursor den besten Fang-Kandidaten.

Prioritaet: eigener Punkt > Plan-Schnittpunkt > Plan-Linienende >
Bild-Ecke (Scan) > Ortho > Punkt auf Plan-Linie > frei.

Qt-frei und direkt testbar. Kandidaten aus dem PDF (Vektorsegmente bzw.
OpenCV-Ecken) liefert ein austauschbarer Provider (infra.plan_snap), den
das GUI pro Seite setzt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from .geometry import GeometryModel
from .transform import Point2

# Kandidaten-Arten und ihre Rangfolge (kleiner = wichtiger)
PLAN_PRIORITY = {"isect": 1, "vend": 2, "corner": 3}


@dataclass
class PlanCandidate:
    pos: Point2
    kind: str      # "isect" | "vend" | "corner" | "online"


class PlanSnapSource(Protocol):
    def query(self, pos: Point2, radius: float) -> list[PlanCandidate]: ...


@dataclass
class SnapResult:
    pos: Point2
    kind: str            # "point" | "isect" | "vend" | "corner" | "online" | "ortho" | "free"
    target_id: Optional[str] = None

    @property
    def snapped(self) -> bool:
        return self.kind != "free"


class SnapEngine:
    def __init__(self) -> None:
        self.point_snap_enabled = True
        self.plan_snap_enabled = True
        self.provider: Optional[PlanSnapSource] = None

    def snap(self, model: GeometryModel, view_id: str, pos: Point2,
             radius_pt: float, anchor: Optional[Point2] = None,
             ortho: bool = False) -> SnapResult:
        """anchor + ortho: waehrend des Zeichnens auf horizontal/vertikal
        relativ zum letzten Vertex beschraenken (Shift)."""
        if self.point_snap_enabled:
            hit = model.find_point_near(view_id, pos, radius_pt)
            if hit is not None:
                return SnapResult(hit.pos, "point", hit.id)

        candidates: list[PlanCandidate] = []
        if self.plan_snap_enabled and self.provider is not None:
            candidates = self.provider.query(pos, radius_pt)
            pointlike = [c for c in candidates if c.kind in PLAN_PRIORITY
                         and c.pos.dist(pos) <= radius_pt]
            if pointlike:
                best = min(pointlike, key=lambda c: (PLAN_PRIORITY[c.kind],
                                                     c.pos.dist(pos)))
                return SnapResult(best.pos, best.kind)

        if ortho and anchor is not None:
            if abs(pos.x - anchor.x) >= abs(pos.y - anchor.y):
                return SnapResult(Point2(pos.x, anchor.y), "ortho")
            return SnapResult(Point2(anchor.x, pos.y), "ortho")

        online = [c for c in candidates if c.kind == "online"
                  and c.pos.dist(pos) <= radius_pt]
        if online:
            best = min(online, key=lambda c: c.pos.dist(pos))
            return SnapResult(best.pos, "online")

        return SnapResult(pos, "free")
