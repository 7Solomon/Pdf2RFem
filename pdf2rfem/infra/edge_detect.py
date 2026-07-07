"""OpenCV-Bilderkennung: Ecken-Fallback fuer gescannte Plaene und
Flaechenerkennung per Grauton-Flood-Fill.

Bewusst reaktiv statt global: gerendert und analysiert wird immer nur ein
kleiner Ausschnitt um Cursor bzw. Klick, nie der ganze Plan.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from ..core.transform import Point2
from .pdf_document import PdfDocument, RenderedPage

REGION_PIXEL_BUDGET = 6e6
REGION_MAX_ZOOM = 8.0


def _to_gray(rp: RenderedPage) -> np.ndarray:
    arr = np.frombuffer(rp.samples, dtype=np.uint8)
    arr = arr.reshape(rp.height, rp.stride)[:, : rp.width * 3]
    arr = arr.reshape(rp.height, rp.width, 3)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)


def find_corners(pdf: PdfDocument, page_index: int, center: Point2,
                 radius_pt: float) -> list[Point2]:
    """Ecken (Linienenden, Kreuzungen) in einem kleinen Patch um den Cursor.

    Fallback fuer gescannte PDFs ohne Vektordaten. Shi-Tomasi-Ecken plus
    Subpixel-Verfeinerung - damit snappt man genauer, als man klicken kann.
    """
    half = max(radius_pt * 3.0, 20.0)
    zoom = min(8.0, 400.0 / (2.0 * half))
    rp = pdf.render_region(page_index, zoom,
                           (center.x - half, center.y - half,
                            center.x + half, center.y + half))
    if rp.width < 8 or rp.height < 8:
        return []
    gray = _to_gray(rp)
    corners = cv2.goodFeaturesToTrack(
        gray, maxCorners=15, qualityLevel=0.05,
        minDistance=max(3.0, zoom * 1.5), blockSize=5)
    if corners is None:
        return []
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.03)
    corners = cv2.cornerSubPix(gray, corners.astype(np.float32),
                               (4, 4), (-1, -1), criteria)
    out = []
    for (px, py) in corners.reshape(-1, 2):
        x, y = rp.px_to_plan(float(px) + 0.5, float(py) + 0.5)
        out.append(Point2(x, y))
    return out


@dataclass
class RegionResult:
    polygon: list[Point2]      # vereinfachter Umriss in Plan-Koordinaten
    touched_border: bool       # Flaeche lief aus dem gerenderten Ausschnitt


def detect_region(pdf: PdfDocument, page_index: int, seed: Point2,
                  clip: tuple[float, float, float, float],
                  tolerance: int = 12) -> Optional[RegionResult]:
    """Zusammenhaengende Flaeche gleichen Farbtons um den Klickpunkt.

    Flood-Fill mit Grauwert-Toleranz, Konturverfolgung, Douglas-Peucker-
    Vereinfachung. `clip` ist ueblicherweise der sichtbare Canvas-Bereich -
    fuer grosse Flaechen also erst passend zoomen.
    """
    w = max(clip[2] - clip[0], 1.0)
    h = max(clip[3] - clip[1], 1.0)
    zoom = min(REGION_MAX_ZOOM, math.sqrt(REGION_PIXEL_BUDGET / (w * h)))
    rp = pdf.render_region(page_index, zoom, clip)
    if rp.width < 4 or rp.height < 4:
        return None
    gray = _to_gray(rp)

    sx = int((seed.x - rp.origin_x) * rp.zoom)
    sy = int((seed.y - rp.origin_y) * rp.zoom)
    if not (0 <= sx < rp.width and 0 <= sy < rp.height):
        return None

    mask = np.zeros((rp.height + 2, rp.width + 2), dtype=np.uint8)
    flags = (4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE
             | (255 << 8))
    cv2.floodFill(gray, mask, (sx, sy), 0,
                  loDiff=(tolerance,), upDiff=(tolerance,), flags=flags)
    region = mask[1:-1, 1:-1]

    touched = bool(region[0, :].any() or region[-1, :].any()
                   or region[:, 0].any() or region[:, -1].any())

    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 9:
        return None
    approx = cv2.approxPolyDP(contour, epsilon=2.5, closed=True)
    if len(approx) < 3:
        return None
    polygon = []
    for (px, py) in approx.reshape(-1, 2):
        x, y = rp.px_to_plan(float(px) + 0.5, float(py) + 0.5)
        polygon.append(Point2(x, y))
    return RegionResult(polygon, touched)
