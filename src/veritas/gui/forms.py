"""Formularios de edición sin Qt (plan T-030 E2b-1; R2-D7).

Convierten el TEXTO de los campos de la ventana (coordenadas, orientación, elecciones) en
magnitudes y órdenes del ``ModelEditor``, o lanzan un ``InputValueError`` con diagnóstico en
español. Aquí no hay Qt ni estado: la ventana solo pinta lo que estas funciones devuelven.

Convenciones: el número admite coma o punto decimal; la unidad es opcional y, si falta, se usa la
del perfil de presentación para esa magnitud. Un vector se escribe «x; y; z» (o separado por
espacios). Los ángulos por defecto van en grados. Todo valor no finito se rechaza.
"""

import math
import re
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from veritas.core import units as u
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError
from veritas.core.geometry.editing import ModelEditor
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily, MaterialRevision
from veritas.core.presentation import PROFILE_SI, PresentationProfile, QuantityKind
from veritas.core.references import RevisionRef
from veritas.core.sections.parametric import (
    OPTIONAL_DIMENSIONS,
    SHAPE_DIMENSIONS,
    ParametricShape,
)
from veritas.core.sections.section import SectionRevision

_NUMBER: Final = re.compile(
    r"\s*([+-]?(?:\d+[.,]?\d*|[.,]\d+)(?:[eE][+-]?\d+)?)(?![\d.,])\s*(.*?)\s*"
)
_THOUSANDS: Final = re.compile(r"\s*[+-]?[1-9]\d{0,2},\d{3}(?!\d)")
"""«1,000» es ambiguo (¿mil o uno?): se rechaza en lugar de leerlo como 1,0."""
DEFAULT_ANGLE_UNIT: Final = "deg"
VECTOR_SIZE: Final = 3


def _error(code: DiagnosticCode, key: str, **params: str | int) -> InputValueError:
    return InputValueError(Diagnostic.create(code, key, params))


def _split(text: str, field: str) -> tuple[float, str]:
    """Número y unidad (posiblemente vacía) de ``text``."""
    if not isinstance(text, str) or not text.strip():
        raise _error(DiagnosticCode.INPUT_OUT_OF_RANGE, "forms.empty_value", field=field)
    if _THOUSANDS.match(text):
        raise _error(
            DiagnosticCode.INPUT_INVALID_TYPE, "forms.invalid_number", field=field, text=text
        )
    match = _NUMBER.fullmatch(text)
    if match is None:
        raise _error(
            DiagnosticCode.INPUT_INVALID_TYPE, "forms.invalid_number", field=field, text=text
        )
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:  # pragma: no cover (el patrón ya lo garantiza)
        raise _error(
            DiagnosticCode.INPUT_INVALID_TYPE, "forms.invalid_number", field=field, text=text
        ) from None
    if not math.isfinite(value):
        raise _error(DiagnosticCode.INPUT_NONFINITE, "forms.invalid_number", field=field, text=text)
    return value, match.group(2)


def parse_quantity(
    text: str,
    kind: QuantityKind,
    *,
    field: str,
    profile: PresentationProfile = PROFILE_SI,
    default_unit: str | None = None,
) -> u.Quantity:
    """Magnitud de ``kind`` a partir de «valor [unidad]»; sin unidad, la del perfil."""
    value, unit_text = _split(text, field)
    if unit_text:
        unit = u.get_unit(unit_text)
    else:
        unit = u.get_unit(default_unit) if default_unit else profile.unit_for(kind)
    if unit.dimension != kind.dimension:
        raise _error(
            DiagnosticCode.UNITS_INCOMPATIBLE,
            "forms.wrong_unit",
            field=field,
            unit=unit.symbol,
            kind=kind.label,
        )
    return u.Quantity.of(value, unit)


def parse_length(text: str, *, field: str, profile: PresentationProfile = PROFILE_SI) -> u.Quantity:
    return parse_quantity(text, QuantityKind.LENGTH, field=field, profile=profile)


def parse_angle(text: str, *, field: str) -> u.Quantity:
    """Ángulo; sin unidad se entiende en grados."""
    return parse_quantity(text, QuantityKind.ANGLE, field=field, default_unit=DEFAULT_ANGLE_UNIT)


def parse_vector(text: str, *, field: str) -> tuple[float, float, float]:
    """Vector adimensional «x; y; z» (no nulo)."""
    if not isinstance(text, str) or not text.strip():
        raise _error(DiagnosticCode.INPUT_OUT_OF_RANGE, "forms.empty_value", field=field)
    parts = [p for p in re.split(r";|\s+", text.strip()) if p]
    if len(parts) != VECTOR_SIZE:
        raise _error(
            DiagnosticCode.INPUT_INVALID_TYPE, "forms.invalid_vector", field=field, text=text
        )
    components: list[float] = []
    for part in parts:
        value, rest = _split(part, field)
        if rest:
            raise _error(
                DiagnosticCode.INPUT_INVALID_TYPE, "forms.invalid_vector", field=field, text=text
            )
        components.append(value)
    if all(c == 0.0 for c in components):
        raise _error(DiagnosticCode.GEOMETRY_INVALID_ORIENTATION, "forms.zero_vector", field=field)
    return (components[0], components[1], components[2])


