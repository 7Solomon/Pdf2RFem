"""Kombinierter Plan-Snap-Provider fuer eine PDF-Seite.

Strategie: Vektorsegmente aus dem PDF sind die erste Wahl (exakt);
nur wenn die Seite keine Vektordaten hat (gescannter Plan), springt die
OpenCV-Eckenerkennung ein. Der Vektorindex wird lazy beim ersten Zugriff
gebaut; Ecken-Ergebnisse werden pro Rasterzelle gecacht, damit das
Snapping bei jeder Mausbewegung fluessig bleibt.
"""
from __future__ import annotations

from typing import Optional

from ..core.snap import PlanCandidate
from ..core.transform import Point2
from .edge_detect import find_corners
from .pdf_document import PdfDocument
from .pdf_vector import PdfVectorIndex, PlanArc, Segment

CORNER_CACHE_CELL = 16.0  # PDF-Punkte


class PlanSnapProvider:
    def __init__(self, pdf: PdfDocument, page_index: int) -> None:
        self.pdf = pdf
        self.page_index = page_index
        self._vector: Optional[PdfVectorIndex] = None
        self._corner_cache: dict[tuple[int, int], list[Point2]] = {}

    @property
    def vector(self) -> PdfVectorIndex:
        if self._vector is None:
            self._vector = PdfVectorIndex.from_page(
                self.pdf.doc[self.page_index])
        return self._vector

    @property
    def is_vector_plan(self) -> bool:
        return self.vector.has_content

    def query(self, pos: Point2, radius: float) -> list[PlanCandidate]:
        out: list[PlanCandidate] = []
        vec = self.vector
        if vec.has_content:
            for p in vec.intersections_near(pos, radius):
                out.append(PlanCandidate(p, "isect"))
            for p in vec.endpoints_near(pos, radius):
                out.append(PlanCandidate(p, "vend"))
            seg = vec.nearest_segment(pos, radius)
            if seg is not None:
                out.append(PlanCandidate(seg.closest_point(pos), "online"))
        else:
            for p in self._corners_cached(pos, radius):
                if p.dist(pos) <= radius:
                    out.append(PlanCandidate(p, "corner"))
        return out

    def nearest_segment(self, pos: Point2,
                        radius: float) -> Optional[Segment]:
        """Fuer das Linien-Abgreifen-Werkzeug."""
        if not self.vector.has_content:
            return None
        return self.vector.nearest_segment(pos, radius)

    def nearest_arc(self, pos: Point2, radius: float) -> Optional[PlanArc]:
        """Fuer das Abgreifen von Kreisboegen."""
        if not self.vector.has_content:
            return None
        return self.vector.nearest_arc(pos, radius)

    def snap_vertex(self, pos: Point2, radius: float) -> Point2:
        """Zieht einen (z.B. aus Raster-Konturen stammenden) Vertex auf den
        naechsten exakten Plan-Punkt, falls einer in der Naehe liegt."""
        best, best_d = pos, radius
        for c in self.query(pos, radius):
            if c.kind == "online":
                continue
            d = c.pos.dist(pos)
            if d <= best_d:
                best, best_d = c.pos, d
        return best

    def _corners_cached(self, pos: Point2, radius: float) -> list[Point2]:
        cell = (int(pos.x // CORNER_CACHE_CELL),
                int(pos.y // CORNER_CACHE_CELL))
        if cell not in self._corner_cache:
            self._corner_cache[cell] = find_corners(
                self.pdf, self.page_index, pos, radius)
            if len(self._corner_cache) > 500:
                self._corner_cache.clear()
        return self._corner_cache[cell]
