"""PyMuPDF-Kapselung: einzige Stelle, die PDF rendert und Seitenmasse liefert.

Alle Plan-Koordinaten im Programm sind PyMuPDF-Seitenkoordinaten in
PDF-Punkten (beruecksichtigt CropBox und Seitenrotation bereits, weil
page.rect und get_pixmap dieselbe sichtbare, rotierte Seite beschreiben).
Gibt rohe RGB-Bytes zurueck, damit der Kern Qt-frei bleibt.
"""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf


@dataclass
class RenderedPage:
    samples: bytes      # RGB888, zeilenweise
    width: int          # Pixel
    height: int
    stride: int
    zoom: float         # Pixel pro PDF-Punkt
    origin_x: float = 0.0   # linke obere Ecke des Ausschnitts in PDF-Punkten
    origin_y: float = 0.0

    def px_to_plan(self, px: float, py: float) -> tuple[float, float]:
        return (self.origin_x + px / self.zoom, self.origin_y + py / self.zoom)


class PdfDocument:
    MAX_RENDER_ZOOM = 10.0   # Schutz gegen riesige Pixmaps bei tiefem Zoom

    def __init__(self, path: str) -> None:
        self.path = path
        self.doc = pymupdf.open(path)
        if self.doc.needs_pass:
            raise ValueError("Passwortgeschuetzte PDFs werden nicht unterstuetzt")

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def page_size(self, page_index: int) -> tuple[float, float]:
        """Sichtbare Seitengroesse in PDF-Punkten (Breite, Hoehe)."""
        r = self.doc[page_index].rect
        return (r.width, r.height)

    def render_page(self, page_index: int, zoom: float) -> RenderedPage:
        zoom = max(0.1, min(zoom, self.MAX_RENDER_ZOOM))
        page = self.doc[page_index]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return RenderedPage(bytes(pix.samples), pix.width, pix.height,
                            pix.stride, zoom)

    def render_region(self, page_index: int, zoom: float,
                      clip: tuple[float, float, float, float]) -> RenderedPage:
        """Rendert nur einen Seitenausschnitt (x0, y0, x1, y1 in PDF-Punkten).

        Damit kann die Canvas beim Hineinzoomen den sichtbaren Bereich scharf
        nachrendern, ohne die ganze Seite in hoher Aufloesung zu erzeugen.
        """
        page = self.doc[page_index]
        rect = pymupdf.Rect(*clip) & page.rect
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                              clip=rect, alpha=False)
        return RenderedPage(bytes(pix.samples), pix.width, pix.height,
                            pix.stride, zoom, rect.x0, rect.y0)

    def close(self) -> None:
        self.doc.close()