# --- Nudos ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeInput:
    label: str
    x: u.Quantity
    y: u.Quantity
    z: u.Quantity


def parse_node(
    label: str, x: str, y: str, z: str, *, profile: PresentationProfile = PROFILE_SI
) -> NodeInput:
    """Valida el formulario de nudo (etiqueta libre, coordenadas con unidad opcional)."""
    return NodeInput(
        label.strip(),
        parse_length(x, field="X", profile=profile),
        parse_length(y, field="Y", profile=profile),
        parse_length(z, field="Z", profile=profile),
    )


def create_node(editor: ModelEditor, form: NodeInput) -> Node:
    return editor.add_node(form.x, form.y, form.z, label=form.label)


def move_node_from_text(  # noqa: PLR0913 (id y tres coordenadas del formulario)
    editor: ModelEditor,
    node_id: UUID,
    x: str,
    y: str,
    z: str,
    *,
    profile: PresentationProfile = PROFILE_SI,
) -> Node:
    form = parse_node("", x, y, z, profile=profile)
    return editor.move_node(node_id, form.x, form.y, form.z)


# --- Elecciones y barras --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Choice[T]:
    """Opción de una lista desplegable: texto visible y valor."""

    text: str
    value: T


def node_choices(model: StructuralModel) -> tuple[Choice[UUID], ...]:
    snapshot = model.snapshot()
    return tuple(Choice(n.label or str(n.id)[:8], n.id) for n in snapshot.nodes)


def material_choices(model: StructuralModel) -> tuple[Choice[RevisionRef], ...]:
    """Última revisión de cada material (las anteriores siguen en el modelo, no se ofrecen)."""
    latest: dict[UUID, MaterialRevision] = {}
    for revision in model.snapshot().materials:
        if revision.id not in latest or revision.revision > latest[revision.id].revision:
            latest[revision.id] = revision
    return tuple(Choice(f"{r.name} (rev {r.revision})", r.ref) for r in latest.values())


def section_choices(model: StructuralModel) -> tuple[Choice[RevisionRef], ...]:
    """Última revisión de cada sección."""
    latest: dict[UUID, SectionRevision] = {}
    for revision in model.snapshot().sections:
        if revision.id not in latest or revision.revision > latest[revision.id].revision:
            latest[revision.id] = revision
    return tuple(Choice(f"{r.name} (rev {r.revision})", r.ref) for r in latest.values())


type Vector3 = tuple[float, float, float]


def parse_orientation(reference: str, alpha: str) -> tuple[Vector3 | None, u.Quantity | None]:
    """Orientación opcional: vector de referencia y giro alfa (vacío = por defecto)."""
    direction = parse_vector(reference, field="Referencia") if reference.strip() else None
    angle = parse_angle(alpha, field="Giro alfa") if alpha.strip() else None
    return direction, angle


def set_orientation_from_text(
    editor: ModelEditor, member_id: UUID, reference: str, alpha: str
) -> Member:
    direction, angle = parse_orientation(reference, alpha)
    return editor.set_member_orientation(member_id, reference=direction, alpha=angle)


@dataclass(frozen=True, slots=True)
class MemberInput:
    node_i: UUID
    node_j: UUID
    material: RevisionRef
    section: RevisionRef
    reference: Vector3 | None
    alpha: u.Quantity | None
    label: str


def chosen[T](value: T | None, what: str, field: str) -> T:
    if value is None:
        raise _error(DiagnosticCode.REFERENCE_NOT_FOUND, "forms.no_choice", what=what, field=field)
    return value


def parse_member(  # noqa: PLR0913 (campos del formulario, por nombre)
    *,
    node_i: UUID | None,
    node_j: UUID | None,
    material: RevisionRef | None,
    section: RevisionRef | None,
    reference: str = "",
    alpha: str = "",
    label: str = "",
) -> MemberInput:
    """Valida el formulario de barra: dos nudos distintos, un material y una sección."""
    first = chosen(node_i, "el nudo I", "Nudo I")
    second = chosen(node_j, "el nudo J", "Nudo J")
    if first == second:
        raise _error(DiagnosticCode.GEOMETRY_ZERO_LENGTH, "forms.same_nodes", field="Nudo J")
    direction, angle = parse_orientation(reference, alpha)
    return MemberInput(
        first,
        second,
        chosen(material, "un material", "Material"),
        chosen(section, "una sección", "Sección"),
        direction,
        angle,
        label.strip(),
    )


def create_member(editor: ModelEditor, form: MemberInput) -> Member:
    return editor.add_member(
        form.node_i,
        form.node_j,
        material=form.material,
        section=form.section,
        reference=form.reference,
        alpha=form.alpha,
        label=form.label,
    )


