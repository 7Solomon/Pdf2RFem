"""Kreisbogen-Geometrie (Qt-frei).

Ein Bogen ist definiert durch Start- und Endpunkt plus einen Kontrollpunkt,
der AUF dem Bogen liegt - dieselbe Definition, die RFEM fuer Arc-Linien
verwendet (arc_control_point). Alle Winkel in Radiant, Plan-Koordinaten
(y nach unten); "sweep" ist der vorzeichenbehaftete Winkelweg vom Start.
"""
from __future__ import annotations

import math
from typing import Optional

from .transform import Point2

TWO_PI = 2.0 * math.pi


def circle_from_3_points(p1: Point2, p2: Point2,
                         p3: Point2) -> Optional[tuple[Point2, float]]:
    """Umkreis durch drei Punkte; None bei (nahezu) kollinearen Punkten."""
    ax, ay, bx, by, cx, cy = p1.x, p1.y, p2.x, p2.y, p3.x, p3.y
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    center = Point2(ux, uy)
    return center, center.dist(p1)


def arc_sweep(center: Point2, start: Point2, control: Point2,
              end: Point2) -> float:
    """Vorzeichenbehafteter Winkelweg von start nach end, so dass der Bogen
    durch control laeuft (positiv = im Uhrzeigersinn bei y nach unten)."""
    a0 = math.atan2(start.y - center.y, start.x - center.x)
    a1 = math.atan2(end.y - center.y, end.x - center.x)
    ac = math.atan2(control.y - center.y, control.x - center.x)
    d_end = (a1 - a0) % TWO_PI
    d_ctrl = (ac - a0) % TWO_PI
    return d_end if d_ctrl <= d_end else d_end - TWO_PI


def sample_arc(start: Point2, control: Point2, end: Point2,
               n: int = 48) -> list[Point2]:
    """Bogen als Punktfolge (fuer Darstellung und Trefferpruefung).

    Fallback auf die Sehne, wenn die drei Punkte kollinear sind.
    """
    fit = circle_from_3_points(start, control, end)
    if fit is None:
        return [start, end]
    center, r = fit
    a0 = math.atan2(start.y - center.y, start.x - center.x)
    sweep = arc_sweep(center, start, control, end)
    pts = []
    for i in range(n + 1):
        a = a0 + sweep * i / n
        pts.append(Point2(center.x + r * math.cos(a),
                          center.y + r * math.sin(a)))
    return pts


def arc_midpoint(start: Point2, control: Point2, end: Point2) -> Point2:
    """Punkt in der Mitte des Bogens (praktisch als Kontrollpunkt)."""
    fit = circle_from_3_points(start, control, end)
    if fit is None:
        return Point2((start.x + end.x) / 2.0, (start.y + end.y) / 2.0)
    center, r = fit
    a0 = math.atan2(start.y - center.y, start.x - center.x)
    a = a0 + arc_sweep(center, start, control, end) / 2.0
    return Point2(center.x + r * math.cos(a), center.y + r * math.sin(a))


def dist_point_arc(p: Point2, start: Point2, control: Point2,
                   end: Point2) -> float:
    """Abstand Punkt-Bogen ueber die exakte Kreisgleichung."""
    fit = circle_from_3_points(start, control, end)
    if fit is None:
        # kollinear: Abstand zur Sehne
        from .geometry import _dist_point_segment
        return _dist_point_segment(p, start, end)
    center, r = fit
    a = math.atan2(p.y - center.y, p.x - center.x)
    a0 = math.atan2(start.y - center.y, start.x - center.x)
    sweep = arc_sweep(center, start, control, end)
    d = (a - a0) % TWO_PI if sweep >= 0 else -((a0 - a) % TWO_PI)
    if abs(d) <= abs(sweep):
        return abs(center.dist(p) - r)
    return min(p.dist(start), p.dist(end))
