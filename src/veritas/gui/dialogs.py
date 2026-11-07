"""Diálogos de edición (plan T-030 E2b-2; R2-D7). Requiere el extra opcional ``gui``.

Son finos: cada diálogo recoge texto y elecciones, y delega la validación en ``gui.forms`` (sin
Qt). Si el formulario no es válido, el diálogo NO se cierra y muestra el diagnóstico en español en
una etiqueta propia (no hay cuadros modales de error), de modo que se puede corregir y repetir.
Al aceptar, ``result_input`` contiene la entrada ya validada; el diálogo nunca toca el modelo.
"""

from enum import StrEnum
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily
from veritas.core.messages_es import render_message
from veritas.core.presentation import PROFILE_SI, PresentationProfile
from veritas.core.sections.parametric import ParametricShape
from veritas.gui import forms
from veritas.gui.deletion import DeletionPlan

FAMILY_NAMES = {
    MaterialFamily.STEEL: "Acero",
    MaterialFamily.ALUMINIUM: "Aluminio",
    MaterialFamily.TIMBER: "Madera",
}
RECTANGULAR = "Rectangular"
NO_CHOICE = "(elegir)"


class FormDialog(QDialog):
    """Diálogo con formulario, etiqueta de error y Aceptar/Cancelar."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.result_input: Any = None
        self.form = QFormLayout()
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #b00020;")
        self.error_label.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(self.form)
        layout.addWidget(self.error_label)
        layout.addWidget(self.buttons)

    def build(self) -> Any:  # pragma: no cover (lo define cada diálogo)
        raise NotImplementedError

    def accept(self) -> None:
        try:
            self.result_input = self.build()
        except DiagnosticError as error:
            self.error_label.setText(render_message(error.diagnostic))
            return
        self.error_label.setText("")
        super().accept()


def _enum[E: StrEnum](kind: type[E], data: Any) -> E | None:
    """Qt devuelve el texto de un ``StrEnum`` guardado como dato: se vuelve a convertir."""
    return None if data is None else kind(data)


def _combo(items: list[tuple[str, Any]], *, placeholder: bool = True) -> QComboBox:
    combo = QComboBox()
    if placeholder:
        combo.addItem(NO_CHOICE, None)
    for text, value in items:
        combo.addItem(text, value)
    return combo


class NodeDialog(FormDialog):
    """Nuevo nudo: etiqueta y X, Y, Z (unidad opcional)."""

    def __init__(
        self, profile: PresentationProfile = PROFILE_SI, parent: QWidget | None = None
    ) -> None:
        super().__init__("Nuevo nudo", parent)
        self._profile = profile
        self.label = QLineEdit()
        self.x_edit, self.y_edit, self.z_edit = QLineEdit("0"), QLineEdit("0"), QLineEdit("0")
        for text, widget in (
            ("Etiqueta", self.label),
            ("X", self.x_edit),
            ("Y", self.y_edit),
            ("Z", self.z_edit),
        ):
            self.form.addRow(text, widget)

    def build(self) -> forms.NodeInput:
        return forms.parse_node(
            self.label.text(),
            self.x_edit.text(),
            self.y_edit.text(),
            self.z_edit.text(),
            profile=self._profile,
        )


class MemberDialog(FormDialog):
    """Nueva barra: nudos I y J, material, sección, orientación opcional y etiqueta."""

    def __init__(self, model: StructuralModel, parent: QWidget | None = None) -> None:
        super().__init__("Nueva barra", parent)
        self.node_i = _combo([(c.text, c.value) for c in forms.node_choices(model)])
        self.node_j = _combo([(c.text, c.value) for c in forms.node_choices(model)])
        self.material = _combo([(c.text, c.value) for c in forms.material_choices(model)])
        self.section = _combo([(c.text, c.value) for c in forms.section_choices(model)])
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("x; y; z (vacío = referencia por defecto)")
        self.alpha = QLineEdit()
        self.alpha.setPlaceholderText("grados (vacío = conservar el giro actual)")
        self.label = QLineEdit()
        for text, widget in (
            ("Nudo I", self.node_i),
            ("Nudo J", self.node_j),
            ("Material", self.material),
            ("Sección", self.section),
            ("Referencia", self.reference),
            ("Giro alfa", self.alpha),
            ("Etiqueta", self.label),
        ):
            self.form.addRow(text, widget)

    def build(self) -> forms.MemberInput:
        return forms.parse_member(
            node_i=self.node_i.currentData(),
            node_j=self.node_j.currentData(),
            material=self.material.currentData(),
            section=self.section.currentData(),
            reference=self.reference.text(),
            alpha=self.alpha.text(),
            label=self.label.text(),
        )


class OrientationDialog(FormDialog):
    """Orientación de una barra: vector de referencia y giro alfa."""

    def __init__(
        self,
        parent: QWidget | None = None,
        defaults: forms.OrientationDefaults | None = None,
    ) -> None:
        super().__init__("Orientar barra", parent)
        initial = defaults or forms.OrientationDefaults()
        self.reference = QLineEdit(initial.reference)
        self.reference.setPlaceholderText(
            "varios valores (vacío = se conservan)"
            if initial.reference_mixed
            else "x; y; z (vacío = referencia por defecto)"
        )
        self.alpha = QLineEdit(initial.alpha)
        self.alpha.setPlaceholderText(
            "varios valores (vacío = se conservan)"
            if initial.alpha_mixed
            else "grados (vacío = conservar el giro actual)"
        )
        self.form.addRow("Referencia", self.reference)
        self.form.addRow("Giro alfa", self.alpha)

    def build(self) -> tuple[str, str]:
        forms.parse_orientation(self.reference.text(), self.alpha.text())  # valida
        return self.reference.text(), self.alpha.text()


class MaterialDialog(FormDialog):
    """Nuevo material (acero, aluminio o madera): E, G y densidad editables."""

    def __init__(
        self, profile: PresentationProfile = PROFILE_SI, parent: QWidget | None = None
    ) -> None:
        super().__init__("Nuevo material", parent)
        self._profile = profile
        self.name = QLineEdit()
        self.family = _combo([(FAMILY_NAMES[f], f) for f in MaterialFamily])
        self.e, self.g, self.density = QLineEdit(), QLineEdit(), QLineEdit()
        self.e.setPlaceholderText("p. ej. 210000 N/mm²")
        self.g.setPlaceholderText("p. ej. 81000 N/mm²")
        self.density.setPlaceholderText("p. ej. 7850 kg/m³")
        self.family.currentIndexChanged.connect(self._suggest)
        for text, widget in (
            ("Nombre", self.name),
            ("Familia", self.family),
            ("E (módulo elástico)", self.e),
            ("G (módulo de cortante)", self.g),
            ("Densidad", self.density),
        ):
            self.form.addRow(text, widget)

    def _suggest(self) -> None:
        """Valores iniciales físicos del acero y el aluminio; la madera se escribe a mano."""
        family = self.family.currentData()
        preset = forms.MATERIAL_PRESETS.get(family) if family is not None else None
        if preset is not None:
            self.e.setText(preset[0])
            self.g.setText(preset[1])
            self.density.setText(preset[2])

    def build(self) -> forms.MaterialInput:
        return forms.parse_material(
            name=self.name.text(),
            family=_enum(MaterialFamily, self.family.currentData()),
            elastic_modulus=self.e.text(),
            shear_modulus=self.g.text(),
            density=self.density.text(),
            profile=self._profile,
        )


class SectionDialog(FormDialog):
    """Nueva sección: rectangular maciza o perfil paramétrico I, RHS, SHS o CHS."""

    def __init__(
        self, profile: PresentationProfile = PROFILE_SI, parent: QWidget | None = None
    ) -> None:
        super().__init__("Nueva sección", parent)
        self._profile = profile
        self.name = QLineEdit()
        self.kind = QComboBox()
        self.kind.addItem(NO_CHOICE, None)
        self.kind.addItem(RECTANGULAR, RECTANGULAR)
        for shape in ParametricShape:
            self.kind.addItem(str(shape), shape)
        self.fields: dict[str, QLineEdit] = {}
        self.form.addRow("Nombre", self.name)
        self.form.addRow("Tipo", self.kind)
        self._first_field_row = self.form.rowCount()
        self.kind.currentIndexChanged.connect(self._rebuild_fields)

    def _rebuild_fields(self) -> None:
        while self.form.rowCount() > self._first_field_row:
            self.form.removeRow(self._first_field_row)
        self.fields = {}
        kind = self.kind.currentData()
        if kind is None:
            return
        names = (
            (("b", False), ("h", False))
            if kind == RECTANGULAR
            else forms.parametric_fields(ParametricShape(kind))
        )
        for name, optional in names:
            edit = QLineEdit()
            edit.setPlaceholderText("opcional (vacío = 0)" if optional else "p. ej. 200 mm")
            self.fields[name] = edit
            self.form.addRow(name, edit)

    def build(self) -> forms.ParametricInput | tuple[str, Any, Any]:
        kind = self.kind.currentData()
        values = {name: edit.text() for name, edit in self.fields.items()}
        if kind == RECTANGULAR:
            return forms.parse_rectangular_section(
                name=self.name.text(), b=values["b"], h=values["h"], profile=self._profile
            )
        return forms.parse_parametric_section(
            name=self.name.text(),
            shape=_enum(ParametricShape, kind),
            values=values,
            profile=self._profile,
        )


class DeletionDialog(FormDialog):
    """Confirmación de borrado con la vista previa de la cascada; Aceptar solo si es válida."""

    def __init__(self, text: str, plan: DeletionPlan, parent: QWidget | None = None) -> None:
        super().__init__("Borrar selección", parent)
        self.preview = QPlainTextEdit(text)
        self.preview.setReadOnly(True)
        self.form.addRow("Se borrará", self.preview)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(plan.ok and not plan.empty)
        if plan.error is not None:
            self.error_label.setText(render_message(plan.error))

    def build(self) -> bool:
        return True


class ChoiceDialog(FormDialog):
    """Elegir un valor de una lista (asignar material o sección a la selección)."""

    def __init__(
        self,
        title: str,
        label: str,
        options: list[tuple[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.combo = _combo(options)
        self.form.addRow(label, self.combo)

    def build(self) -> Any:
        return forms.chosen(self.combo.currentData(), "una opción", "Opción")
