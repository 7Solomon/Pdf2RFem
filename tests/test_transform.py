"""Tests der Transformationskette - dem risikoreichsten Teil des Programms."""
import math

import pytest

from pdf2rfem.core.transform import (MM_PER_PDF_POINT, Point2, ViewTransform,
                                     Workplane, implied_scale,
                                     meters_per_pdf_point)


def test_meters_per_pdf_point_1_50():
    # 1 PDF-Punkt = 25.4/72 mm Papier; bei 1:50 -> * 50 / 1000 m
    assert meters_per_pdf_point(50) == pytest.approx(0.352777778 * 50 / 1000)


def test_meters_per_pdf_point_invalid():
    with pytest.raises(ValueError):
        meters_per_pdf_point(0)


def test_implied_scale():
    # 100 pt Papier, real 1.7638...m -> Massstab 50
    d_pt = 100.0
    real = d_pt * MM_PER_PDF_POINT / 1000 * 50
    assert implied_scale(d_pt, real) == pytest.approx(50)


def test_xy_grundriss_mapping():
    """u->+X, v->+Y; PDF-y zeigt nach unten, v nach oben."""
    tf = ViewTransform(Workplane.from_preset("XY (Grundriss)"),
                       ref_pdf=Point2(100, 200),
                       ref_target=(10.0, 20.0, 5.0),
                       scale_denominator=50)
    m = meters_per_pdf_point(50)
    # 10 pt nach rechts, 10 pt nach OBEN im Bild (y kleiner)
    x, y, z = tf.pdf_to_rfem(Point2(110, 190))
    assert x == pytest.approx(10.0 + 10 * m)
    assert y == pytest.approx(20.0 + 10 * m)
    assert z == pytest.approx(5.0)  # feste Achse unveraendert


def test_xz_ansicht_mapping():
    """Ansicht: v (oben auf Papier) -> -Z, weil RFEM-Z nach unten zeigt."""
    tf = ViewTransform(Workplane.from_preset("XZ (Ansicht/Laengsschnitt)"),
                       ref_pdf=Point2(0, 0),
                       ref_target=(0.0, 0.0, 0.0),
                       scale_denominator=100)
    m = meters_per_pdf_point(100)
    # Punkt 20 pt oberhalb des Referenzpunkts -> Z negativ (nach oben)
    x, y, z = tf.pdf_to_rfem(Point2(0, -20))
    assert x == pytest.approx(0.0)
    assert y == pytest.approx(0.0)
    assert z == pytest.approx(-20 * m)


def test_sign_flip():
    wp = Workplane(axis_u=0, sign_u=-1, axis_v=1, sign_v=1)
    tf = ViewTransform(wp, Point2(0, 0), (0, 0, 0), 50)
    x, _, _ = tf.pdf_to_rfem(Point2(10, 0))
    assert x < 0


def test_roundtrip():
    tf = ViewTransform(Workplane.from_preset("YZ (Querschnitt)"),
                       ref_pdf=Point2(300, 400),
                       ref_target=(1.0, 2.0, 3.0),
                       scale_denominator=25)
    p = Point2(345.6, 378.9)
    assert tf.rfem_to_pdf(tf.pdf_to_rfem(p)).dist(p) < 1e-9


def test_workplane_validation():
    with pytest.raises(ValueError):
        Workplane(axis_u=0, sign_u=1, axis_v=0, sign_v=1)
    with pytest.raises(ValueError):
        Workplane(axis_u=0, sign_u=2, axis_v=1, sign_v=1)


def test_workplane_dict_roundtrip():
    wp = Workplane(1, -1, 2, 1)
    wp2 = Workplane.from_dict(wp.to_dict())
    assert (wp2.axis_u, wp2.sign_u, wp2.axis_v, wp2.sign_v) == (1, -1, 2, 1)
    assert wp.fixed_axis == 0
