"""Vektor-Snap: Liniensegmente direkt aus dem PDF lesen (page.get_drawings).

Bei Vektor-Plaenen ist das exakter als jede Bilderkennung: Endpunkte und
Schnittpunkte werden mathematisch exakt bestimmt statt aus Pixeln geschaetzt.
Ein einfacher Zellen-Index haelt die Abfragen um den Cursor schnell, auch
bei Plaenen mit zehntausenden Segmenten.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

from ..core.arcs import TWO_PI, arc_sweep, circle_from_3_points
from ..core.transform import Point2


@dataclass(frozen=True)
class Segment:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def p1(self) -> Point2:
        return Point2(self.x1, self.y1)

    @property
    def p2(self) -> Point2:
        return Point2(self.x2, self.y2)

    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    def closest_point(self, p: Point2) -> Point2:
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        d2 = dx * dx + dy * dy
        if d2 == 0:
            return self.p1
        t = max(0.0, min(1.0, ((p.x - self.x1) * dx + (p.y - self.y1) * dy) / d2))
        return Point2(self.x1 + t * dx, self.y1 + t * dy)


@dataclass(frozen=True)
class PlanArc:
    """Aus PDF-Bezierkurven rekonstruierter Kreisbogen."""
    cx: float
    cy: float
    r: float
    a0: float       # Startwinkel (rad)
    sweep: float    # vorzeichenbehafteter Winkelweg (rad)

    def point_at(self, t: float) -> Point2:
        a = self.a0 + self.sweep * t
        return Point2(self.cx + self.r * math.cos(a),
                      self.cy + self.r * math.sin(a))

    @property
    def start(self) -> Point2:
        return self.point_at(0.0)

    @property
    def end(self) -> Point2:
        return self.point_at(1.0)

    @property
    def is_full_circle(self) -> bool:
        return abs(self.sweep) > math.radians(350)

    def distance(self, pos: Point2) -> float:
        center = Point2(self.cx, self.cy)
        a = math.atan2(pos.y - self.cy, pos.x - self.cx)
        d = ((a - self.a0) % TWO_PI if self.sweep >= 0
             else -((self.a0 - a) % TWO_PI))
        if abs(d) <= abs(self.sweep):
            return abs(center.dist(pos) - self.r)
        return min(pos.dist(self.start), pos.dist(self.end))


def _bezier_points(p0, p1, p2, p3, n: int = 8) -> list[Point2]:
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1.0 - t
        x = (u**3 * p0.x + 3 * u**2 * t * p1.x
             + 3 * u * t**2 * p2.x + t**3 * p3.x)
        y = (u**3 * p0.y + 3 * u**2 * t * p1.y
             + 3 * u * t**2 * p2.y + t**3 * p3.y)
        pts.append(Point2(x, y))
    return pts


def _fit_arc(samples: list[Point2]) -> Optional[tuple[Point2, float, float]]:
    """Kreis durch Anfang/Mitte/Ende; None, wenn die Kurve kein Kreisbogen
    ist (Abweichung der uebrigen Stuetzpunkte zu gross)."""
    fit = circle_from_3_points(samples[0], samples[len(samples) // 2],
                               samples[-1])
    if fit is None:
        return None
    center, r = fit
    if r < 0.5:  # Winzradien: praktisch Punkte, kein nutzbarer Bogen
        return None
    tol = max(0.05, 0.004 * r)
    for p in samples:
        if abs(center.dist(p) - r) > tol:
            return None
    sweep = arc_sweep(center, samples[0], samples[len(samples) // 2],
                      samples[-1])
    return center, r, sweep


def seg_intersection(a: Segment, b: Segment) -> Optional[Point2]:
    """Echter Schnittpunkt zweier Segmente (inkl. T-Stoessen), sonst None."""
    d1x, d1y = a.x2 - a.x1, a.y2 - a.y1
    d2x, d2y = b.x2 - b.x1, b.y2 - b.y1
    denom = d1x * d2y - d1y * d2x
    if abs(denom) < 1e-12:
        return None  # parallel/kollinear
    sx, sy = b.x1 - a.x1, b.y1 - a.y1
    t = (sx * d2y - sy * d2x) / denom
    u = (sx * d1y - sy * d1x) / denom
    eps = 1e-9
    if -eps <= t <= 1 + eps and -eps <= u <= 1 + eps:
        return Point2(a.x1 + t * d1x, a.y1 + t * d1y)
    return None


class PdfVectorIndex:
    """Raeumlicher Index ueber alle Vektorsegmente einer PDF-Seite."""

    CELL = 20.0  # Zellgroesse in PDF-Punkten

    def __init__(self, segments: Iterable[Segment],
                 extra_endpoints: Iterable[Point2] = (),
                 arcs: Iterable[PlanArc] = ()) -> None:
        self.arcs = list(arcs)
        # Duplikate (z.B. doppelt gezeichnete Kanten) zusammenfassen
        seen: set[tuple] = set()
        self.segments: list[Segment] = []
        for s in segments:
            if s.length() < 1e-6:
                continue
            key = (round(s.x1, 3), round(s.y1, 3),
                   round(s.x2, 3), round(s.y2, 3))
            key = min(key, (key[2], key[3], key[0], key[1]))
            if key not in seen:
                seen.add(key)
                self.segments.append(s)
        self.extra_endpoints = list(extra_endpoints)

        self._grid: dict[tuple[int, int], list[int]] = {}
        for idx, s in enumerate(self.segments):
            for cell in self._cells_of(s):
                self._grid.setdefault(cell, []).append(idx)

    @classmethod
    def from_page(cls, page) -> "PdfVectorIndex":
        """Extrahiert Segmente und Kreisboegen aus einer PyMuPDF-Seite.

        Kreise/Boegen liegen in PDFs als Bezierketten vor: pro Kurvenstueck
        wird ein Kreis gefittet, aufeinanderfolgende ko-zirkulare Stuecke
        werden zu einem Bogen verkettet (Vollkreis = 4 Beziers -> 2*pi).
        """
        segments: list[Segment] = []
        endpoints: list[Point2] = []
        arcs: list[PlanArc] = []

        def add(p, q) -> None:
            segments.append(Segment(p.x, p.y, q.x, q.y))

        for path in page.get_drawings():
            chain: Optional[dict] = None

            def flush() -> None:
                nonlocal chain
                if chain is not None:
                    arcs.append(PlanArc(chain["cx"], chain["cy"], chain["r"],
                                        chain["a0"], chain["sweep"]))
                    chain = None

            for item in path["items"]:
                op = item[0]
                if op == "l":
                    flush()
                    add(item[1], item[2])
                elif op == "re":
                    flush()
                    r = item[1]
                    import pymupdf
                    a = pymupdf.Point(r.x0, r.y0)
                    b = pymupdf.Point(r.x1, r.y0)
                    c = pymupdf.Point(r.x1, r.y1)
                    d = pymupdf.Point(r.x0, r.y1)
                    add(a, b); add(b, c); add(c, d); add(d, a)
                elif op == "qu":
                    flush()
                    q = item[1]
                    add(q.ul, q.ur); add(q.ur, q.lr)
                    add(q.lr, q.ll); add(q.ll, q.ul)
                elif op == "c":
                    samples = _bezier_points(item[1], item[2],
                                             item[3], item[4])
                    fit = _fit_arc(samples)
                    if fit is None:
                        # Freiformkurve: nur Endpunkte als Fangpunkte
                        flush()
                        endpoints.append(samples[0])
                        endpoints.append(samples[-1])
                        continue
                    center, r, sweep = fit
                    tol = max(0.5, 0.01 * r)
                    if (chain is not None
                            and abs(center.x - chain["cx"]) < tol
                            and abs(center.y - chain["cy"]) < tol
                            and abs(r - chain["r"]) < tol
                            and samples[0].dist(chain["end"]) < 0.1
                            and sweep * chain["sweep"] >= 0
                            and abs(chain["sweep"] + sweep) <= TWO_PI + 1e-6):
                        chain["sweep"] += sweep
                        chain["end"] = samples[-1]
                    else:
                        flush()
                        a0 = math.atan2(samples[0].y - center.y,
                                        samples[0].x - center.x)
                        chain = {"cx": center.x, "cy": center.y, "r": r,
                                 "a0": a0, "sweep": sweep,
                                 "end": samples[-1]}
            flush()
        return cls(segments, endpoints, arcs)

    @property
    def has_content(self) -> bool:
        return bool(self.segments or self.extra_endpoints or self.arcs)

    # --- Abfragen (alle Radien in PDF-Punkten) --------------------------------
    def _cells_of(self, s: Segment):
        c = self.CELL
        i0, i1 = sorted((int(s.x1 // c), int(s.x2 // c)))
        j0, j1 = sorted((int(s.y1 // c), int(s.y2 // c)))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                yield (i, j)

    def segments_near(self, pos: Point2, radius: float) -> list[Segment]:
        c = self.CELL
        i0 = int((pos.x - radius) // c)
        i1 = int((pos.x + radius) // c)
        j0 = int((pos.y - radius) // c)
        j1 = int((pos.y + radius) // c)
        idxs: set[int] = set()
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                idxs.update(self._grid.get((i, j), ()))
        return [self.segments[i] for i in idxs]

    def endpoints_near(self, pos: Point2, radius: float) -> list[Point2]:
        out = []
        for s in self.segments_near(pos, radius):
            for p in (s.p1, s.p2):
                if p.dist(pos) <= radius:
                    out.append(p)
        for p in self.extra_endpoints:
            if p.dist(pos) <= radius:
                out.append(p)
        for arc in self.arcs:
            if not arc.is_full_circle:
                for p in (arc.start, arc.end):
                    if p.dist(pos) <= radius:
                        out.append(p)
        return out

    def intersections_near(self, pos: Point2, radius: float) -> list[Point2]:
        """Schnittpunkte der Segmente in Cursornaehe (paarweise, lazy)."""
        segs = self.segments_near(pos, radius + self.CELL)
        out: list[Point2] = []
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                p = seg_intersection(segs[i], segs[j])
                if p is not None and p.dist(pos) <= radius:
                    # Segment-Endpunkte sind schon Endpunkt-Kandidaten
                    if not any(p.dist(e) < 1e-6
                               for s in (segs[i], segs[j])
                               for e in (s.p1, s.p2)):
                        out.append(p)
        return out

    def nearest_segment(self, pos: Point2,
                        radius: float) -> Optional[Segment]:
        best, best_d = None, radius
        for s in self.segments_near(pos, radius):
            d = s.closest_point(pos).dist(pos)
            if d <= best_d:
                best, best_d = s, d
        return best

    def nearest_arc(self, pos: Point2, radius: float) -> Optional[PlanArc]:
        best, best_d = None, radius
        for arc in self.arcs:
            # grobe Vorauswahl ueber den Kreisring, dann exakte Distanz
            if abs(Point2(arc.cx, arc.cy).dist(pos) - arc.r) > radius + 1:
                continue
            d = arc.distance(pos)
            if d <= best_d:
                best, best_d = arc, d
        return best
