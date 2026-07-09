"""Programmatisch gezeichnete Toolbar-Icons (keine Asset-Dateien noetig).

Einheitlicher Stil: dunkelgraue Konturen, blauer Akzent fuer "aktive"
Elemente, gelber Akzent fuer Plan-Bezug. 32x32 px, cosmetic genug fuer
HiDPI dank QIcon-Skalierung.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF)

FG = QColor("#37474f")        # Grundfarbe Konturen
ACCENT = QColor("#1f77b4")    # Blau: eigenes/aktives Element
PLAN = QColor("#9e9e9e")      # Grau: der Plan im Hintergrund
WARN = QColor("#d62728")


def make_icon(name: str) -> QIcon:
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(FG, 2))
    _DRAW[name](p)
    p.end()
    return QIcon(pm)


def _dot(p: QPainter, x: float, y: float, r: float = 3,
         color: QColor = ACCENT) -> None:
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(color))
    p.drawEllipse(QPointF(x, y), r, r)
    p.restore()


def _select(p: QPainter) -> None:
    poly = QPolygonF([QPointF(10, 5), QPointF(10, 24), QPointF(15, 19),
                      QPointF(19, 27), QPointF(22, 25), QPointF(18, 18),
                      QPointF(24, 17)])
    p.setBrush(QBrush(FG))
    p.drawPolygon(poly)


def _point(p: QPainter) -> None:
    _dot(p, 16, 16, 4)
    p.setPen(QPen(FG, 1.5))
    p.drawLine(16, 5, 16, 10)
    p.drawLine(16, 22, 16, 27)
    p.drawLine(5, 16, 10, 16)
    p.drawLine(22, 16, 27, 16)


def _polyline(p: QPainter) -> None:
    p.setPen(QPen(ACCENT, 2))
    path = QPainterPath(QPointF(5, 25))
    path.lineTo(13, 9)
    path.lineTo(20, 20)
    path.lineTo(27, 7)
    p.drawPath(path)
    for x, y in ((5, 25), (13, 9), (20, 20), (27, 7)):
        _dot(p, x, y, 2.5, FG)


def _arc(p: QPainter) -> None:
    p.setPen(QPen(ACCENT, 2))
    path = QPainterPath(QPointF(6, 25))
    path.quadTo(16, -2, 26, 25)
    p.drawPath(path)
    for x, y in ((6, 25), (26, 25)):
        _dot(p, x, y, 2.5, FG)
    _dot(p, 16, 11, 2.5, WARN)


def _trace(p: QPainter) -> None:
    pen = QPen(PLAN, 2, Qt.DashLine)
    p.setPen(pen)
    p.drawLine(5, 10, 27, 10)
    p.drawLine(5, 17, 27, 17)
    p.setPen(QPen(ACCENT, 3))
    p.drawLine(5, 24, 27, 24)
    _dot(p, 5, 24, 2.5, FG)
    _dot(p, 27, 24, 2.5, FG)


def _region(p: QPainter) -> None:
    poly = QPolygonF([QPointF(6, 12), QPointF(18, 6), QPointF(27, 14),
                      QPointF(23, 26), QPointF(9, 25)])
    p.setBrush(QBrush(QColor(31, 119, 180, 70)))
    p.setPen(QPen(ACCENT, 2))
    p.drawPolygon(poly)
    for pt in poly:
        _dot(p, pt.x(), pt.y(), 2, FG)


def _fill(p: QPainter) -> None:
    poly = QPolygonF([QPointF(5, 13), QPointF(16, 5), QPointF(27, 13),
                      QPointF(24, 26), QPointF(8, 26)])
    p.setPen(QPen(FG, 2))
    p.drawPolygon(poly)
    # Farbtropfen in der Mitte
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(ACCENT))
    p.drawPolygon(QPolygonF([QPointF(16, 9), QPointF(12, 17),
                             QPointF(20, 17)]))
    p.drawEllipse(QPointF(16, 18.5), 4.2, 4.2)


def _merge(p: QPainter) -> None:
    # zwei Enden, die zu einem Knoten zusammenlaufen
    p.setPen(QPen(FG, 2))
    p.drawLine(4, 7, 15, 16)
    p.drawLine(4, 25, 15, 16)
    p.setPen(QPen(ACCENT, 2))
    p.drawLine(15, 16, 28, 16)
    _dot(p, 4, 7, 2.5, WARN)
    _dot(p, 4, 25, 2.5, WARN)
    _dot(p, 15, 16, 3.5, ACCENT)


def _refpoint(p: QPainter) -> None:
    p.setPen(QPen(WARN, 2))
    p.drawEllipse(QRectF(9, 9, 14, 14))
    p.drawLine(16, 4, 16, 28)
    p.drawLine(4, 16, 28, 16)


def _measure(p: QPainter) -> None:
    p.setPen(QPen(FG, 2))
    p.drawLine(6, 26, 26, 6)
    for t in (0.25, 0.5, 0.75):
        x = 6 + (26 - 6) * t
        y = 26 + (6 - 26) * t
        p.drawLine(QPointF(x - 2, y - 2), QPointF(x + 2, y + 2))
    _dot(p, 6, 26, 2.5, ACCENT)
    _dot(p, 26, 6, 2.5, ACCENT)


def _plansnap(p: QPainter) -> None:
    p.setPen(QPen(PLAN, 2))
    p.drawLine(4, 16, 28, 16)
    p.drawLine(16, 4, 16, 28)
    p.setPen(QPen(QColor("#ff4081"), 2))
    p.drawRect(QRectF(11, 11, 10, 10))


def _magnifier(p: QPainter) -> None:
    p.setPen(QPen(FG, 2.5))
    p.drawEllipse(QRectF(6, 6, 14, 14))
    p.drawLine(18, 18, 27, 27)
    p.setPen(QPen(ACCENT, 1.5))
    p.drawLine(13, 9, 13, 17)
    p.drawLine(9, 13, 17, 13)


def _fit(p: QPainter) -> None:
    p.drawRect(QRectF(8, 8, 16, 16))
    p.setPen(QPen(ACCENT, 2))
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        x0, y0 = 16 + dx * 5, 16 + dy * 5
        x1, y1 = 16 + dx * 11, 16 + dy * 11
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))


def _undo(p: QPainter) -> None:
    path = QPainterPath(QPointF(24, 22))
    path.quadTo(26, 10, 12, 10)
    p.drawPath(path)
    p.setBrush(QBrush(FG))
    p.drawPolygon(QPolygonF([QPointF(14, 4), QPointF(14, 16), QPointF(6, 10)]))


def _redo(p: QPainter) -> None:
    path = QPainterPath(QPointF(8, 22))
    path.quadTo(6, 10, 20, 10)
    p.drawPath(path)
    p.setBrush(QBrush(FG))
    p.drawPolygon(QPolygonF([QPointF(18, 4), QPointF(18, 16), QPointF(26, 10)]))


def _transfer(p: QPainter) -> None:
    p.setPen(QPen(FG, 2))
    p.drawRect(QRectF(16, 14, 12, 12))
    p.drawLine(22, 14, 22, 26)
    p.drawLine(16, 20, 28, 20)
    p.setPen(QPen(ACCENT, 2.5))
    p.drawLine(3, 8, 13, 8)
    p.setBrush(QBrush(ACCENT))
    p.setPen(Qt.NoPen)
    p.drawPolygon(QPolygonF([QPointF(12, 3), QPointF(12, 13), QPointF(18, 8)]))


def _connect(p: QPainter) -> None:
    p.setPen(QPen(FG, 2))
    p.drawEllipse(QRectF(5, 5, 22, 22))
    p.setPen(QPen(QColor("#2ca02c"), 3))
    path = QPainterPath(QPointF(10, 16))
    path.lineTo(14, 21)
    path.lineTo(22, 11)
    p.drawPath(path)


def _open_pdf(p: QPainter) -> None:
    p.drawRect(QRectF(7, 5, 14, 20))
    p.drawLine(10, 10, 18, 10)
    p.drawLine(10, 14, 18, 14)
    p.setPen(QPen(ACCENT, 2))
    p.drawLine(10, 19, 15, 19)
    p.setBrush(QBrush(ACCENT))
    p.setPen(Qt.NoPen)
    p.drawPolygon(QPolygonF([QPointF(21, 17), QPointF(29, 22),
                             QPointF(21, 27)]))


def _save(p: QPainter) -> None:
    p.drawRect(QRectF(6, 6, 20, 20))
    p.drawRect(QRectF(11, 6, 10, 7))
    p.setBrush(QBrush(FG))
    p.drawRect(QRectF(10, 17, 12, 9))


_DRAW = {
    "select": _select, "point": _point, "polyline": _polyline, "arc": _arc,
    "trace": _trace, "region": _region, "fill": _fill, "merge": _merge,
    "refpoint": _refpoint,
    "measure": _measure, "plansnap": _plansnap, "magnifier": _magnifier,
    "fit": _fit, "undo": _undo, "redo": _redo, "transfer": _transfer,
    "connect": _connect, "open_pdf": _open_pdf, "save": _save,
}
