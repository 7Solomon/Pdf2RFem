"""Fuellen-Werkzeug: kleinste vom Liniengraphen eingeschlossene Flaeche um
einen Klickpunkt finden - inklusive innerer Loecher/Aussparungen (Cutouts).

Ansatz: planare Flaechen-Traversierung (Half-Edge, "naechste Kante gegen
den Uhrzeigersinn") liefert alle geschlossenen Zyklen. Ueber einen
Verschachtelungs-Baum (welcher Zyklus liegt in welchem) wird die vom Klick
getroffene Aussenkontur bestimmt und ihre direkten Kind-Zyklen werden als
Aussparungen abgezogen. Linien zaehlen nur als verbunden, wenn sie
denselben Knoten teilen - deshalb ist die Knoten-Wiederverwendung beim
Zeichnen/Abgreifen entscheidend. Qt-frei und direkt testbar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .arcs import sample_arc
from .transform import Point2


@dataclass
class _Edge:
    obj_id: str             # ID der GeoPolyline bzw. des GeoArc
    seg_idx: int            # Segmentindex innerhalb der Polylinie (Arc: 0)
    a: str                  # Knoten-IDs
    b: str
    samples: list[Point2]   # Geometrie von a nach b (Bogen: abgetastet)
    is_arc: bool


@dataclass
class BoundaryEdge:
    obj_id: str
    from_id: str
    to_id: str
    is_arc: bool


@dataclass
class Loop:
    """Ein geschlossener Randzug (Aussen- oder Lochkontur)."""
    boundary: list[BoundaryEdge]
    polygon: list[Point2]     # abgetasteter Umriss
    area: float               # |Flaeche| in PDF-Punkten^2

    @property
    def boundary_ids(self) -> list[str]:
        out: list[str] = []
        for e in self.boundary:
            if not out or out[-1] != e.obj_id:
                out.append(e.obj_id)
        if len(out) > 1 and out[0] == out[-1]:
            out.pop()
        return out

    @property
    def node_ids(self) -> list[str]:
        return [e.from_id for e in self.boundary]

    @property
    def has_arcs(self) -> bool:
        return any(e.is_arc for e in self.boundary)


@dataclass
class FaceResult:
    outer: Loop
    holes: list[Loop] = field(default_factory=list)
    partial_objects: list[str] = field(default_factory=list)
    loose_ends: list[Point2] = field(default_factory=list)

    # --- Kompatible Kurzzugriffe (frueher direkt auf FaceResult) ---
    @property
    def boundary(self) -> list[BoundaryEdge]:
        return self.outer.boundary

    @property
    def boundary_ids(self) -> list[str]:
        return self.outer.boundary_ids

    @property
    def node_ids(self) -> list[str]:
        return self.outer.node_ids

    @property
    def hole_boundary_ids(self) -> list[list[str]]:
        return [h.boundary_ids for h in self.holes]

    @property
    def has_arcs(self) -> bool:
        return self.outer.has_arcs or any(h.has_arcs for h in self.holes)

    @property
    def area(self) -> float:
        """Netto-Flaeche: Aussenkontur minus Loecher."""
        return self.outer.area - sum(h.area for h in self.holes)


def _collect_edges(model, view_id: str) -> list[_Edge]:
    edges: list[_Edge] = []
    seen: set[tuple] = set()
    for line in model.lines_in_view(view_id):
        ids = line.point_ids
        pairs = list(zip(ids, ids[1:]))
        if line.closed and len(ids) > 2:
            pairs.append((ids[-1], ids[0]))
        for i, (a, b) in enumerate(pairs):
            if a == b:
                continue
            key = (min(a, b), max(a, b), "l")
            if key in seen:
                continue  # doppelte Kanten wuerden Scheinflaechen erzeugen
            seen.add(key)
            edges.append(_Edge(line.id, i, a, b,
                               [model.points[a].pos, model.points[b].pos],
                               False))
    for arc in model.arcs_in_view(view_id):
        a, b = arc.point_ids
        if a == b:
            continue
        key = (min(a, b), max(a, b), "a",
               round(arc.control.x, 3), round(arc.control.y, 3))
        if key in seen:
            continue
        seen.add(key)
        edges.append(_Edge(arc.id, 0, a, b,
                           sample_arc(model.points[a].pos, arc.control,
                                      model.points[b].pos, n=24), True))
    return edges


def _segment_count(model, obj_id: str) -> int:
    if obj_id in model.arcs:
        return 1
    line = model.lines[obj_id]
    n = len(line.point_ids) - 1
    if line.closed and len(line.point_ids) > 2:
        n += 1
    return n


def _contains(poly: list[Point2], p: Point2) -> bool:
    """Punkt-in-Polygon (even-odd)."""
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a.y > p.y) != (b.y > p.y):
            x_int = a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)
            if x_int > p.x:
                inside = not inside
    return inside


def _area(poly: list[Point2]) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        s += a.x * b.y - b.x * a.y
    return s / 2.0


def _interior_point(poly: list[Point2]) -> Point2:
    """Ein Punkt garantiert im Inneren des einfachen Polygons (Strahlen-
    verfahren ueber die Mitte einer horizontalen Sekante)."""
    cy = sum(p.y for p in poly) / len(poly)
    xs = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a.y > cy) != (b.y > cy):
            xs.append(a.x + (cy - a.y) * (b.x - a.x) / (b.y - a.y))
    xs.sort()
    if len(xs) >= 2:
        return Point2((xs[0] + xs[1]) / 2.0, cy)
    cx = sum(p.x for p in poly) / len(poly)
    return Point2(cx, cy)


def _extract_loops(model, view_id: str) -> list[Loop]:
    """Alle eindeutigen geschlossenen Randzuege des Liniengraphen."""
    edges = _collect_edges(model, view_id)
    if not edges:
        return []

    # Halbkanten: Index 2i = a->b, 2i+1 = b->a (Partner = Index ^ 1)
    half_from: list[str] = []
    half_to: list[str] = []
    half_ang: list[float] = []
    out_by_node: dict[str, list[int]] = {}
    for e in edges:
        for fwd in (True, False):
            s = e.samples if fwd else list(reversed(e.samples))
            ang = math.atan2(s[1].y - s[0].y, s[1].x - s[0].x)
            h = len(half_from)
            half_from.append(e.a if fwd else e.b)
            half_to.append(e.b if fwd else e.a)
            half_ang.append(ang)
            out_by_node.setdefault(half_from[-1], []).append(h)
    for lst in out_by_node.values():
        lst.sort(key=lambda h: half_ang[h])
    pos_at_node = {h: i for lst in out_by_node.values()
                   for i, h in enumerate(lst)}

    def next_half(h: int) -> int:
        lst = out_by_node[half_to[h]]
        k = pos_at_node[h ^ 1]
        return lst[(k + 1) % len(lst)]

    def half_samples(h: int) -> list[Point2]:
        e = edges[h // 2]
        return e.samples if h % 2 == 0 else list(reversed(e.samples))

    visited: set[int] = set()
    loops: list[Loop] = []
    seen_edge_sets: set[frozenset] = set()
    for start in range(len(half_from)):
        if start in visited:
            continue
        cycle: list[int] = []
        h = start
        while h not in visited:
            visited.add(h)
            cycle.append(h)
            h = next_half(h)
        if h != start or not cycle:
            continue
        # Stichleitungen (Kante + Gegenkante im selben Umlauf) sind
        # Sackgassen und gehoeren nicht zum umschliessenden Rand
        cyc_set = set(cycle)
        pruned = [hh for hh in cycle if (hh ^ 1) not in cyc_set]
        if len(pruned) < 2:   # 2 erlaubt: Sehne + Bogen (Linse)
            continue
        # Aussen- und Innenumlauf desselben Randes tragen dieselben
        # ungerichteten Kanten -> nur einmal behalten
        key = frozenset(hh // 2 for hh in pruned)
        if key in seen_edge_sets:
            continue
        seen_edge_sets.add(key)

        poly: list[Point2] = []
        for hh in pruned:
            poly.extend(half_samples(hh)[:-1])
        if len(poly) < 3:
            continue
        area = abs(_area(poly))
        if area < 1e-9:
            continue
        boundary = [BoundaryEdge(edges[hh // 2].obj_id, half_from[hh],
                                 half_to[hh], edges[hh // 2].is_arc)
                    for hh in pruned]
        loops.append(Loop(boundary, poly, area))
    return loops


def _loose_ends(model, view_id: str) -> list[Point2]:
    """Endpunkte, an denen nur eine Linie/ein Bogen haengt (moegliche
    Luecke im Randzug)."""
    deg: dict[str, int] = {}
    for e in _collect_edges(model, view_id):
        deg[e.a] = deg.get(e.a, 0) + 1
        deg[e.b] = deg.get(e.b, 0) + 1
    return [model.points[n].pos for n, d in deg.items() if d < 2]


def find_enclosing_cycle(model, view_id: str,
                         click: Point2) -> Optional[FaceResult]:
    """Kleinste vom Liniengraphen eingeschlossene Flaeche um den Klickpunkt,
    inklusive direkt darin liegender Loecher (Aussparungen).

    None, wenn der Punkt in keinem geschlossenen Zug liegt.
    """
    loops = _extract_loops(model, view_id)
    if not loops:
        return None

    reps = [_interior_point(lp.polygon) for lp in loops]

    # Aussenkontur = kleinster Zyklus, der den Klick enthaelt
    containing = [i for i, lp in enumerate(loops)
                  if _contains(lp.polygon, click)]
    if not containing:
        return None
    outer_i = min(containing, key=lambda i: loops[i].area)
    outer = loops[outer_i]

    # Direkte Kinder: Zyklen in outer, aber in keinem anderen Zyklus, der
    # selbst noch in outer liegt (echte Aussparungen, keine Inseln)
    inside_outer = [i for i in range(len(loops))
                    if i != outer_i and loops[i].area < outer.area
                    and _contains(outer.polygon, reps[i])]
    holes: list[Loop] = []
    for i in inside_outer:
        parent = outer_i
        for j in inside_outer:
            if (j != i and loops[j].area < loops[parent].area
                    and loops[j].area > loops[i].area
                    and _contains(loops[j].polygon, reps[i])):
                parent = j
        if parent == outer_i:
            holes.append(loops[i])

    # Objekte, die nur mit einem Teil ihrer Segmente am Rand liegen
    boundary_edges = list(outer.boundary)
    for h in holes:
        boundary_edges.extend(h.boundary)
    seg_idx_of: dict[tuple, int] = {}
    for e in _collect_edges(model, view_id):
        seg_idx_of[(e.obj_id, e.a, e.b)] = e.seg_idx
        seg_idx_of[(e.obj_id, e.b, e.a)] = e.seg_idx
    used_segidx: dict[str, set[int]] = {}
    for be in boundary_edges:
        idx = seg_idx_of.get((be.obj_id, be.from_id, be.to_id), 0)
        used_segidx.setdefault(be.obj_id, set()).add(idx)
    partial = [oid for oid, segs in used_segidx.items()
               if len(segs) < _segment_count(model, oid)]

    # Lose Enden innerhalb der Bounding-Box der Aussenkontur melden
    xs = [p.x for p in outer.polygon]
    ys = [p.y for p in outer.polygon]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    loose = [p for p in _loose_ends(model, view_id)
             if bbox[0] - 5 <= p.x <= bbox[2] + 5
             and bbox[1] - 5 <= p.y <= bbox[3] + 5]

    if len(set(outer.node_ids)) < (2 if outer.has_arcs else 3):
        return None
    return FaceResult(outer, holes, partial, loose)


# --- Hilfen fuer gespeicherte Flaechen (GeoSurface) ----------------------------

def boundary_path_points(model, boundary_ids: list[str],
                         n_arc: int = 48) -> Optional[list[Point2]]:
    """Geschlossener Umriss einer Flaeche aus ihren Randobjekten.

    Verkettet die Objekte ueber gemeinsame Endknoten; None, wenn die Kette
    nicht schliesst (z.B. weil ein Randobjekt geloescht wurde).
    """
    if not boundary_ids:
        return None

    def endpoints(oid: str) -> tuple[str, str]:
        if oid in model.arcs:
            a, b = model.arcs[oid].point_ids
            return a, b
        line = model.lines[oid]
        return line.point_ids[0], line.point_ids[-1]

    def geometry(oid: str, forward: bool) -> list[Point2]:
        if oid in model.arcs:
            arc = model.arcs[oid]
            a, b = (model.points[pid].pos for pid in arc.point_ids)
            pts = sample_arc(a, arc.control, b, n=n_arc)
        else:
            line = model.lines[oid]
            pts = [model.points[pid].pos for pid in line.point_ids]
            if line.closed and len(pts) > 2:
                pts = pts + [pts[0]]
        return pts if forward else list(reversed(pts))

    for oid in boundary_ids:
        if oid not in model.arcs and oid not in model.lines:
            return None

    if len(boundary_ids) == 1:
        oid = boundary_ids[0]
        if oid in model.lines and model.lines[oid].closed:
            return geometry(oid, True)[:-1]
        return None

    # Orientierung des ersten Objekts: sein "hinteres" Ende muss ans
    # naechste Objekt anschliessen
    a0, b0 = endpoints(boundary_ids[0])
    n1 = set(endpoints(boundary_ids[1]))
    if b0 in n1:
        current, path = b0, geometry(boundary_ids[0], True)
    elif a0 in n1:
        current, path = a0, geometry(boundary_ids[0], False)
    else:
        return None
    for oid in boundary_ids[1:]:
        a, b = endpoints(oid)
        if a == current:
            path.extend(geometry(oid, True)[1:])
            current = b
        elif b == current:
            path.extend(geometry(oid, False)[1:])
            current = a
        else:
            return None
    if path[0].dist(path[-1]) > 1e-6:
        return None
    return path[:-1]


def surface_outline(model, surface, n_arc: int = 48
                    ) -> Optional[tuple[list[Point2], list[list[Point2]]]]:
    """(Aussenumriss, [Lochumrisse]) einer gespeicherten Flaeche."""
    outer = boundary_path_points(model, surface.boundary_ids, n_arc)
    if outer is None:
        return None
    holes = []
    for hole_ids in getattr(surface, "opening_ids", []) or []:
        h = boundary_path_points(model, hole_ids, n_arc)
        if h is not None:
            holes.append(h)
    return outer, holes


def surface_contains(model, surface, pos: Point2) -> bool:
    result = surface_outline(model, surface, n_arc=24)
    if result is None:
        return False
    outer, holes = result
    if not _contains(outer, pos):
        return False
    return not any(_contains(h, pos) for h in holes)


def surface_area(model, surface) -> Optional[float]:
    result = surface_outline(model, surface)
    if result is None:
        return None
    outer, holes = result
    return abs(_area(outer)) - sum(abs(_area(h)) for h in holes)