# --- Materiales y secciones (T-030 E2b-2) -----------------------------------------------------

USER_SOURCE: Final = "Introducido por el usuario"

MATERIAL_PRESETS: Final = {
    MaterialFamily.STEEL: ("210000 N/mm²", "81000 N/mm²", "7850 kg/m³"),
    MaterialFamily.ALUMINIUM: ("70000 N/mm²", "27000 N/mm²", "2700 kg/m³"),
}
"""Valores iniciales EDITABLES (constantes físicas habituales; no son una normativa). La madera
no tiene valores sugeridos: depende de la clase resistente y la escribe el usuario."""


@dataclass(frozen=True, slots=True)
class MaterialInput:
    name: str
    family: MaterialFamily
    elastic_modulus: u.Quantity
    shear_modulus: u.Quantity
    density: u.Quantity


def _name(text: str, field: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise _error(DiagnosticCode.INPUT_OUT_OF_RANGE, "forms.empty_value", field=field)
    return text.strip()


def parse_material(  # noqa: PLR0913 (campos del formulario, por nombre)
    *,
    name: str,
    family: MaterialFamily | None,
    elastic_modulus: str,
    shear_modulus: str,
    density: str,
    profile: PresentationProfile = PROFILE_SI,
) -> MaterialInput:
    """Valida el formulario de material; sin unidad, la del perfil (tensión y densidad)."""
    return MaterialInput(
        _name(name, "Nombre"),
        chosen(family, "una familia", "Familia"),
        parse_quantity(elastic_modulus, QuantityKind.STRESS, field="E", profile=profile),
        parse_quantity(shear_modulus, QuantityKind.STRESS, field="G", profile=profile),
        parse_quantity(density, QuantityKind.DENSITY, field="Densidad", profile=profile),
    )


def create_material(editor: ModelEditor, form: MaterialInput) -> MaterialRevision:
    return editor.add_material(
        name=form.name,
        family=form.family,
        source=USER_SOURCE,
        elastic_modulus=form.elastic_modulus,
        shear_modulus=form.shear_modulus,
        density=form.density,
    )


def parse_rectangular_section(
    *, name: str, b: str, h: str, profile: PresentationProfile = PROFILE_SI
) -> tuple[str, u.Quantity, u.Quantity]:
    """Nombre (opcional), ancho b y canto h de una sección rectangular maciza."""
    return (
        name.strip(),
        parse_length(b, field="b", profile=profile),
        parse_length(h, field="h", profile=profile),
    )


def create_rectangular_section(
    editor: ModelEditor, form: tuple[str, u.Quantity, u.Quantity]
) -> SectionRevision:
    name, b, h = form
    return editor.add_rectangular_section(b=b, h=h, name=name)


@dataclass(frozen=True, slots=True)
class ParametricInput:
    name: str
    shape: ParametricShape
    dimensions: dict[str, u.Quantity]


def parametric_fields(shape: ParametricShape) -> tuple[tuple[str, bool], ...]:
    """Dimensiones de la forma en orden canónico y si son opcionales (radios)."""
    return tuple((n, n in OPTIONAL_DIMENSIONS) for n in SHAPE_DIMENSIONS[shape])


def parse_parametric_section(
    *,
    name: str,
    shape: ParametricShape | None,
    values: dict[str, str],
    profile: PresentationProfile = PROFILE_SI,
) -> ParametricInput:
    """Perfil I, RHS, SHS o CHS; los radios (r, ro) pueden quedar vacíos (= 0)."""
    picked = chosen(shape, "una forma", "Forma")
    dimensions: dict[str, u.Quantity] = {}
    for dim, optional in parametric_fields(picked):
        text = values.get(dim, "")
        if optional and not text.strip():
            continue
        dimensions[dim] = parse_length(text, field=dim, profile=profile)
    return ParametricInput(name.strip(), picked, dimensions)


def create_parametric_section(editor: ModelEditor, form: ParametricInput) -> SectionRevision:
    return editor.add_parametric_section(form.shape, form.dimensions, name=form.name)


AXES: Final = ("x", "y", "z")


def move_node_coordinate(
    editor: ModelEditor,
    node_id: UUID,
    axis: str,
    text: str,
    *,
    profile: PresentationProfile = PROFILE_SI,
) -> Node:
    """Cambia UNA coordenada de un nudo desde una celda de tabla.

    Las otras dos se toman EXACTAS del modelo (la tabla las muestra redondeadas: reescribirlas
    desde el texto las alteraría sin que el usuario lo pidiera).
    """
    if axis not in AXES:
        raise _error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "forms.invalid_number", field="eje", text=axis
        )
    new = parse_length(text, field=axis.upper(), profile=profile)
    position = editor.model.node(node_id).position
    current = {"x": position.x, "y": position.y, "z": position.z}
    values = {a: (new if a == axis else u.Quantity.of(current[a], "m")) for a in AXES}
    return editor.move_node(node_id, values["x"], values["y"], values["z"])
