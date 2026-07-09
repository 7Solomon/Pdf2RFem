"""RFEM-6-Anbindung ueber dlubal.api (gRPC).

Kapselt die API vollstaendig; der Rest des Programms kennt RFEM nicht.
Die Uebertragung ist eine explizite Nutzeraktion mit Vorschau (build_plan)
und idempotent: über project.rfem_node_map/rfem_line_map werden bereits
uebertragene Objekte aktualisiert statt dupliziert. Vor der Nummernvergabe
werden die in RFEM tatsaechlich vorhandenen Objektnummern abgefragt, damit
manuell in RFEM angelegte Objekte nicht ueberschrieben werden.

Der API-Key kommt aus %LOCALAPPDATA%/Dlubal/api/config.ini (Standard von
dlubal.api) oder aus der Umgebungsvariable PDF2RFEM_API_KEY.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from ..core.project import Project


class RfemError(RuntimeError):
    """Lesbare Fehlermeldung fuer das GUI-Log."""


@dataclass
class NodeTransfer:
    point_id: str
    coords: tuple[float, float, float]
    no: Optional[int] = None          # None = neu, wird bei Uebertragung vergeben


@dataclass
class LineTransfer:
    line_id: str
    point_ids: list[str]
    closed: bool
    no: Optional[int] = None
    # gesetzt bei Kreisboegen: RFEM-Koordinate des Kontrollpunkts auf dem Bogen
    arc_control: Optional[tuple[float, float, float]] = None


@dataclass
class OpeningTransfer:
    key: str                   # stabile ID: f"{surface_id}#<index>"
    boundary_ids: list[str]    # interne Objekt-IDs der Loch-Randlinien/-boegen
    no: Optional[int] = None


@dataclass
class SurfaceTransfer:
    surface_id: str
    boundary_ids: list[str]    # interne Objekt-IDs der Randlinien/-boegen
    no: Optional[int] = None
    openings: list[OpeningTransfer] = field(default_factory=list)


@dataclass
class TransferPlan:
    nodes: list[NodeTransfer] = field(default_factory=list)
    lines: list[LineTransfer] = field(default_factory=list)
    surfaces: list[SurfaceTransfer] = field(default_factory=list)
    skipped_views: list[str] = field(default_factory=list)

    @property
    def new_node_count(self) -> int:
        return sum(1 for n in self.nodes if n.no is None)

    @property
    def new_line_count(self) -> int:
        return sum(1 for l in self.lines if l.no is None)

    @property
    def new_surface_count(self) -> int:
        return sum(1 for s in self.surfaces if s.no is None)

    @property
    def openings(self) -> list["OpeningTransfer"]:
        return [o for s in self.surfaces for o in s.openings]

    def summary(self) -> str:
        parts = [
            f"Knoten: {self.new_node_count} neu, "
            f"{len(self.nodes) - self.new_node_count} aktualisieren",
            f"Linien: {self.new_line_count} neu, "
            f"{len(self.lines) - self.new_line_count} aktualisieren",
        ]
        if self.surfaces:
            parts.append(
                f"Flaechen: {self.new_surface_count} neu, "
                f"{len(self.surfaces) - self.new_surface_count} aktualisieren")
        if self.openings:
            new_op = sum(1 for o in self.openings if o.no is None)
            parts.append(
                f"Aussparungen: {new_op} neu, "
                f"{len(self.openings) - new_op} aktualisieren")
        if self.skipped_views:
            parts.append(
                "Uebersprungen (kein Referenzpunkt): "
                + ", ".join(self.skipped_views))
        return "\n".join(parts)

    @property
    def is_empty(self) -> bool:
        return not self.nodes and not self.lines and not self.surfaces


def build_plan(project: Project) -> TransferPlan:
    """Stellt zusammen, was uebertragen wuerde (rein lokal, testbar)."""
    plan = TransferPlan()
    for view in project.ordered_views():
        tf = view.transform()
        if tf is None:
            if project.model.points_in_view(view.id):
                plan.skipped_views.append(view.name)
            continue
        for p in project.model.points_in_view(view.id):
            x, y, z = tf.pdf_to_rfem(p.pos)
            plan.nodes.append(NodeTransfer(
                p.id, (round(x, 6), round(y, 6), round(z, 6)),
                no=project.rfem_node_map.get(p.id)))
        for line in project.model.lines_in_view(view.id):
            plan.lines.append(LineTransfer(
                line.id, list(line.point_ids), line.closed,
                no=project.rfem_line_map.get(line.id)))
        for arc in project.model.arcs_in_view(view.id):
            cx, cy, cz = tf.pdf_to_rfem(arc.control)
            plan.lines.append(LineTransfer(
                arc.id, list(arc.point_ids), closed=False,
                no=project.rfem_line_map.get(arc.id),
                arc_control=(round(cx, 6), round(cy, 6), round(cz, 6))))
        for surface in project.model.surfaces_in_view(view.id):
            openings = []
            for idx, hole_ids in enumerate(surface.opening_ids):
                key = f"{surface.id}#{idx}"
                openings.append(OpeningTransfer(
                    key, list(hole_ids),
                    no=project.rfem_opening_map.get(key)))
            plan.surfaces.append(SurfaceTransfer(
                surface.id, list(surface.boundary_ids),
                no=project.rfem_surface_map.get(surface.id),
                openings=openings))
    return plan


class RfemConnector:
    def __init__(self, url: str = "127.0.0.1", port: int = 9000) -> None:
        self.url = url
        self.port = port
        self.app = None

    # --- Verbindung -----------------------------------------------------------
    def connect(self) -> str:
        """Verbindet und liefert eine Infozeile (Name + Version)."""
        if self.app is not None:
            return self.info()
        try:
            from dlubal.api import rfem
        except ImportError as e:
            raise RfemError(f"dlubal.api nicht installiert: {e}") from e
        try:
            self.app = rfem.Application(
                api_key_value=os.environ.get("PDF2RFEM_API_KEY"),
                url=self.url, port=self.port)
        except Exception as e:
            self.app = None
            raise RfemError(
                f"Keine Verbindung zu RFEM ({self.url}:{self.port}).\n"
                f"Laeuft RFEM 6 und ist der API-Key hinterlegt?\n\n{e}") from e
        return self.info()

    def info(self) -> str:
        info = self.app.get_application_info()
        return f"{info.name} {info.version} ({info.language_name})"

    def close(self) -> None:
        if self.app is not None:
            try:
                self.app.close_connection()
            except Exception:
                pass
            self.app = None

    # --- Uebertragung -----------------------------------------------------------
    def transfer(self, project: Project, plan: TransferPlan) -> str:
        """Fuehrt den Plan aus und aktualisiert die Zuordnungstabellen."""
        from dlubal.api import rfem

        self.connect()
        self._ensure_model(rfem)

        existing_nodes = self._existing_numbers(rfem.OBJECT_TYPE_NODE)
        existing_lines = self._existing_numbers(rfem.OBJECT_TYPE_LINE)
        existing_surfaces = self._existing_numbers(rfem.OBJECT_TYPE_SURFACE)
        existing_openings = self._existing_numbers(rfem.OBJECT_TYPE_OPENING)

        # Zuordnungen, deren Objekt in RFEM inzwischen geloescht wurde,
        # als "neu" behandeln statt ins Leere zu aktualisieren.
        for n in plan.nodes:
            if n.no is not None and n.no not in existing_nodes:
                n.no = None
        for l in plan.lines:
            if l.no is not None and l.no not in existing_lines:
                l.no = None
        for s in plan.surfaces:
            if s.no is not None and s.no not in existing_surfaces:
                s.no = None
            for o in s.openings:
                if o.no is not None and o.no not in existing_openings:
                    o.no = None

        next_node = max(existing_nodes | set(project.rfem_node_map.values()),
                        default=0) + 1
        next_line = max(existing_lines | set(project.rfem_line_map.values()),
                        default=0) + 1
        next_surface = max(existing_surfaces
                           | set(project.rfem_surface_map.values()),
                           default=0) + 1
        next_opening = max(existing_openings
                           | set(project.rfem_opening_map.values()),
                           default=0) + 1

        new_nodes, upd_nodes = [], []
        node_no_by_point: dict[str, int] = {}
        for n in plan.nodes:
            if n.no is None:
                n.no = next_node
                next_node += 1
                bucket = new_nodes
            else:
                bucket = upd_nodes
            node_no_by_point[n.point_id] = n.no
            bucket.append(rfem.structure_core.Node(
                no=n.no, coordinate_1=n.coords[0],
                coordinate_2=n.coords[1], coordinate_3=n.coords[2]))

        new_lines, upd_lines = [], []
        for l in plan.lines:
            nos = [node_no_by_point[pid] for pid in l.point_ids]
            if l.closed and len(nos) > 2:
                nos.append(nos[0])
            if l.no is None:
                l.no = next_line
                next_line += 1
                bucket = new_lines
            else:
                bucket = upd_lines
            if l.arc_control is not None:
                bucket.append(rfem.structure_core.Line(
                    no=l.no, type=rfem.structure_core.Line.TYPE_ARC,
                    definition_nodes=nos,
                    arc_control_point_x=l.arc_control[0],
                    arc_control_point_y=l.arc_control[1],
                    arc_control_point_z=l.arc_control[2]))
            else:
                bucket.append(rfem.structure_core.Line(
                    no=l.no, type=rfem.structure_core.Line.TYPE_POLYLINE,
                    definition_nodes=nos))

        # Flaechen: Randobjekte -> RFEM-Liniennummern
        line_no_by_obj = {l.line_id: l.no for l in plan.lines}
        new_surfaces, upd_surfaces = [], []
        for s in plan.surfaces:
            boundary_nos = [line_no_by_obj.get(oid)
                            or project.rfem_line_map.get(oid)
                            for oid in s.boundary_ids]
            if any(no is None for no in boundary_nos):
                raise RfemError(
                    f"Flaeche {s.surface_id}: Randlinie ohne RFEM-Nummer - "
                    "Randobjekte zuerst uebertragen.")
            if s.no is None:
                s.no = next_surface
                next_surface += 1
                bucket = new_surfaces
            else:
                bucket = upd_surfaces
            bucket.append(rfem.structure_core.Surface(
                no=s.no, boundary_lines=boundary_nos))

        # Aussparungen (Openings): eigene Randlinien, liegen auf der Flaeche
        new_openings, upd_openings = [], []
        for s in plan.surfaces:
            for o in s.openings:
                boundary_nos = [line_no_by_obj.get(oid)
                                or project.rfem_line_map.get(oid)
                                for oid in o.boundary_ids]
                if any(no is None for no in boundary_nos):
                    raise RfemError(
                        f"Aussparung {o.key}: Randlinie ohne RFEM-Nummer.")
                if o.no is None:
                    o.no = next_opening
                    next_opening += 1
                    bucket = new_openings
                else:
                    bucket = upd_openings
                bucket.append(rfem.structure_core.Opening(
                    no=o.no, boundary_lines=boundary_nos))

        try:
            # Reihenfolge: Knoten -> Linien -> Flaechen -> Aussparungen.
            if new_nodes:
                self.app.create_object_list(new_nodes)
            if upd_nodes:
                self.app.update_object_list(upd_nodes)
            if new_lines:
                self.app.create_object_list(new_lines)
            if upd_lines:
                self.app.update_object_list(upd_lines)
            if new_surfaces:
                self.app.create_object_list(new_surfaces)
            if upd_surfaces:
                self.app.update_object_list(upd_surfaces)
            if new_openings:
                self.app.create_object_list(new_openings)
            if upd_openings:
                self.app.update_object_list(upd_openings)
        except Exception as e:
            raise RfemError(f"Uebertragung fehlgeschlagen:\n{e}") from e

        # Erst nach erfolgreicher Uebertragung die Zuordnung festschreiben.
        for n in plan.nodes:
            project.rfem_node_map[n.point_id] = n.no
        for l in plan.lines:
            project.rfem_line_map[l.line_id] = l.no
        for s in plan.surfaces:
            project.rfem_surface_map[s.surface_id] = s.no
            for o in s.openings:
                project.rfem_opening_map[o.key] = o.no
        project.dirty = True

        msg = (f"Uebertragen: {len(new_nodes)} Knoten neu, "
               f"{len(upd_nodes)} aktualisiert; "
               f"{len(new_lines)} Linien neu, {len(upd_lines)} aktualisiert")
        if plan.surfaces:
            msg += (f"; {len(new_surfaces)} Flaechen neu, "
                    f"{len(upd_surfaces)} aktualisiert "
                    "(Dicke/Material in RFEM zuweisen)")
        if plan.openings:
            msg += (f"; {len(new_openings)} Aussparungen neu, "
                    f"{len(upd_openings)} aktualisiert")
        return msg + "."

    # --- intern -----------------------------------------------------------------
    def _ensure_model(self, rfem) -> None:
        """Stellt sicher, dass in RFEM ein Modell aktiv ist."""
        try:
            self.app.get_active_model()
        except Exception:
            try:
                self.app.create_model(name="PDF2RFEM")
            except Exception as e:
                raise RfemError(
                    f"Kein aktives RFEM-Modell und Anlegen fehlgeschlagen:\n{e}"
                ) from e

    def _existing_numbers(self, object_type) -> set[int]:
        try:
            id_list = self.app.get_object_id_list(object_type=object_type)
            return {oid.no for oid in id_list.object_id}
        except Exception:
            # Abfrage nicht moeglich -> konservativ nur eigene Zuordnung nutzen
            return set()
