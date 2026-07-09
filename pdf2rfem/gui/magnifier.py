"""Lupe: Overlay oben rechts in der Canvas, zeigt den Bereich um den Cursor
vergroessert und scharf nachgerendert - inklusive der eigenen Geometrie
und einem Fadenkreuz. Weicht in die andere Ecke aus, wenn der Cursor ihr
zu nahe kommt.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..core.arcs import sample_arc
from ..core.transform import Point2

SIZE = 240            # Kantenlaenge des Lupenfensters in px
FACTOR = 4.0          # Vergroesserung relativ zum aktuellen Canvas-Zoom
MAX_RENDER_ZOOM = 32.0


class Magnifier(QWidget):
    def __init__(self, canvas, window) -> None:
        super().__init__(canvas.viewport())
        self.canvas = canvas
        self.window = window
        self.setFixedSize(SIZE, SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

        self._cursor: Optional[Point2] = None
        self._pixmap: Optional[QPixmap] = None
        self._pix_zoom = 1.0
        self._pix_center: Optional[Point2] = None

        # Nachrendern entprellen, damit Mausbewegung fluessig bleibt
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._render)

    # --- Ansteuerung ----------------------------------------------------------
    def track(self, pos: Point2) -> None:
        if not self.isVisible():
            return
        self._cursor = pos
        self._reposition()
        if (self._pix_center is None
                or self._pix_center.dist(pos) > 4.0 / self._zoom()):
            self._timer.start()
        self.update()

    def set_active(self, active: bool) -> None:
        self.setVisible(active)
        if active:
            self._reposition()
            self._timer.start()

    def _zoom(self) -> float:
        z = self.canvas.current_scale() * FACTOR
        return max(1.0, min(z, MAX_RENDER_ZOOM))

    def _reposition(self) -> None:
        vp = self.canvas.viewport()
        margin = 10
        x_right = vp.width() - SIZE - margin
        pos = QPointF(x_right, margin)
        cursor = vp.mapFromGlobal(self.canvas.cursor().pos())
        rect = QRectF(x_right - 20, 0, SIZE + 40, SIZE + 40)
        if rect.contains(QPointF(cursor)):
            pos = QPointF(margin, margin)   # Cursor zu nah: nach links wechseln
        self.move(int(pos.x()), int(pos.y()))

    # --- Rendern ---------------------------------------------------------------
    def _render(self) -> None:
        if (self._cursor is None or self.canvas.pdf is None
                or self.canvas.page_index is None):
            return
        zoom = self._zoom()
        half = SIZE / 2.0 / zoom
        c = self._cursor
        rp = self.canvas.pdf.render_region(
            self.canvas.page_index, zoom,
            (c.x - half, c.y - half, c.x + half, c.y + half))
        img = QImage(rp.samples, rp.width, rp.height, rp.stride,
                     QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(img)
        self._pix_zoom = rp.zoom
        self._pix_origin = (rp.origin_x, rp.origin_y)
        self._pix_center = c
        self.update()

    def _to_widget(self, p: Point2) -> QPointF:
        """Plan-Koordinate -> Pixel im Lupenfenster (Zentrum = Cursor)."""
        z = self._pix_zoom
        cx, cy = self._pix_center.x, self._pix_center.y
        return QPointF(SIZE / 2 + (p.x - cx) * z, SIZE / 2 + (p.y - cy) * z)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if self._pixmap is not None and self._pix_center is not None:
            ox, oy = self._pix_origin
            top_left = self._to_widget(Point2(ox, oy))
            painter.drawPixmap(top_left, self._pixmap)
            self._draw_geometry(painter)
        # Fadenkreuz + Rahmen
        pen = QPen(QColor("#d62728"), 1)
        painter.setPen(pen)
        m = SIZE // 2
        painter.drawLine(m, m - 14, m, m + 14)
        painter.drawLine(m - 14, m, m + 14, m)
        painter.setPen(QPen(QColor("#555555"), 2))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))

    def _draw_geometry(self, painter: QPainter) -> None:
        project = self.window.project
        if project is None:
            return
        painter.setRenderHint(QPainter.Antialiasing)
        for view in project.ordered_views():
            if not view.visible or view.page_index != self.canvas.page_index:
                continue
            color = QColor(view.color)
            pen = QPen(color, 2)
            painter.setPen(pen)
            model = project.model
            for line in model.lines_in_view(view.id):
                pts = [model.points[pid].pos for pid in line.point_ids]
                if line.closed and len(pts) > 2:
                    pts = pts + [pts[0]]
                path = QPainterPath(self._to_widget(pts[0]))
                for p in pts[1:]:
                    path.lineTo(self._to_widget(p))
                painter.drawPath(path)
            for arc in model.arcs_in_view(view.id):
                s, e = (model.points[pid].pos for pid in arc.point_ids)
                pts = sample_arc(s, arc.control, e)
                path = QPainterPath(self._to_widget(pts[0]))
                for p in pts[1:]:
                    path.lineTo(self._to_widget(p))
                painter.drawPath(path)
            painter.setBrush(color)
            for p in model.points_in_view(view.id):
                painter.drawEllipse(self._to_widget(p.pos), 3, 3)
            painter.setBrush(Qt.NoBrush)
