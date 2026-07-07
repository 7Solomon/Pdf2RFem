"""Undo/Redo ueber das Command-Pattern.

Jede nutzerwirksame Aenderung (auch Referenzpunkt-Aenderungen!) laeuft als
Command ueber den Stack, damit der teuerste Fehler - ein versehentlich
verschobener Referenzpunkt - immer rueckgaengig gemacht werden kann.
Commands operieren auf dem Project (Geometrie + Views).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Optional

from .geometry import GeoArc, GeoPoint, GeoPolyline
from .transform import Point2

if TYPE_CHECKING:
    from .project import Project, View


class Command(ABC):
    text: str = ""

    @abstractmethod
    def do(self, project: "Project") -> None: ...

    @abstractmethod
    def undo(self, project: "Project") -> None: ...


class CommandStack:
    def __init__(self, project: "Project") -> None:
        self.project = project
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def _notify(self) -> None:
        for fn in self._listeners:
            fn()
        self.project.model.notify()

    def push(self, cmd: Command) -> None:
        cmd.do(self.project)
        self._undo.append(cmd)
        self._redo.clear()
        self.project.dirty = True
        self._notify()

    def undo(self) -> None:
        if self._undo:
            cmd = self._undo.pop()
            cmd.undo(self.project)
            self._redo.append(cmd)
            self.project.dirty = True
            self._notify()

    def redo(self) -> None:
        if self._redo:
            cmd = self._redo.pop()
            cmd.do(self.project)
            self._undo.append(cmd)
            self.project.dirty = True
            self._notify()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_text(self) -> str:
        return self._undo[-1].text if self._undo else ""

    def redo_text(self) -> str:
        return self._redo[-1].text if self._redo else ""


class AddPointCmd(Command):
    def __init__(self, point: GeoPoint) -> None:
        self.point = point
        self.text = "Punkt hinzufuegen"

    def do(self, project: "Project") -> None:
        project.model.add_point(self.point)

    def undo(self, project: "Project") -> None:
        project.model.remove_point(self.point.id)


class AddPolylineCmd(Command):
    """Fuegt eine Polylinie samt der dafuer NEU erzeugten Punkte ein.

    Beim Zeichnen gesnappte, bereits existierende Punkte werden nur
    referenziert (gemeinsamer RFEM-Knoten), nicht dupliziert.
    """

    def __init__(self, new_points: list[GeoPoint], line: GeoPolyline) -> None:
        self.new_points = new_points
        self.line = line
        self.text = "Polylinie zeichnen"

    def do(self, project: "Project") -> None:
        for p in self.new_points:
            project.model.add_point(p)
        project.model.add_line(self.line)

    def undo(self, project: "Project") -> None:
        project.model.remove_line(self.line.id)
        for p in self.new_points:
            project.model.remove_point(p.id)


class AddArcCmd(Command):
    """Fuegt einen Kreisbogen samt der dafuer NEU erzeugten Punkte ein."""

    def __init__(self, new_points: list[GeoPoint], arc: GeoArc) -> None:
        self.new_points = new_points
        self.arc = arc
        self.text = "Bogen zeichnen"

    def do(self, project: "Project") -> None:
        for p in self.new_points:
            project.model.add_point(p)
        project.model.add_arc(self.arc)

    def undo(self, project: "Project") -> None:
        project.model.remove_arc(self.arc.id)
        for p in self.new_points:
            project.model.remove_point(p.id)


class DeleteObjectsCmd(Command):
    """Loescht Punkte, Linien und Boegen inkl. Abhaengigkeits-Abschluss.

    Wird ein Punkt geloescht, der von Linien/Boegen verwendet wird, werden
    diese mit geloescht (deren dadurch verwaiste Punkte NICHT -
    freie Punkte bleiben bewusst bestehen, das ist vorhersehbarer).
    """

    def __init__(self, point_ids: set[str], line_ids: set[str],
                 model, arc_ids: set[str] = frozenset()) -> None:
        line_ids = set(line_ids)
        arc_ids = set(arc_ids)
        for pid in point_ids:
            line_ids.update(l.id for l in model.lines_using_point(pid))
            arc_ids.update(a.id for a in model.arcs_using_point(pid))
        self.removed_lines: list[GeoPolyline] = [model.lines[lid] for lid in line_ids]
        self.removed_arcs: list[GeoArc] = [model.arcs[aid] for aid in arc_ids]
        self.removed_points: list[GeoPoint] = [model.points[pid] for pid in point_ids]
        self.text = "Loeschen"

    def do(self, project: "Project") -> None:
        for line in self.removed_lines:
            project.model.remove_line(line.id)
        for arc in self.removed_arcs:
            project.model.remove_arc(arc.id)
        for p in self.removed_points:
            project.model.remove_point(p.id)

    def undo(self, project: "Project") -> None:
        for p in self.removed_points:
            project.model.add_point(p)
        for line in self.removed_lines:
            project.model.add_line(line)
        for arc in self.removed_arcs:
            project.model.add_arc(arc)


class MovePointCmd(Command):
    def __init__(self, point_id: str, old_pos: Point2, new_pos: Point2) -> None:
        self.point_id = point_id
        self.old_pos = old_pos
        self.new_pos = new_pos
        self.text = "Punkt verschieben"

    def do(self, project: "Project") -> None:
        project.model.points[self.point_id].pos = self.new_pos

    def undo(self, project: "Project") -> None:
        project.model.points[self.point_id].pos = self.old_pos


class SetReferenceCmd(Command):
    """Setzt oder verschiebt den Referenzpunkt einer Ansicht (undo-faehig)."""

    def __init__(self, view: "View", new_pdf: Optional[Point2],
                 new_target: Optional[tuple[float, float, float]]) -> None:
        self.view_id = view.id
        self.old = (view.ref_pdf, view.ref_target)
        self.new = (new_pdf, new_target)
        self.text = "Referenzpunkt setzen"

    def do(self, project: "Project") -> None:
        view = project.views[self.view_id]
        view.ref_pdf, view.ref_target = self.new

    def undo(self, project: "Project") -> None:
        view = project.views[self.view_id]
        view.ref_pdf, view.ref_target = self.old
