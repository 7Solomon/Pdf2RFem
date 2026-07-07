"""Transformationskette: PDF-Punkt (1/72 Zoll) -> Papier-mm -> reale RFEM-Koordinate.

Konventionen:
- Plan-Koordinaten sind PyMuPDF-Seitenkoordinaten in PDF-Punkten:
  Ursprung oben links, x nach rechts, y nach UNTEN.
- Papierrichtungen: u = nach rechts, v = nach OBEN (daher Vorzeichenwechsel bei y).
- RFEM-Achsen: 0=X, 1=Y, 2=Z (Z zeigt in RFEM standardmaessig nach unten,
  deshalb bilden die Ansicht-Presets v auf -Z ab).
"""
from __future__ import annotations

from dataclasses import dataclass

MM_PER_PDF_POINT = 25.4 / 72.0

AXIS_NAMES = ("X", "Y", "Z")


def meters_per_pdf_point(scale_denominator: float) -> float:
    """Umrechnungsfaktor PDF-Punkt -> reale Meter bei Nennmassstab 1:n."""
    if scale_denominator <= 0:
        raise ValueError("Massstab muss positiv sein")
    return MM_PER_PDF_POINT * scale_denominator / 1000.0


def implied_scale(pdf_distance_pt: float, real_length_m: float) -> float:
    """Massstabs-Nenner, der sich aus einer gemessenen Strecke ergibt (Verifikation)."""
    paper_m = pdf_distance_pt * MM_PER_PDF_POINT / 1000.0
    if paper_m <= 0:
        raise ValueError("Messstrecke hat Laenge 0")
    return real_length_m / paper_m


@dataclass(frozen=True)
class Point2:
    """2D-Punkt in PDF-Punkten (Seitenkoordinaten)."""
    x: float
    y: float

    def dist(self, other: "Point2") -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5


@dataclass
class Workplane:
    """Abbildung der Papierrichtungen u (rechts) / v (oben) auf RFEM-Achsen.

    axis_u/axis_v: Achsindex 0=X, 1=Y, 2=Z; sign_u/sign_v: +1 oder -1.
    Die dritte (feste) Achse behaelt den Wert des Referenzpunkt-Ziels.
    """
    axis_u: int = 0
    sign_u: int = 1
    axis_v: int = 1
    sign_v: int = 1

    # Preset-Name -> (axis_u, sign_u, axis_v, sign_v)
    PRESETS = {
        "XY (Grundriss)": (0, 1, 1, 1),
        "XZ (Ansicht/Laengsschnitt)": (0, 1, 2, -1),
        "YZ (Querschnitt)": (1, 1, 2, -1),
    }

    def __post_init__(self) -> None:
        if self.axis_u == self.axis_v:
            raise ValueError("u- und v-Achse muessen verschieden sein")
        if self.sign_u not in (1, -1) or self.sign_v not in (1, -1):
            raise ValueError("Vorzeichen muss +1 oder -1 sein")

    @classmethod
    def from_preset(cls, name: str) -> "Workplane":
        return cls(*cls.PRESETS[name])

    @property
    def fixed_axis(self) -> int:
        return ({0, 1, 2} - {self.axis_u, self.axis_v}).pop()

    def describe(self) -> str:
        su = "+" if self.sign_u > 0 else "-"
        sv = "+" if self.sign_v > 0 else "-"
        return (f"u→{su}{AXIS_NAMES[self.axis_u]}, "
                f"v→{sv}{AXIS_NAMES[self.axis_v]}, "
                f"{AXIS_NAMES[self.fixed_axis]} fest")

    def to_dict(self) -> dict:
        return {"axis_u": self.axis_u, "sign_u": self.sign_u,
                "axis_v": self.axis_v, "sign_v": self.sign_v}

    @classmethod
    def from_dict(cls, d: dict) -> "Workplane":
        return cls(d["axis_u"], d["sign_u"], d["axis_v"], d["sign_v"])


class ViewTransform:
    """Rechnet Plan-Koordinaten einer Ansicht in RFEM-Koordinaten um (und zurueck)."""

    def __init__(self, workplane: Workplane, ref_pdf: Point2,
                 ref_target: tuple[float, float, float],
                 scale_denominator: float) -> None:
        self.workplane = workplane
        self.ref_pdf = ref_pdf
        self.ref_target = ref_target
        self.m_per_pt = meters_per_pdf_point(scale_denominator)

    def pdf_to_rfem(self, p: Point2) -> tuple[float, float, float]:
        u = (p.x - self.ref_pdf.x) * self.m_per_pt
        v = (self.ref_pdf.y - p.y) * self.m_per_pt  # PDF-y zeigt nach unten
        c = list(self.ref_target)
        wp = self.workplane
        c[wp.axis_u] += wp.sign_u * u
        c[wp.axis_v] += wp.sign_v * v
        return (c[0], c[1], c[2])

    def rfem_to_pdf(self, xyz: tuple[float, float, float]) -> Point2:
        """Projektion einer RFEM-Koordinate zurueck in die Ansichtsebene."""
        wp = self.workplane
        u = (xyz[wp.axis_u] - self.ref_target[wp.axis_u]) * wp.sign_u
        v = (xyz[wp.axis_v] - self.ref_target[wp.axis_v]) * wp.sign_v
        return Point2(self.ref_pdf.x + u / self.m_per_pt,
                      self.ref_pdf.y - v / self.m_per_pt)

    def pdf_dist_to_meters(self, d_pt: float) -> float:
        return d_pt * self.m_per_pt
