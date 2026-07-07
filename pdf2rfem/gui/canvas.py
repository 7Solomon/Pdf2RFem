"""Zeichenflaeche: QGraphicsView, Szenen-Einheit = PDF-Punkt der aktiven Seite.

Zwei Render-Ebenen fuer Schaerfe ohne Speicherexplosion:
- Basis-Pixmap der ganzen Seite in moderater Aufloesung,
- Detail-Pixmap nur des sichtbaren Ausschnitts in hoher Aufloesung,
  nachgerendert (entprellt) nach Zoom/Pan.
Events werden mit Plan-Koordinaten an den ToolController weitergereicht.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QImage, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsItemGroup, QGraphicsPathItem,
                               QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView)

from ..core.transform import Point2
from ..infra.pdf_document import PdfDocument, RenderedPage

BASE_PIXEL_BUDGET = 20e6    # Pixel fuer die Basis-Seiten-Pixmap
DETAIL_PIXEL_BUDGET = 12e6  # Pixel fuer den Detail-Ausschnitt
DETAIL_MAX_ZOOM = 32.0      # px pro PDF-Punkt (~2300 dpi), mehr bringt nichts


def _to_pixmap(rp: RenderedPage) -> QPixmap:
    img = QImage(rp.samples, rp.width, rp.height, rp.stride,
                 QImage.Format_RGB888)
    return QPixmap.fromImage(img)  # kopiert; samples duerfen danach weg


class PdfCanvas(QGraphicsView):
    mouse_moved = Signal(object)          # Point2 (Plan-Koordinaten)
    mouse_left_view = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing
                            | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setBackgroundBrush(QBrush(QColor("#3c3f41")))

        self.controller = None            # wird vom MainWindow gesetzt
        self.pdf: Optional[PdfDocument] = None
        self.page_index: Optional[int] = None

        self._base_item = QGraphicsPixmapItem()
        self._base_item.setZValue(-10)
        self._base_item.setTransformationMode(Qt.SmoothTransformation)
        self._detail_item = QGraphicsPixmapItem()
        self._detail_item.setZValue(-9)
        self._detail_item.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self._base_item)
        self.scene().addItem(self._detail_item)

        self._obj_group = QGraphicsItemGroup()
        self._obj_group.setZValue(0)
        self._obj_group.setHandlesChildEvents(False)
        self.scene().addItem(self._obj_group)

        self._preview_item = QGraphicsPathItem()
        pen = QPen(QColor("#00b0ff"), 2)
        pen.setCosmetic(True)
        pen.setStyle(Qt.DashLine)
        self._preview_item.setPen(pen)
        self._preview_item.setZValue(5)
        self.scene().addItem(self._preview_item)

        self._snap_marker = QGraphicsRectItem(-5, -5, 10, 10)
        self._snap_marker.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self._snap_marker.setZValue(6)
        self._snap_marker.setVisible(False)
        self.scene().addItem(self._snap_marker)

        self._base_zoom = 1.0
        self._panning = False
        self._pan_start = None

        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.setInterval(200)
        self._detail_timer.timeout.connect(self._render_detail)
        self.horizontalScrollBar().valueChanged.connect(
            lambda _: self._detail_timer.start())
        self.verticalScrollBar().valueChanged.connect(
            lambda _: self._detail_timer.start())

    # --- Seite anzeigen -----------------------------------------------------
    def show_page(self, pdf: Optional[PdfDocument], page_index: Optional[int],
                  fit: bool = False) -> None:
        changed = (pdf is not self.pdf) or (page_index != self.page_index)
        self.pdf = pdf
        self.page_index = page_index
        self._detail_item.setPixmap(QPixmap())
        if pdf is None or page_index is None:
            self._base_item.setPixmap(QPixmap())
            return
        if changed or self._base_item.pixmap().isNull():
            w, h = pdf.page_size(page_index)
            self.scene().setSceneRect(0, 0, w, h)
            base_zoom = min(math.sqrt(BASE_PIXEL_BUDGET / (w * h)), 4.0)
            rp = pdf.render_page(page_index, base_zoom)
            self._base_zoom = rp.zoom
            self._base_item.setPixmap(_to_pixmap(rp))
            self._base_item.setScale(1.0 / rp.zoom)
            if fit:
                self.fit_page()
            self._detail_timer.start()

    def fit_page(self) -> None:
        if self.pdf is not None:
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)
            self._detail_timer.start()

    def current_scale(self) -> float:
        return self.transform().m11()

    def visible_plan_rect(self) -> tuple[float, float, float, float]:
        """Sichtbarer Bereich in Plan-Koordinaten (x0, y0, x1, y1)."""
        r = self.mapToScene(self.viewport().rect()).boundingRect()
        r = r.intersected(self.scene().sceneRect())
        return (r.left(), r.top(), r.right(), r.bottom())

    # --- Detail-Rendering -----------------------------------------------------
    def _render_detail(self) -> None:
        if self.pdf is None or self.page_index is None:
            return
        scale = self.current_scale() * self.devicePixelRatioF()
        if scale <= self._base_zoom * 1.2:
            self._detail_item.setPixmap(QPixmap())
            return
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        margin_x, margin_y = vis.width() * 0.3, vis.height() * 0.3
        vis = vis.adjusted(-margin_x, -margin_y, margin_x, margin_y)
        vis = vis.intersected(self.scene().sceneRect())
        if vis.isEmpty():
            return
        zoom = min(scale, DETAIL_MAX_ZOOM)
        if vis.width() * vis.height() * zoom * zoom > DETAIL_PIXEL_BUDGET:
            zoom = math.sqrt(DETAIL_PIXEL_BUDGET / (vis.width() * vis.height()))
        if zoom <= self._base_zoom:
            self._detail_item.setPixmap(QPixmap())
            return
        rp = self.pdf.render_region(
            self.page_index, zoom, (vis.left(), vis.top(),
                                    vis.right(), vis.bottom()))
        self._detail_item.setPixmap(_to_pixmap(rp))
        self._detail_item.setScale(1.0 / rp.zoom)
        self._detail_item.setPos(rp.origin_x, rp.origin_y)

    # --- Overlays fuer Werkzeuge -------------------------------------------------
    def set_preview(self, points: Optional[list[Point2]],
                    closed: bool = False) -> None:
        path = QPainterPath()
        if points:
            path.moveTo(points[0].x, points[0].y)
            for p in points[1:]:
                path.lineTo(p.x, p.y)
            if closed and len(points) > 2:
                path.closeSubpath()
        self._preview_item.setPath(path)

    # Farbcode der Fangarten: eigener Punkt gruen, Plan-Schnittpunkt magenta,
    # Plan-Linienende hellblau, Bild-Ecke orange, auf Linie violett, Ortho blau
    SNAP_COLORS = {"point": "#00e676", "isect": "#ff4081", "vend": "#40c4ff",
                   "corner": "#ffab40", "online": "#b388ff", "ortho": "#00b0ff"}

    def set_snap_marker(self, pos: Optional[Point2], kind: str = "free") -> None:
        if pos is None or kind == "free":
            self._snap_marker.setVisible(False)
            return
        color = QColor(self.SNAP_COLORS.get(kind, "#00b0ff"))
        pen = QPen(color, 2)
        self._snap_marker.setPen(pen)
        self._snap_marker.setBrush(Qt.NoBrush)
        self._snap_marker.setPos(pos.x, pos.y)
        self._snap_marker.setVisible(True)

    # --- Objekt-Layer ------------------------------------------------------------
    def rebuild_objects(self, project, selection: set[str]) -> None:
        """Zeichnet alle Objekte der auf dieser Seite sichtbaren Ansichten neu."""
        for item in list(self._obj_group.childItems()):
            self._obj_group.removeFromGroup(item)
            self.scene().removeItem(item)
        if project is None or self.page_index is None:
            return
        for view in project.ordered_views():
            if not view.visible or view.page_index != self.page_index:
                continue
            color = QColor(view.color)
            model = project.model
            for line in model.lines_in_view(view.id):
                pts = [model.points[pid].pos for pid in line.point_ids]
                if len(pts) < 2:
                    continue
                path = QPainterPath()
                path.moveTo(pts[0].x, pts[0].y)
                for p in pts[1:]:
                    path.lineTo(p.x, p.y)
                if line.closed and len(pts) > 2:
                    path.closeSubpath()
                item = QGraphicsPathItem(path)
                selected = line.id in selection
                pen = QPen(QColor("#ffd600") if selected else color,
                           4 if selected else 2)
                pen.setCosmetic(True)
                item.setPen(pen)
                self._obj_group.addToGroup(item)
            from ..core.arcs import sample_arc
            for arc in model.arcs_in_view(view.id):
                s, e = (model.points[pid].pos for pid in arc.point_ids)
                pts = sample_arc(s, arc.control, e)
                path = QPainterPath()
                path.moveTo(pts[0].x, pts[0].y)
                for p in pts[1:]:
                    path.lineTo(p.x, p.y)
                item = QGraphicsPathItem(path)
                selected = arc.id in selection
                pen = QPen(QColor("#ffd600") if selected else color,
                           4 if selected else 2)
                pen.setCosmetic(True)
                item.setPen(pen)
                self._obj_group.addToGroup(item)
            for p in model.points_in_view(view.id):
                selected = p.id in selection
                r = 5 if selected else 3.5
                marker = QGraphicsEllipseItem(-r, -r, 2 * r, 2 * r)
                marker.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                marker.setPos(p.pos.x, p.pos.y)
                pen = QPen(QColor("#ffd600") if selected else color, 1.5)
                marker.setPen(pen)
                marker.setBrush(QBrush(QColor("#ffd600") if selected else color))
                self._obj_group.addToGroup(marker)
            if view.is_ready:
                self._obj_group.addToGroup(self._ref_marker(view, color))

    def _ref_marker(self, view, color: QColor) -> QGraphicsItem:
        """Referenzpunkt: Kreis mit Fadenkreuz, konstante Bildschirmgroesse."""
        path = QPainterPath()
        path.addEllipse(-8, -8, 16, 16)
        path.moveTo(-12, 0); path.lineTo(12, 0)
        path.moveTo(0, -12); path.lineTo(0, 12)
        item = QGraphicsPathItem(path)
        item.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        item.setPos(view.ref_pdf.x, view.ref_pdf.y)
        pen = QPen(color, 2)
        item.setPen(pen)
        t = view.ref_target
        item.setToolTip(f"Referenzpunkt {view.name}\n"
                        f"X={t[0]:.3f}  Y={t[1]:.3f}  Z={t[2]:.3f} m")
        return item

    # --- Maus / Tastatur --------------------------------------------------------
    def _scene_point(self, event) -> Point2:
        sp = self.mapToScene(event.position().toPoint())
        return Point2(sp.x(), sp.y())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.controller and self.pdf:
            self.controller.on_press(self._scene_point(event),
                                     event.modifiers())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._pan_start is not None:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        if self.pdf is not None:
            p = self._scene_point(event)
            self.mouse_moved.emit(p)
            if self.controller:
                self.controller.on_move(p, event.modifiers())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.controller and self.pdf:
            self.controller.on_double(self._scene_point(event),
                                      event.modifiers())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        if self.pdf is None:
            return
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        new_scale = self.current_scale() * factor
        if 0.02 <= new_scale <= 200:
            self.scale(factor, factor)
            self._detail_timer.start()
        event.accept()

    def keyPressEvent(self, event) -> None:
        if self.controller and self.controller.on_key(event.key()):
            event.accept()
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event) -> None:
        self.set_snap_marker(None)
        self.mouse_left_view.emit()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._detail_timer.start()
