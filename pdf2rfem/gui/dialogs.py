"""Dialoge: Ansicht anlegen/bearbeiten, Referenzpunkt-Ziel, Massstab pruefen."""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QLabel, QLineEdit, QSpinBox, QVBoxLayout)

from ..core.geometry import new_id
from ..core.project import Project, View
from ..core.transform import (AXIS_NAMES, MM_PER_PDF_POINT, Workplane,
                              implied_scale)


class ViewDialog(QDialog):
    """Anlegen/Bearbeiten einer Ansicht (Name, Seite, Massstab, Ebene)."""

    def __init__(self, project: Project, page_count: int,
                 view: Optional[View] = None, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.view = view
        self.setWindowTitle("Ansicht bearbeiten" if view else "Neue Ansicht")

        self.name_edit = QLineEdit(
            view.name if view else f"Ansicht {len(project.views) + 1}")
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, max(page_count, 1))
        self.page_spin.setValue((view.page_index if view else 0) + 1)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(1, 100000)
        self.scale_spin.setDecimals(1)
        self.scale_spin.setPrefix("1 : ")
        self.scale_spin.setValue(view.scale_denominator if view else 50)

        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(Workplane.PRESETS.keys()))
        self.flip_u = QCheckBox("u-Richtung spiegeln")
        self.flip_v = QCheckBox("v-Richtung spiegeln")
        if view:
            self._load_workplane(view.workplane)
        self.describe_label = QLabel()
        for w in (self.preset_combo,):
            w.currentIndexChanged.connect(self._update_describe)
        for w in (self.flip_u, self.flip_v):
            w.toggled.connect(self._update_describe)
        self._update_describe()

        form = QFormLayout()
        form.addRow("Name:", self.name_edit)
        form.addRow("PDF-Seite:", self.page_spin)
        form.addRow("Massstab:", self.scale_spin)
        form.addRow("Zeichenebene:", self.preset_combo)
        form.addRow("", self.flip_u)
        form.addRow("", self.flip_v)
        form.addRow("Abbildung:", self.describe_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        hint = QLabel("v zeigt auf dem Papier nach oben; die Presets fuer "
                      "Ansichten/Schnitte bilden v auf -Z ab (RFEM: Z nach unten).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _load_workplane(self, wp: Workplane) -> None:
        """Preset + Spiegel-Haken aus vorhandener Workplane rekonstruieren."""
        for i, (name, (au, su, av, sv)) in enumerate(Workplane.PRESETS.items()):
            if (au, av) == (wp.axis_u, wp.axis_v):
                self.preset_combo.setCurrentIndex(i)
                self.flip_u.setChecked(wp.sign_u != su)
                self.flip_v.setChecked(wp.sign_v != sv)
                return

    def workplane(self) -> Workplane:
        au, su, av, sv = Workplane.PRESETS[self.preset_combo.currentText()]
        if self.flip_u.isChecked():
            su = -su
        if self.flip_v.isChecked():
            sv = -sv
        return Workplane(au, su, av, sv)

    def _update_describe(self) -> None:
        self.describe_label.setText(self.workplane().describe())

    def apply(self) -> View:
        """Erzeugt eine neue View bzw. schreibt die Felder in die bestehende."""
        wp = self.workplane()
        if self.view is None:
            return View(
                id=new_id(), name=self.name_edit.text().strip() or "Ansicht",
                page_index=self.page_spin.value() - 1,
                scale_denominator=self.scale_spin.value(),
                workplane=wp, color=self.project.next_view_color())
        v = self.view
        v.name = self.name_edit.text().strip() or v.name
        v.page_index = self.page_spin.value() - 1
        v.scale_denominator = self.scale_spin.value()
        v.workplane = wp
        return v


class RefTargetDialog(QDialog):
    """RFEM-Zielkoordinaten fuer den angeklickten Referenzpunkt."""

    def __init__(self, view: View, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Referenzpunkt - {view.name}")
        self.spins: list[QDoubleSpinBox] = []
        form = QFormLayout()
        initial = view.ref_target or (0.0, 0.0, 0.0)
        fixed = view.workplane.fixed_axis
        for i, axis in enumerate(AXIS_NAMES):
            spin = QDoubleSpinBox()
            spin.setRange(-1e6, 1e6)
            spin.setDecimals(4)
            spin.setSuffix(" m")
            spin.setValue(initial[i])
            label = f"{axis}:"
            if i == fixed:
                label = f"{axis} (feste Achse der Ebene):"
            form.addRow(label, spin)
            self.spins.append(spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        info = QLabel(f"Ebene: {view.workplane.describe()}\n"
                      "Alle weiteren Klicks werden relativ zu diesem Punkt "
                      "umgerechnet.")
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def target(self) -> tuple[float, float, float]:
        return (self.spins[0].value(), self.spins[1].value(),
                self.spins[2].value())


class MeasureDialog(QDialog):
    """Ergebnis der 2-Punkt-Messung inkl. Massstabs-Verifikation.

    Nur Kontrolle/Warnung - der Nennmassstab bleibt die primaere Kalibrierung.
    """

    WARN_DEVIATION_PCT = 0.5

    def __init__(self, dist_pt: float, scale_denominator: float,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Massstab pruefen")
        self.dist_pt = dist_pt
        self.scale_denominator = scale_denominator

        paper_mm = dist_pt * MM_PER_PDF_POINT
        real_m = paper_mm * scale_denominator / 1000.0

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Gemessen auf Papier: {paper_mm:.2f} mm\n"
            f"Bei Massstab 1:{scale_denominator:g} entspricht das: "
            f"{real_m:.4f} m"))

        form = QFormLayout()
        self.expected_spin = QDoubleSpinBox()
        self.expected_spin.setRange(0, 1e6)
        self.expected_spin.setDecimals(4)
        self.expected_spin.setSuffix(" m")
        self.expected_spin.setValue(round(real_m, 4))
        self.expected_spin.valueChanged.connect(self._update)
        form.addRow("Sollmass laut Plan:", self.expected_spin)
        layout.addLayout(form)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(self.accept)
        layout.addWidget(buttons)
        self._update()

    def _update(self) -> None:
        expected = self.expected_spin.value()
        if expected <= 0 or self.dist_pt <= 0:
            self.result_label.setText("")
            return
        scale = implied_scale(self.dist_pt, expected)
        deviation = (scale / self.scale_denominator - 1.0) * 100.0
        text = (f"Tatsaechlicher Massstab dieser Strecke: 1:{scale:.2f}\n"
                f"Abweichung vom Nennmassstab: {deviation:+.2f} %")
        if abs(deviation) > self.WARN_DEVIATION_PCT:
            text += ("\n\nWARNUNG: Das PDF ist vermutlich nicht "
                     "massstabsgetreu (z.B. 'an Seite anpassen' beim Export). "
                     "Nennmassstab oder Plan pruefen!")
            self.result_label.setStyleSheet("color: #d62728; font-weight: bold;")
        else:
            self.result_label.setStyleSheet("color: #2ca02c;")
        self.result_label.setText(text)
