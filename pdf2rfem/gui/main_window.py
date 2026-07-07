"""Hauptfenster: verbindet Canvas, Werkzeuge, Ansichten-/Objektlisten und RFEM.

RFEM-Aufrufe laufen in einem Worker-Thread, damit das GUI bei gRPC-Aufrufen
nie einfriert. Die Uebertragung ist eine explizite Aktion mit Vorschau.
"""
from __future__ import annotations

import traceback
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QLabel, QListWidget,
                               QListWidgetItem, QDockWidget, QHBoxLayout,
                               QMainWindow, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget, QAbstractItemView)

from ..core.commands import CommandStack
from ..core.project import Project
from ..core.transform import MM_PER_PDF_POINT
from ..infra.pdf_document import PdfDocument
from ..infra.rfem_connector import RfemConnector, RfemError, build_plan
from .canvas import PdfCanvas
from .dialogs import ViewDialog
from .tools import ToolController


class RfemWorker(QThread):
    """Fuehrt eine RFEM-Aktion im Hintergrund aus."""
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], str], parent=None) -> None:
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self.fn())
        except RfemError as e:
            self.failed.emit(str(e))
        except Exception:
            self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF2RFEM")
        self.resize(1400, 900)

        self.project: Optional[Project] = None
        self.pdf: Optional[PdfDocument] = None
        self.stack: Optional[CommandStack] = None
        self.selection: set[str] = set()
        self.connector = RfemConnector()
        self._worker: Optional[RfemWorker] = None
        self._updating_ui = False
        self._plan_providers: dict[int, object] = {}

        self.canvas = PdfCanvas(self)
        self.setCentralWidget(self.canvas)
        self.controller = ToolController(self, self.canvas)
        self.canvas.controller = self.controller
        self.canvas.mouse_moved.connect(self._on_mouse_moved)
        self.canvas.mouse_left_view.connect(
            lambda: self.coords_label.setText(""))

        self._build_actions()
        self._build_docks()
        self._build_statusbar()
        self._update_enabled()

    # ------------------------------------------------------------------ UI-Aufbau
    def _build_actions(self) -> None:
        menu = self.menuBar()
        toolbar = self.addToolBar("Werkzeuge")
        toolbar.setMovable(False)

        m_file = menu.addMenu("&Datei")
        self.act_open_pdf = QAction("PDF oeffnen (neues Projekt)...", self)
        self.act_open_pdf.setShortcut(QKeySequence("Ctrl+N"))
        self.act_open_pdf.triggered.connect(self.open_pdf)
        self.act_open_project = QAction("Projekt oeffnen...", self)
        self.act_open_project.setShortcut(QKeySequence.Open)
        self.act_open_project.triggered.connect(self.open_project)
        self.act_save = QAction("Projekt speichern", self)
        self.act_save.setShortcut(QKeySequence.Save)
        self.act_save.triggered.connect(self.save_project)
        self.act_save_as = QAction("Projekt speichern unter...", self)
        self.act_save_as.triggered.connect(lambda: self.save_project(save_as=True))
        for a in (self.act_open_pdf, self.act_open_project,
                  self.act_save, self.act_save_as):
            m_file.addAction(a)
        m_file.addSeparator()
        act_quit = QAction("Beenden", self)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_edit = menu.addMenu("&Bearbeiten")
        self.act_undo = QAction("Rueckgaengig", self)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_undo.triggered.connect(lambda: self.stack and self.stack.undo())
        self.act_redo = QAction("Wiederholen", self)
        self.act_redo.setShortcut(QKeySequence.Redo)
        self.act_redo.triggered.connect(lambda: self.stack and self.stack.redo())
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)

        # Werkzeuge (checkbar, Tastenkuerzel S/P/L/R/M)
        m_tools = menu.addMenu("&Werkzeuge")
        group = QActionGroup(self)
        self.tool_actions: dict[str, QAction] = {}
        for name, label, key in (
                ("select", "Auswahl", "S"),
                ("point", "Punkt", "P"),
                ("polyline", "Polylinie", "L"),
                ("trace", "Linie abgreifen", "T"),
                ("region", "Flaeche aufnehmen", "F"),
                ("refpoint", "Referenzpunkt", "R"),
                ("measure", "Massstab pruefen", "M")):
            act = QAction(label, self, checkable=True)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(lambda _=False, n=name: self.set_tool(n))
            group.addAction(act)
            m_tools.addAction(act)
            toolbar.addAction(act)
            self.tool_actions[name] = act
        self.tool_actions["select"].setChecked(True)
        m_tools.addSeparator()
        self.act_plan_snap = QAction("Plan-Snap (Linien/Schnittpunkte)", self,
                                     checkable=True, checked=True)
        self.act_plan_snap.setShortcut(QKeySequence("G"))
        self.act_plan_snap.toggled.connect(self._toggle_plan_snap)
        m_tools.addAction(self.act_plan_snap)
        toolbar.addSeparator()
        toolbar.addAction(self.act_plan_snap)
        toolbar.addAction(self.act_undo)
        toolbar.addAction(self.act_redo)

        m_view = menu.addMenu("&Ansichten")
        self.act_add_view = QAction("Neue Ansicht...", self)
        self.act_add_view.triggered.connect(self.add_view)
        self.act_edit_view = QAction("Aktive Ansicht bearbeiten...", self)
        self.act_edit_view.triggered.connect(self.edit_view)
        self.act_del_view = QAction("Aktive Ansicht loeschen", self)
        self.act_del_view.triggered.connect(self.delete_view)
        self.act_fit = QAction("Seite einpassen", self)
        self.act_fit.setShortcut(QKeySequence("Ctrl+0"))
        self.act_fit.triggered.connect(self.canvas.fit_page)
        for a in (self.act_add_view, self.act_edit_view,
                  self.act_del_view, self.act_fit):
            m_view.addAction(a)

        m_rfem = menu.addMenu("&RFEM")
        self.act_rfem_test = QAction("Verbindung testen", self)
        self.act_rfem_test.triggered.connect(self.test_rfem)
        self.act_rfem_transfer = QAction("Nach RFEM uebertragen...", self)
        self.act_rfem_transfer.setShortcut(QKeySequence("F5"))
        self.act_rfem_transfer.triggered.connect(self.transfer_to_rfem)
        m_rfem.addAction(self.act_rfem_test)
        m_rfem.addAction(self.act_rfem_transfer)
        toolbar.addSeparator()
        toolbar.addAction(self.act_rfem_transfer)

    def _build_docks(self) -> None:
        # --- Ansichten -------------------------------------------------------
        self.view_list = QListWidget()
        self.view_list.currentRowChanged.connect(self._on_view_selected)
        self.view_list.itemChanged.connect(self._on_view_check_changed)
        btn_add = QPushButton("Neu")
        btn_add.clicked.connect(self.add_view)
        btn_edit = QPushButton("Bearbeiten")
        btn_edit.clicked.connect(self.edit_view)
        btn_del = QPushButton("Loeschen")
        btn_del.clicked.connect(self.delete_view)
        btn_row = QHBoxLayout()
        for b in (btn_add, btn_edit, btn_del):
            btn_row.addWidget(b)
        views_widget = QWidget()
        vl = QVBoxLayout(views_widget)
        vl.setContentsMargins(4, 4, 4, 4)
        vl.addWidget(self.view_list)
        vl.addLayout(btn_row)
        dock = QDockWidget("Ansichten", self)
        dock.setWidget(views_widget)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

        # --- Objekte ----------------------------------------------------------
        self.obj_table = QTableWidget(0, 4)
        self.obj_table.setHorizontalHeaderLabels(
            ["Typ", "Ansicht", "Koordinaten / Info", "RFEM-Nr."])
        self.obj_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.obj_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.obj_table.itemSelectionChanged.connect(self._on_table_selection)
        self.obj_table.horizontalHeader().setStretchLastSection(True)
        dock2 = QDockWidget("Objekte", self)
        dock2.setWidget(self.obj_table)
        dock2.setFeatures(QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.RightDockWidgetArea, dock2)

    def _build_statusbar(self) -> None:
        self.view_label = QLabel("")
        self.coords_label = QLabel("")
        self.statusBar().addPermanentWidget(self.view_label)
        self.statusBar().addPermanentWidget(self.coords_label)
        self.show_status("PDF oeffnen (Strg+N), um zu beginnen.")

    # ------------------------------------------------------------------ Projekt
    def open_pdf(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "PDF-Plan oeffnen", "", "PDF-Dateien (*.pdf)")
        if not path:
            return
        try:
            pdf = PdfDocument(path)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"PDF nicht lesbar:\n{e}")
            return
        self._set_project(Project(path), pdf)
        self.add_view()
        if not self.project.views:
            self.show_status("Projekt ohne Ansicht - ueber 'Ansichten > Neue "
                             "Ansicht' anlegen.")

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Projekt oeffnen", "", "PDF2RFEM-Projekt (*.p2r.json *.json)")
        if not path:
            return
        try:
            project = Project.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Projekt nicht lesbar:\n{e}")
            return
        if not Path(project.pdf_path).exists():
            QMessageBox.warning(
                self, "PDF fehlt",
                f"Das PDF wurde nicht gefunden:\n{project.pdf_path}\n\n"
                "Bitte neu auswaehlen.")
            pdf_path, _ = QFileDialog.getOpenFileName(
                self, "PDF-Plan suchen", "", "PDF-Dateien (*.pdf)")
            if not pdf_path:
                return
            project.pdf_path = pdf_path
            project.dirty = True
        try:
            pdf = PdfDocument(project.pdf_path)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"PDF nicht lesbar:\n{e}")
            return
        self._set_project(project, pdf)

    def save_project(self, save_as: bool = False) -> bool:
        if self.project is None:
            return False
        path = self.project.file_path
        if save_as or not path:
            suggestion = str(Path(self.project.pdf_path).with_suffix(".p2r.json"))
            path, _ = QFileDialog.getSaveFileName(
                self, "Projekt speichern", suggestion,
                "PDF2RFEM-Projekt (*.p2r.json)")
            if not path:
                return False
        try:
            self.project.save(path)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Speichern fehlgeschlagen:\n{e}")
            return False
        self._update_title()
        self.show_status(f"Gespeichert: {path}")
        return True

    def _toggle_plan_snap(self, checked: bool) -> None:
        self.controller.snap_engine.plan_snap_enabled = checked
        self.show_status("Plan-Snap " + ("aktiv" if checked else "aus"))

    def get_plan_provider(self, page_index: Optional[int]):
        """Snap-Provider je Seite; Vektorindex wird beim ersten Zugriff gebaut."""
        if self.pdf is None or page_index is None:
            return None
        if page_index not in self._plan_providers:
            from PySide6.QtWidgets import QApplication
            from ..infra.plan_snap import PlanSnapProvider
            provider = PlanSnapProvider(self.pdf, page_index)
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                provider.vector  # Index jetzt bauen, nicht mitten im Zeichnen
            finally:
                QApplication.restoreOverrideCursor()
            self._plan_providers[page_index] = provider
            if not provider.is_vector_plan:
                self.show_status(
                    "Seite ohne Vektordaten (Scan) - Snapping nutzt "
                    "OpenCV-Eckenerkennung.")
        return self._plan_providers[page_index]

    def _set_project(self, project: Project, pdf: PdfDocument) -> None:
        if self.pdf is not None:
            self.pdf.close()
        self.project = project
        self.pdf = pdf
        self.selection = set()
        self._plan_providers = {}
        self.stack = CommandStack(project)
        project.model.add_listener(self.refresh_all)
        view = project.active_view
        self.canvas.show_page(pdf, view.page_index if view else None, fit=True)
        self.set_tool("select")
        self.refresh_all()
        self._update_enabled()
        if view and not view.is_ready:
            self.show_status("Referenzpunkt setzen (Taste R), dann zeichnen.")

    def _confirm_discard(self) -> bool:
        if self.project is None or not self.project.dirty:
            return True
        ret = QMessageBox.question(
            self, "Ungespeicherte Aenderungen",
            "Das Projekt hat ungespeicherte Aenderungen. Speichern?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if ret == QMessageBox.Save:
            return self.save_project()
        return ret == QMessageBox.Discard

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------------ Ansichten
    def add_view(self) -> None:
        if self.project is None or self.pdf is None:
            return
        dlg = ViewDialog(self.project, self.pdf.page_count, parent=self)
        if dlg.exec():
            view = dlg.apply()
            self.project.add_view(view)
            self.project.active_view_id = view.id
            self.canvas.show_page(self.pdf, view.page_index, fit=True)
            self.refresh_all()
            self.show_status(f"Ansicht '{view.name}' angelegt - Referenzpunkt "
                             "setzen (Taste R).")

    def edit_view(self) -> None:
        view = self.project.active_view if self.project else None
        if view is None:
            return
        dlg = ViewDialog(self.project, self.pdf.page_count, view=view,
                         parent=self)
        if dlg.exec():
            dlg.apply()
            self.project.dirty = True
            self.canvas.show_page(self.pdf, view.page_index)
            self.refresh_all()

    def delete_view(self) -> None:
        view = self.project.active_view if self.project else None
        if view is None:
            return
        n_objects = (len(self.project.model.points_in_view(view.id))
                     + len(self.project.model.lines_in_view(view.id)))
        ret = QMessageBox.question(
            self, "Ansicht loeschen",
            f"Ansicht '{view.name}' mit {n_objects} Objekten loeschen?\n"
            "(Dies kann nicht rueckgaengig gemacht werden.)")
        if ret != QMessageBox.Yes:
            return
        self.project.remove_view(view.id)
        self.selection = set()
        active = self.project.active_view
        self.canvas.show_page(self.pdf,
                              active.page_index if active else None, fit=True)
        self.refresh_all()

    def _on_view_selected(self, row: int) -> None:
        if self._updating_ui or self.project is None or row < 0:
            return
        view_id = self.view_list.item(row).data(Qt.UserRole)
        if view_id == self.project.active_view_id:
            return
        old = self.project.active_view
        self.project.active_view_id = view_id
        view = self.project.views[view_id]
        page_changed = old is None or old.page_index != view.page_index
        self.canvas.show_page(self.pdf, view.page_index, fit=page_changed)
        self.refresh_all()

    def _on_view_check_changed(self, item: QListWidgetItem) -> None:
        if self._updating_ui or self.project is None:
            return
        view = self.project.views.get(item.data(Qt.UserRole))
        if view is not None:
            view.visible = item.checkState() == Qt.Checked
            self.canvas.rebuild_objects(self.project, self.selection)

    # ------------------------------------------------------------------ RFEM
    def test_rfem(self) -> None:
        self._run_rfem(self.connector.connect,
                       lambda info: QMessageBox.information(
                           self, "RFEM", f"Verbunden mit:\n{info}"))

    def transfer_to_rfem(self) -> None:
        if self.project is None:
            return
        plan = build_plan(self.project)
        if plan.is_empty:
            QMessageBox.information(
                self, "RFEM", "Keine uebertragbaren Objekte vorhanden."
                + ("\n\n" + plan.summary() if plan.skipped_views else ""))
            return
        ret = QMessageBox.question(
            self, "Nach RFEM uebertragen",
            plan.summary() + "\n\nJetzt uebertragen?")
        if ret != QMessageBox.Yes:
            return

        def job() -> str:
            return self.connector.transfer(self.project, plan)

        def on_done(msg: str) -> None:
            self.refresh_all()
            QMessageBox.information(self, "RFEM", msg)

        self._run_rfem(job, on_done)

    def _run_rfem(self, fn: Callable[[], str],
                  on_done: Callable[[str], None]) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.show_status("RFEM-Aktion laeuft bereits...")
            return
        self.act_rfem_test.setEnabled(False)
        self.act_rfem_transfer.setEnabled(False)
        self.show_status("RFEM-Aktion laeuft...")

        def finish() -> None:
            self.act_rfem_test.setEnabled(True)
            self.act_rfem_transfer.setEnabled(self.project is not None)
            self.show_status("")

        worker = RfemWorker(fn, self)
        worker.done.connect(lambda msg: (finish(), on_done(msg)))
        worker.failed.connect(lambda msg: (
            finish(), QMessageBox.critical(self, "RFEM-Fehler", msg)))
        self._worker = worker
        worker.start()

    # ------------------------------------------------------------------ Anzeige
    def set_tool(self, name: str) -> None:
        self.controller.set_tool(name)
        self.tool_actions[name].setChecked(True)

    def show_status(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 8000)

    def refresh_all(self) -> None:
        self._update_title()
        self._update_undo_actions()
        self._refresh_view_list()
        self._refresh_object_table()
        self._update_view_label()
        self.canvas.rebuild_objects(self.project, self.selection)

    def _update_title(self) -> None:
        title = "PDF2RFEM"
        if self.project is not None:
            name = (Path(self.project.file_path).name
                    if self.project.file_path
                    else Path(self.project.pdf_path).name)
            title += f" - {name}" + ("*" if self.project.dirty else "")
        self.setWindowTitle(title)

    def _update_undo_actions(self) -> None:
        s = self.stack
        self.act_undo.setEnabled(bool(s and s.can_undo))
        self.act_redo.setEnabled(bool(s and s.can_redo))
        if s and s.can_undo:
            self.act_undo.setText(f"Rueckgaengig: {s.undo_text()}")
        else:
            self.act_undo.setText("Rueckgaengig")
        if s and s.can_redo:
            self.act_redo.setText(f"Wiederholen: {s.redo_text()}")
        else:
            self.act_redo.setText("Wiederholen")

    def _update_enabled(self) -> None:
        has_project = self.project is not None
        for a in (self.act_save, self.act_save_as, self.act_add_view,
                  self.act_edit_view, self.act_del_view, self.act_fit,
                  self.act_rfem_transfer):
            a.setEnabled(has_project)

    def _update_view_label(self) -> None:
        view = self.project.active_view if self.project else None
        if view is None:
            self.view_label.setText("")
            return
        text = (f"Aktiv: {view.name} | {view.workplane.describe()} | "
                f"1:{view.scale_denominator:g}")
        if not view.is_ready:
            text += " | KEIN REFERENZPUNKT"
            self.view_label.setStyleSheet("color: #d62728; font-weight: bold;")
        else:
            self.view_label.setStyleSheet("")
        self.view_label.setText(text)

    def _refresh_view_list(self) -> None:
        if self.project is None:
            self.view_list.clear()
            return
        self._updating_ui = True
        try:
            self.view_list.clear()
            for i, view in enumerate(self.project.ordered_views()):
                ready = "" if view.is_ready else "  (kein Referenzpunkt)"
                item = QListWidgetItem(
                    f"{view.name} - S.{view.page_index + 1} - "
                    f"1:{view.scale_denominator:g}{ready}")
                item.setData(Qt.UserRole, view.id)
                item.setForeground(QColor(view.color))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if view.visible else Qt.Unchecked)
                self.view_list.addItem(item)
                if view.id == self.project.active_view_id:
                    self.view_list.setCurrentRow(i)
        finally:
            self._updating_ui = False

    def _refresh_object_table(self) -> None:
        self._updating_ui = True
        try:
            self.obj_table.setRowCount(0)
            if self.project is None:
                return
            model = self.project.model
            rows = []
            for p in model.points.values():
                view = self.project.views.get(p.view_id)
                tf = view.transform() if view else None
                if tf:
                    x, y, z = tf.pdf_to_rfem(p.pos)
                    info = f"X {x:.3f}  Y {y:.3f}  Z {z:.3f} m"
                else:
                    info = "(kein Referenzpunkt)"
                rows.append((p.id, "Punkt", view.name if view else "?", info,
                             self.project.rfem_node_map.get(p.id)))
            for l in model.lines.values():
                view = self.project.views.get(l.view_id)
                kind = "Polygon" if l.closed else "Polylinie"
                rows.append((l.id, kind, view.name if view else "?",
                             f"{len(l.point_ids)} Punkte",
                             self.project.rfem_line_map.get(l.id)))
            self.obj_table.setRowCount(len(rows))
            for r, (oid, typ, vname, info, no) in enumerate(rows):
                for c, text in enumerate(
                        (typ, vname, info, str(no) if no else "-")):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.UserRole, oid)
                    self.obj_table.setItem(r, c, item)
        finally:
            self._updating_ui = False

    def _on_table_selection(self) -> None:
        if self._updating_ui or self.project is None:
            return
        ids = {item.data(Qt.UserRole)
               for item in self.obj_table.selectedItems()}
        self.selection = ids
        self.canvas.rebuild_objects(self.project, self.selection)

    def _on_mouse_moved(self, p) -> None:
        parts = [f"Papier {p.x * MM_PER_PDF_POINT:7.1f}, "
                 f"{p.y * MM_PER_PDF_POINT:7.1f} mm"]
        view = self.project.active_view if self.project else None
        if (view is not None and view.is_ready
                and view.page_index == self.canvas.page_index):
            x, y, z = view.transform().pdf_to_rfem(p)
            parts.append(f"RFEM  X {x:9.3f}   Y {y:9.3f}   Z {z:9.3f} m")
        self.coords_label.setText("      ".join(parts))
