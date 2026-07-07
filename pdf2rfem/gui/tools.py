"""Werkzeuge als kleine State-Machine: genau ein aktives Tool.

Der ToolController nimmt rohe Canvas-Events entgegen, wendet Snapping an
(Punkt-Snap immer, Ortho mit Shift) und reicht das Ergebnis an das aktive
Tool weiter. Tools erzeugen ausschliesslich Commands - nie direkte
Modell-Mutationen -, damit alles undo-faehig bleibt.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt

from ..core.arcs import arc_midpoint, sample_arc
from ..core.commands import (AddArcCmd, AddPointCmd, AddPolylineCmd,
                             DeleteObjectsCmd, SetReferenceCmd)
from ..core.geometry import GeoArc, GeoPoint, GeoPolyline, new_id
from ..core.snap import SnapEngine, SnapResult
from ..core.transform import Point2

SNAP_RADIUS_PX = 12.0
PICK_RADIUS_PX = 8.0


class Tool:
    name = ""
    status_hint = ""
    needs_ready_view = True   # Referenzpunkt muss gesetzt sein
    wants_snap = True         # False: Tool bekommt rohe Cursorposition

    def __init__(self, ctrl: "ToolController") -> None:
        self.ctrl = ctrl

    def anchor(self) -> Optional[Point2]:
        """Bezugspunkt fuer Ortho-Snapping (letzter Vertex o.ae.)."""
        return None

    def on_press(self, snap: SnapResult, mods) -> None: ...
    def on_move(self, snap: SnapResult, mods) -> None: ...
    def on_double(self, snap: SnapResult, mods) -> None: ...

    def on_key(self, key) -> bool:
        return False

    def activate(self) -> None: ...

    def deactivate(self) -> None:
        self.ctrl.canvas.set_preview(None)
        self.ctrl.canvas.set_snap_marker(None)


class SelectTool(Tool):
    name = "select"
    status_hint = "Klick: Objekt waehlen (Strg: hinzu) | Entf: loeschen | Esc: Auswahl aufheben"
    needs_ready_view = False

    def on_press(self, snap: SnapResult, mods) -> None:
        win = self.ctrl.window
        view = win.project.active_view if win.project else None
        if view is None:
            return
        radius = PICK_RADIUS_PX / max(self.ctrl.canvas.current_scale(), 1e-9)
        model = win.project.model
        hit_id = None
        point = model.find_point_near(view.id, snap.pos, radius)
        if point is not None:
            hit_id = point.id
        else:
            line = model.find_line_near(view.id, snap.pos, radius)
            if line is not None:
                hit_id = line.id
            else:
                arc = model.find_arc_near(view.id, snap.pos, radius)
                if arc is not None:
                    hit_id = arc.id
        if mods & Qt.ControlModifier:
            if hit_id:
                win.selection.symmetric_difference_update({hit_id})
        else:
            win.selection = {hit_id} if hit_id else set()
        win.refresh_all()

    def on_key(self, key) -> bool:
        win = self.ctrl.window
        if key in (Qt.Key_Delete, Qt.Key_Backspace) and win.selection:
            model = win.project.model
            point_ids = {i for i in win.selection if i in model.points}
            line_ids = {i for i in win.selection if i in model.lines}
            arc_ids = {i for i in win.selection if i in model.arcs}
            if point_ids or line_ids or arc_ids:
                win.stack.push(DeleteObjectsCmd(point_ids, line_ids, model,
                                                arc_ids))
                win.selection = set()
            return True
        if key == Qt.Key_Escape:
            win.selection = set()
            win.refresh_all()
            return True
        return False


class PointTool(Tool):
    name = "point"
    status_hint = "Klick: Punkt setzen | Snap auf vorhandene Punkte automatisch"

    def on_press(self, snap: SnapResult, mods) -> None:
        view = self.ctrl.require_ready_view()
        if view is None:
            return
        if snap.kind == "point":
            self.ctrl.window.show_status("Hier liegt bereits ein Punkt.")
            return
        self.ctrl.window.stack.push(
            AddPointCmd(GeoPoint(new_id(), view.id, snap.pos)))


class PolylineTool(Tool):
    name = "polyline"
    status_hint = ("Klick: Vertex | Shift: Ortho | Enter/Doppelklick: fertig | "
                   "C: schliessen | Rueck: Vertex zurueck | Esc: abbrechen")

    def __init__(self, ctrl: "ToolController") -> None:
        super().__init__(ctrl)
        # Vertex = (vorhandene Punkt-ID oder None, Position)
        self.vertices: list[tuple[Optional[str], Point2]] = []
        self._cursor: Optional[Point2] = None

    def anchor(self) -> Optional[Point2]:
        return self.vertices[-1][1] if self.vertices else None

    def on_press(self, snap: SnapResult, mods) -> None:
        view = self.ctrl.require_ready_view()
        if view is None:
            return
        pid = snap.target_id if snap.kind == "point" else None
        if self.vertices and self.vertices[-1][1].dist(snap.pos) < 1e-9:
            return  # Doppelklick erzeugt keinen doppelten Vertex
        self.vertices.append((pid, snap.pos))
        self._update_preview()

    def on_move(self, snap: SnapResult, mods) -> None:
        self._cursor = snap.pos
        self._update_preview()

    def on_double(self, snap: SnapResult, mods) -> None:
        self.commit(closed=False)

    def on_key(self, key) -> bool:
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.commit(closed=False)
            return True
        if key == Qt.Key_C:
            self.commit(closed=True)
            return True
        if key == Qt.Key_Backspace:
            if self.vertices:
                self.vertices.pop()
                self._update_preview()
            return True
        if key == Qt.Key_Escape:
            self.reset()
            return True
        return False

    def commit(self, closed: bool) -> None:
        win = self.ctrl.window
        view = win.project.active_view if win.project else None
        if view is None:
            return
        if len(self.vertices) < 2 or (closed and len(self.vertices) < 3):
            win.show_status("Zu wenige Punkte fuer eine Polylinie.")
            return
        new_points: list[GeoPoint] = []
        point_ids: list[str] = []
        for pid, pos in self.vertices:
            if pid is None:
                gp = GeoPoint(new_id(), view.id, pos)
                new_points.append(gp)
                point_ids.append(gp.id)
            else:
                point_ids.append(pid)
        line = GeoPolyline(new_id(), view.id, point_ids, closed=closed)
        win.stack.push(AddPolylineCmd(new_points, line))
        self.reset()

    def reset(self) -> None:
        self.vertices = []
        self._cursor = None
        self.ctrl.canvas.set_preview(None)

    def deactivate(self) -> None:
        self.reset()
        super().deactivate()

    def _update_preview(self) -> None:
        pts = [pos for _, pos in self.vertices]
        if self._cursor is not None and pts:
            pts = pts + [self._cursor]
        self.ctrl.canvas.set_preview(pts if len(pts) >= 2 else None)


class RefPointTool(Tool):
    name = "refpoint"
    status_hint = ("Klick auf den Referenzpunkt im Plan - danach RFEM-"
                   "Zielkoordinaten eingeben. Snap auf vorhandene Punkte aktiv.")
    needs_ready_view = False

    def on_press(self, snap: SnapResult, mods) -> None:
        win = self.ctrl.window
        view = win.project.active_view if win.project else None
        if view is None:
            win.show_status("Zuerst eine Ansicht anlegen.")
            return
        from .dialogs import RefTargetDialog
        dlg = RefTargetDialog(view, win)
        if dlg.exec():
            win.stack.push(SetReferenceCmd(view, snap.pos, dlg.target()))
            win.show_status(
                f"Referenzpunkt fuer '{view.name}' gesetzt - Zeichnen freigegeben.")
            win.set_tool("select")


class MeasureTool(Tool):
    name = "measure"
    status_hint = ("Massstab pruefen: zwei Punkte einer bekannten Strecke "
                   "anklicken. | Esc: abbrechen")
    needs_ready_view = False

    def __init__(self, ctrl: "ToolController") -> None:
        super().__init__(ctrl)
        self.first: Optional[Point2] = None

    def anchor(self) -> Optional[Point2]:
        return self.first

    def on_press(self, snap: SnapResult, mods) -> None:
        win = self.ctrl.window
        view = win.project.active_view if win.project else None
        if view is None:
            return
        if self.first is None:
            self.first = snap.pos
            return
        from .dialogs import MeasureDialog
        MeasureDialog(self.first.dist(snap.pos),
                      view.scale_denominator, win).exec()
        self.first = None
        self.ctrl.canvas.set_preview(None)

    def on_move(self, snap: SnapResult, mods) -> None:
        if self.first is not None:
            self.ctrl.canvas.set_preview([self.first, snap.pos])

    def on_key(self, key) -> bool:
        if key == Qt.Key_Escape:
            self.first = None
            self.ctrl.canvas.set_preview(None)
            return True
        return False

    def deactivate(self) -> None:
        self.first = None
        super().deactivate()


class LineTraceTool(Tool):
    """Vorhandene Plan-Linie ODER Plan-Kreisbogen per Klick als Geometrie
    uebernehmen (nur Vektor-PDFs; bei Scans gibt es keine exakten Pfade)."""
    name = "trace"
    status_hint = ("Auf eine Linie oder einen Bogen im Plan klicken, um sie "
                   "als Geometrie zu uebernehmen. | Esc: Vorschau weg")
    wants_snap = False

    def _pick(self, pos: Point2):
        """Naeheres von Segment/Bogen unter dem Cursor: (segment, arc)."""
        provider = self.ctrl.plan_provider()
        if provider is None:
            return None, None
        radius = SNAP_RADIUS_PX / max(self.ctrl.canvas.current_scale(), 1e-9)
        seg = provider.nearest_segment(pos, radius)
        arc = provider.nearest_arc(pos, radius)
        if seg is not None and arc is not None:
            if seg.closest_point(pos).dist(pos) <= arc.distance(pos):
                return seg, None
            return None, arc
        return seg, arc

    def on_move(self, snap: SnapResult, mods) -> None:
        seg, arc = self._pick(snap.pos)
        if seg is not None:
            self.ctrl.canvas.set_preview([seg.p1, seg.p2])
        elif arc is not None:
            self.ctrl.canvas.set_preview(
                [arc.point_at(i / 48) for i in range(49)])
        else:
            self.ctrl.canvas.set_preview(None)

    def on_press(self, snap: SnapResult, mods) -> None:
        view = self.ctrl.require_ready_view()
        if view is None:
            return
        seg, arc = self._pick(snap.pos)
        if seg is None and arc is None:
            provider = self.ctrl.plan_provider()
            if provider is not None and not provider.is_vector_plan:
                self.ctrl.window.show_status(
                    "Dieses PDF enthaelt keine Vektorlinien (gescannter "
                    "Plan?) - Werkzeug hier nicht nutzbar.")
            return
        win = self.ctrl.window
        tf = view.transform()
        if seg is not None:
            ids, new_points = self._point_ids(view, [seg.p1, seg.p2])
            if ids[0] == ids[1]:
                return
            win.stack.push(AddPolylineCmd(
                new_points, GeoPolyline(new_id(), view.id, ids)))
            win.show_status(f"Linie uebernommen "
                            f"({tf.pdf_dist_to_meters(seg.length()):.3f} m).")
            return
        # Bogen; Vollkreis in zwei Halbboegen aufteilen (RFEM-Arc braucht
        # zwei verschiedene Endknoten)
        r_m = tf.pdf_dist_to_meters(arc.r)
        if arc.is_full_circle:
            for t0, t1 in ((0.0, 0.5), (0.5, 1.0)):
                self._commit_arc(view, arc.point_at(t0), arc.point_at(t1),
                                 arc.point_at((t0 + t1) / 2))
            win.show_status(f"Vollkreis als 2 Halbboegen uebernommen "
                            f"(R = {r_m:.3f} m).")
        else:
            self._commit_arc(view, arc.point_at(0.0), arc.point_at(1.0),
                             arc.point_at(0.5))
            win.show_status(f"Bogen uebernommen (R = {r_m:.3f} m).")

    def _point_ids(self, view, positions: list[Point2]):
        model = self.ctrl.window.project.model
        new_points: list[GeoPoint] = []
        ids: list[str] = []
        for pos in positions:
            existing = model.find_point_near(view.id, pos, 0.5)
            if existing is not None:
                ids.append(existing.id)
            else:
                gp = GeoPoint(new_id(), view.id, pos)
                new_points.append(gp)
                ids.append(gp.id)
        return ids, new_points

    def _commit_arc(self, view, start: Point2, end: Point2,
                    control: Point2) -> None:
        ids, new_points = self._point_ids(view, [start, end])
        if ids[0] == ids[1]:
            return
        self.ctrl.window.stack.push(AddArcCmd(
            new_points, GeoArc(new_id(), view.id, ids, control)))

    def on_key(self, key) -> bool:
        if key == Qt.Key_Escape:
            self.ctrl.canvas.set_preview(None)
            return True
        return False


class ArcTool(Tool):
    """Bogen mit 3 Klicks zeichnen: Start, Ende, Punkt auf dem Bogen."""
    name = "arc"
    status_hint = ("Bogen: 1. Startpunkt, 2. Endpunkt, 3. Punkt auf dem "
                   "Bogen klicken | Ruecktaste: zurueck | Esc: abbrechen")

    def __init__(self, ctrl: "ToolController") -> None:
        super().__init__(ctrl)
        # bis zu 2 Eintraege: (vorhandene Punkt-ID oder None, Position)
        self.picked: list[tuple[Optional[str], Point2]] = []

    def on_press(self, snap: SnapResult, mods) -> None:
        view = self.ctrl.require_ready_view()
        if view is None:
            return
        if len(self.picked) < 2:
            pid = snap.target_id if snap.kind == "point" else None
            if self.picked and self.picked[-1][1].dist(snap.pos) < 1e-9:
                return
            self.picked.append((pid, snap.pos))
            if len(self.picked) == 1:
                self.ctrl.window.show_status("Endpunkt klicken.")
            else:
                self.ctrl.window.show_status("Punkt auf dem Bogen klicken.")
            return
        self._commit(view, snap.pos)

    def _commit(self, view, through: Point2) -> None:
        win = self.ctrl.window
        (pid1, p1), (pid2, p2) = self.picked
        control = arc_midpoint(p1, through, p2)
        model = win.project.model
        new_points: list[GeoPoint] = []
        ids: list[str] = []
        for pid, pos in self.picked:
            if pid is not None:
                ids.append(pid)
            else:
                gp = GeoPoint(new_id(), view.id, pos)
                new_points.append(gp)
                ids.append(gp.id)
        if ids[0] == ids[1]:
            win.show_status("Start- und Endpunkt muessen verschieden sein.")
            return
        win.stack.push(AddArcCmd(
            new_points, GeoArc(new_id(), view.id, ids, control)))
        self.reset()

    def on_move(self, snap: SnapResult, mods) -> None:
        if len(self.picked) == 1:
            self.ctrl.canvas.set_preview([self.picked[0][1], snap.pos])
        elif len(self.picked) == 2:
            self.ctrl.canvas.set_preview(
                sample_arc(self.picked[0][1], snap.pos, self.picked[1][1]))

    def on_key(self, key) -> bool:
        if key == Qt.Key_Backspace:
            if self.picked:
                self.picked.pop()
                self.ctrl.canvas.set_preview(None)
            return True
        if key == Qt.Key_Escape:
            self.reset()
            return True
        return False

    def reset(self) -> None:
        self.picked = []
        self.ctrl.canvas.set_preview(None)

    def deactivate(self) -> None:
        self.reset()
        super().deactivate()


class RegionTool(Tool):
    """Gefuellte Flaeche (z.B. Grauton) per Klick als geschlossenes Polygon
    aufnehmen: Flood-Fill nach Farbtoleranz, Kontur vereinfachen, Ecken auf
    exakte Vektorpunkte ziehen."""
    name = "region"
    status_hint = ("In eine gefuellte Flaeche klicken | Enter: uebernehmen | "
                   "+/-: Farbtoleranz | Esc: abbrechen")
    wants_snap = False

    def __init__(self, ctrl: "ToolController") -> None:
        super().__init__(ctrl)
        self.seed: Optional[Point2] = None
        self.polygon: Optional[list[Point2]] = None
        self.tolerance = 12

    def on_press(self, snap: SnapResult, mods) -> None:
        if self.ctrl.require_ready_view() is None:
            return
        self.seed = snap.pos
        self._detect()

    def _detect(self) -> None:
        from ..infra.edge_detect import detect_region
        win = self.ctrl.window
        result = detect_region(
            win.pdf, self.ctrl.canvas.page_index, self.seed,
            self.ctrl.canvas.visible_plan_rect(), self.tolerance)
        if result is None:
            self.polygon = None
            self.ctrl.canvas.set_preview(None)
            win.show_status(f"Keine Flaeche erkannt (Toleranz {self.tolerance}).")
            return
        self.polygon = result.polygon
        self.ctrl.canvas.set_preview(result.polygon, closed=True)
        msg = (f"Flaeche mit {len(result.polygon)} Ecken erkannt "
               f"(Toleranz {self.tolerance}). Enter = uebernehmen.")
        if result.touched_border:
            msg += (" ACHTUNG: Flaeche laeuft aus dem sichtbaren Bereich - "
                    "herauszoomen und neu klicken!")
        win.show_status(msg)

    def on_key(self, key) -> bool:
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.commit()
            return True
        if key in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus, Qt.Key_Underscore):
            delta = 4 if key in (Qt.Key_Plus, Qt.Key_Equal) else -4
            self.tolerance = max(2, min(80, self.tolerance + delta))
            if self.seed is not None:
                self._detect()
            return True
        if key == Qt.Key_Escape:
            self.reset()
            return True
        return False

    def commit(self) -> None:
        win = self.ctrl.window
        view = win.project.active_view if win.project else None
        if view is None or not self.polygon:
            return
        provider = self.ctrl.plan_provider()
        model = win.project.model
        new_points: list[GeoPoint] = []
        ids: list[str] = []
        last_pos: Optional[Point2] = None
        for v in self.polygon:
            # Raster-Ecke auf exakten Vektorpunkt ziehen, falls vorhanden
            pos = provider.snap_vertex(v, 3.0) if provider else v
            if last_pos is not None and pos.dist(last_pos) < 0.5:
                continue
            existing = model.find_point_near(view.id, pos, 0.5)
            if existing is not None:
                if existing.id in ids:
                    continue
                ids.append(existing.id)
            else:
                gp = GeoPoint(new_id(), view.id, pos)
                new_points.append(gp)
                ids.append(gp.id)
            last_pos = pos
        if len(ids) < 3:
            win.show_status("Zu wenige eindeutige Ecken fuer ein Polygon.")
            return
        win.stack.push(AddPolylineCmd(
            new_points, GeoPolyline(new_id(), view.id, ids, closed=True)))
        win.show_status(f"Polygon mit {len(ids)} Ecken uebernommen.")
        self.reset()

    def reset(self) -> None:
        self.seed = None
        self.polygon = None
        self.ctrl.canvas.set_preview(None)

    def deactivate(self) -> None:
        self.reset()
        super().deactivate()


class ToolController:
    def __init__(self, window, canvas) -> None:
        self.window = window
        self.canvas = canvas
        self.snap_engine = SnapEngine()
        self.tools: dict[str, Tool] = {
            t.name: t for t in (SelectTool(self), PointTool(self),
                                PolylineTool(self), RefPointTool(self),
                                MeasureTool(self), LineTraceTool(self),
                                RegionTool(self))
        }
        self.active: Tool = self.tools["select"]

    def set_tool(self, name: str) -> None:
        if self.tools[name] is self.active:
            return
        self.active.deactivate()
        self.active = self.tools[name]
        self.active.activate()
        self.window.show_status(self.active.status_hint)

    def require_ready_view(self):
        """Zeichnen erst, wenn die aktive Ansicht einen Referenzpunkt hat."""
        win = self.window
        view = win.project.active_view if win.project else None
        if view is None:
            win.show_status("Keine aktive Ansicht - zuerst Ansicht anlegen.")
            return None
        if not view.is_ready:
            win.show_status(
                f"Ansicht '{view.name}' hat noch keinen Referenzpunkt - "
                "mit Werkzeug 'Referenzpunkt' (R) setzen.")
            return None
        return view

    def plan_provider(self):
        """Plan-Snap-Provider (Vektor/Raster) fuer die angezeigte Seite."""
        return self.window.get_plan_provider(self.canvas.page_index)

    # --- Event-Eingang von der Canvas -------------------------------------
    def _snapped(self, raw: Point2, mods) -> SnapResult:
        win = self.window
        view = win.project.active_view if win.project else None
        if view is None:
            return SnapResult(raw, "free")
        if not self.active.wants_snap:
            self.canvas.set_snap_marker(None)
            return SnapResult(raw, "free")
        radius = SNAP_RADIUS_PX / max(self.canvas.current_scale(), 1e-9)
        self.snap_engine.provider = self.plan_provider()
        snap = self.snap_engine.snap(
            win.project.model, view.id, raw, radius,
            anchor=self.active.anchor(),
            ortho=bool(mods & Qt.ShiftModifier))
        self.canvas.set_snap_marker(snap.pos, snap.kind)
        return snap

    def on_press(self, raw: Point2, mods) -> None:
        self.active.on_press(self._snapped(raw, mods), mods)

    def on_move(self, raw: Point2, mods) -> None:
        self.active.on_move(self._snapped(raw, mods), mods)

    def on_double(self, raw: Point2, mods) -> None:
        self.active.on_double(self._snapped(raw, mods), mods)

    def on_key(self, key) -> bool:
        return self.active.on_key(key)
