"""Agregado de modelo en memoria (planes T-003, D3, y T-004, D2; ADR-001 §3-§4).

Guarda la revisión actual de cada nudo y barra y TODAS las revisiones de material y
sección. Cada transacción válida que cambia el modelo analítico incrementa
``model_revision``; una operación rechazada no cambia nada. Cambiar solo la etiqueta crea
una revisión de la entidad pero no altera ``model_revision`` (dato de presentación).

Una revisión de material o sección entra en el modelo analítico solo cuando una barra la
referencia: crearla o revisarla no incrementa ``model_revision``; reasignarla a una barra
sí. Revisar un material o una sección no altera las barras, que conservan su par exacto.
``revise_*`` no recibe la revisión base: no detecta ediciones concurrentes (un usuario,
en memoria; se revisará con la persistencia, T-012). Sin deshacer ni persistencia.

Apoyos (plan T-005): como máximo uno por nudo, gestionado por nudo. Crear, cambiar o
retirar un apoyo incrementa ``model_revision``; cambiar solo su etiqueta, no. Retirar un
apoyo es un borrado: su id no se recicla.

Cargas (plan T-006): casos de carga, cargas nodales, cargas uniformes de barra y un único
peso propio en el modelo, en un caso permanente. Crear, cambiar la categoría o retirar un
caso, y crear, revisar o retirar una carga incrementan ``model_revision``; cambiar solo el
nombre o la descripción de un caso, no. Retirar un caso retira sus cargas (una sola vez
+1). Contenido idéntico: sin cambio. El peso propio de cada barra se deriva bajo demanda.
"""

from collections.abc import Sequence
from dataclasses import replace
from enum import Enum, auto
from types import MappingProxyType
from typing import Final, Literal
from uuid import UUID

from veritas.core.connections.end import EndConnection, MemberEnd
from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputTypeError,
    InputValueError,
    Location,
)
from veritas.core.geometry.entities import Member, Node, new_entity_id
from veritas.core.geometry.local_axes import (
    LocalAxes,
    compute_local_axes,
    recompute_local_axes,
    validate_alpha,
)
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.load_case import LoadCase, LoadCategory
from veritas.core.loads.loads import (
    STANDARD_GRAVITY,
    Load,
    LoadBasis,
    LoadResultant,
    MemberUniformLoad,
    NodalLoad,
    SelfWeight,
    SupportSettlement,
    point_contribution,
    self_weight_intensity,
    uniform_contribution,
)
from veritas.core.materials.material import MaterialFamily, MaterialRevision, build_material
from veritas.core.references import RevisionRef, RevisionRegistry, require_ref
from veritas.core.sections.mass import linear_mass
from veritas.core.sections.section import SectionRevision, build_rectangular_section
from veritas.core.supports.support import (
    RestraintKind,
    Restraints,
    Support,
    SupportBasis,
    SupportDof,
    check_restraints,
)
from veritas.core.units import (
    ACCELERATION,
    ANGLE,
    DENSITY,
    FORCE,
    FORCE_PER_LENGTH,
    LENGTH,
    MOMENT,
    STRESS,
    Dimension,
    Quantity,
)
from veritas.core.validation import invalid_type as _invalid_type

type Direction = Vec3 | Sequence[object]


class Keep(Enum):
    """Centinela de ``revise_material``: conservar el valor actual (``None`` lo borra)."""

    KEEP = auto()


KEEP: Final = Keep.KEEP


type _Kind = Literal["nudo", "barra", "apoyo", "caso de carga", "carga"]


def _not_found(kind: _Kind, entity_id: object) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.REFERENCE_NOT_FOUND,
            "model.reference_not_found",
            {"kind": kind, "entity_id": str(entity_id)},
        )
    )


def _alpha_rad(alpha: Quantity | None, location: Location) -> float:
    if alpha is None:
        return 0.0
    if not isinstance(alpha, Quantity):
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "input.invalid_type",
                {"field": "alfa", "expected": "Quantity", "received": type(alpha).__name__},
                location=location,
            )
        )
    return validate_alpha(alpha.require(ANGLE).si_value, location)


class StructuralModel:
    """Nudos y barras con identidad UUID y contador ``model_revision``."""

    def __init__(self) -> None:
        self._nodes: dict[UUID, Node] = {}
        self._members: dict[UUID, Member] = {}
        self._materials: RevisionRegistry[MaterialRevision] = RevisionRegistry("material")
        self._sections: RevisionRegistry[SectionRevision] = RevisionRegistry("sección")
        self._supports: dict[UUID, Support] = {}
        self._support_by_node: dict[UUID, UUID] = {}
        self._load_cases: dict[UUID, LoadCase] = {}
        self._loads: dict[UUID, Load] = {}  # Orden de creación.
        self._self_weight_id: UUID | None = None
        self._model_revision = 0

    @property
    def model_revision(self) -> int:
        """Contador de transacciones válidas que cambian el modelo analítico."""
        return self._model_revision

    @property
    def nodes(self) -> MappingProxyType[UUID, Node]:
        """Revisión actual de cada nudo (vista de solo lectura)."""
        return MappingProxyType(self._nodes)

    @property
    def members(self) -> MappingProxyType[UUID, Member]:
        """Revisión actual de cada barra (vista de solo lectura)."""
        return MappingProxyType(self._members)

    def node(self, node_id: UUID) -> Node:
        """Revisión actual de un nudo o ``REFERENCE_NOT_FOUND``."""
        try:
            return self._nodes[node_id]
        except (KeyError, TypeError):
            raise _not_found("nudo", node_id) from None

    def member(self, member_id: UUID) -> Member:
        """Revisión actual de una barra o ``REFERENCE_NOT_FOUND``."""
        try:
            return self._members[member_id]
        except (KeyError, TypeError):
            raise _not_found("barra", member_id) from None

    def axes(self, member_id: UUID) -> LocalAxes:
        """Base local actual de una barra con su orientación guardada."""
        member = self.member(member_id)
        return recompute_local_axes(
            self._nodes[member.node_i].position,
            self._nodes[member.node_j].position,
            member.orientation,
            location=Location(entity_id=member.id, revision=member.revision),
        )

    def add_node(self, x: Quantity, y: Quantity, z: Quantity, *, label: str = "") -> Node:
        """Crea un nudo (revisión 1) en la posición global dada."""
        node = Node(new_entity_id(), 1, label, Vec3.from_lengths(x, y, z))
        self._nodes[node.id] = node
        self._model_revision += 1
        return node

    def move_node(self, node_id: UUID, x: Quantity, y: Quantity, z: Quantity) -> Node:
        """Mueve un nudo; recalcula la base de sus barras con la referencia guardada.

        Si alguna barra degenera (longitud o referencia), se rechaza todo sin cambios.
        Mover a la misma posición no es un cambio: devuelve la revisión actual.
        """
        current = self.node(node_id)
        position = Vec3.from_lengths(x, y, z)
        if position == current.position:
            return current
        moved = replace(current, revision=current.revision + 1, position=position)

        def resolved(uid: UUID) -> Vec3:
            return position if uid == node_id else self._nodes[uid].position

        for member in self._members.values():
            if node_id in (member.node_i, member.node_j):
                recompute_local_axes(
                    resolved(member.node_i),
                    resolved(member.node_j),
                    member.orientation,
                    location=Location(entity_id=member.id, revision=member.revision),
                )
        self._nodes[node_id] = moved
        self._model_revision += 1
        return moved

    def relabel_node(self, node_id: UUID, label: str) -> Node:
        """Nueva revisión con otra etiqueta; no altera ``model_revision``."""
        current = self.node(node_id)
        if not isinstance(label, str):
            raise TypeError("label debe ser texto")
        if label == current.label:
            return current
        relabeled = replace(current, revision=current.revision + 1, label=label)
        self._nodes[node_id] = relabeled
        return relabeled

    def add_member(  # noqa: PLR0913 (material y sección obligatorios por nombre, GEO-003)
        self,
        node_i: UUID,
        node_j: UUID,
        *,
        material: RevisionRef,
        section: RevisionRef,
        reference: Direction | None = None,
        alpha: Quantity | None = None,
        label: str = "",
    ) -> Member:
        """Crea una barra I -> J con un material, una sección y extremos rígidos (CON-001).

        ``material`` y ``section`` son pares exactos (id, revisión) ya registrados
        (GEO-003). ``reference`` es la dirección local +z (adimensional); sin ella se usa
        +Z con fallback -X para barras verticales. Se guarda la referencia efectiva resuelta.
        """
        member_id = new_entity_id()
        location = Location(entity_id=member_id, revision=1)
        material_ref = self._resolve_material(material, member_id)
        section_ref = self._resolve_section(section, member_id)
        start, end = self.node(node_i), self.node(node_j)
        if node_i == node_j:
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.GEOMETRY_ZERO_LENGTH,
                    "geometry.same_node",
                    {"node": str(node_i)},
                    location=Location(entity_id=member_id, revision=1, field="node_j"),
                )
            )
        axes = compute_local_axes(
            start.position,
            end.position,
            reference=None if reference is None else Vec3.direction(reference),
            alpha=_alpha_rad(alpha, location),
            location=location,
        )
        member = Member(
            member_id, 1, label, node_i, node_j, axes.orientation, material_ref, section_ref
        )
        self._members[member.id] = member
        self._model_revision += 1
        return member

    def set_member_orientation(
        self,
        member_id: UUID,
        *,
        reference: Direction | None = None,
        alpha: Quantity | None = None,
    ) -> Member:
        """Reorientación explícita: nueva revisión de barra (ADR-001 §1, paso 5).

        Sin ``reference`` se vuelve a resolver la referencia por defecto (+Z, fallback -X)
        para la geometría actual. Sin ``alpha`` se conserva el giro actual. Si la orientación
        resultante es idéntica a la guardada, no hay cambio y se devuelve la revisión actual.
        """
        current = self.member(member_id)
        location = Location(entity_id=current.id, revision=current.revision + 1)
        axes = compute_local_axes(
            self._nodes[current.node_i].position,
            self._nodes[current.node_j].position,
            reference=None if reference is None else Vec3.direction(reference),
            alpha=current.orientation.alpha if alpha is None else _alpha_rad(alpha, location),
            location=location,
        )
        if axes.orientation == current.orientation:
            return current
        updated = replace(current, revision=current.revision + 1, orientation=axes.orientation)
        self._members[member_id] = updated
        self._model_revision += 1
        return updated

    def set_member_end(self, member_id: UUID, end: MemberEnd, connection: EndConnection) -> Member:
        """Configuración de un extremo: nueva revisión de barra (CON-002, T-CON-002).

        Solo cambia el extremo indicado; el otro extremo y las demás barras del nudo no se
        modifican. Si la configuración es idéntica a la guardada, no hay cambio y se
        devuelve la revisión actual.
        """
        current = self.member(member_id)
        if not isinstance(end, MemberEnd):
            raise _invalid_type("extremo", "MemberEnd", end)
        if not isinstance(connection, EndConnection):
            raise _invalid_type("unión de extremo", "EndConnection", connection)
        if (current.end_i if end is MemberEnd.I else current.end_j) == connection:
            return current
        revision = current.revision + 1
        updated = (
            replace(current, revision=revision, end_i=connection)
            if end is MemberEnd.I
            else replace(current, revision=revision, end_j=connection)
        )
        self._members[member_id] = updated
        self._model_revision += 1
        return updated

    # --- Materiales y secciones (T-004) -------------------------------------------------

    def material(self, ref: RevisionRef) -> MaterialRevision:
        """Revisión exacta de material o ``REFERENCE_NOT_FOUND``."""
        return self._materials.get(require_ref(ref, "material"))

    def section(self, ref: RevisionRef) -> SectionRevision:
        """Revisión exacta de sección o ``REFERENCE_NOT_FOUND``."""
        return self._sections.get(require_ref(ref, "section"))

    def _resolve_material(self, value: object, member_id: UUID) -> RevisionRef:
        location = Location(entity_id=member_id, field="material")
        return self._materials.get(require_ref(value, "material", location), location).ref

    def _resolve_section(self, value: object, member_id: UUID) -> RevisionRef:
        location = Location(entity_id=member_id, field="section")
        return self._sections.get(require_ref(value, "section", location), location).ref

    def material_history(self, material_id: UUID) -> tuple[MaterialRevision, ...]:
        """Todas las revisiones de un material, de la 1 a la última."""
        return self._materials.history(material_id)

    def section_history(self, section_id: UUID) -> tuple[SectionRevision, ...]:
        """Todas las revisiones de una sección, de la 1 a la última."""
        return self._sections.history(section_id)

    def add_material(  # noqa: PLR0913 (campos de MAT-002, por nombre)
        self,
        *,
        name: str,
        family: MaterialFamily,
        source: str,
        elastic_modulus: Quantity,
        shear_modulus: Quantity,
        density: Quantity,
        poisson_ratio: Quantity | float | None = None,
        standard_edition: str | None = None,
    ) -> MaterialRevision:
        """Crea un material (revisión 1). No altera ``model_revision``."""
        return self._materials.register(
            build_material(
                new_entity_id(),
                1,
                name=name,
                family=family,
                source=source,
                elastic_modulus=elastic_modulus,
                shear_modulus=shear_modulus,
                density=density,
                poisson_ratio=poisson_ratio,
                standard_edition=standard_edition,
            )
        )

    def revise_material(  # noqa: PLR0913 (campos opcionales de MAT-002, por nombre)
        self,
        material_id: UUID,
        *,
        name: str | None = None,
        family: MaterialFamily | None = None,
        source: str | None = None,
        elastic_modulus: Quantity | None = None,
        shear_modulus: Quantity | None = None,
        density: Quantity | None = None,
        poisson_ratio: Quantity | float | Keep | None = KEEP,
        standard_edition: str | Keep | None = KEEP,
    ) -> MaterialRevision:
        """Nueva revisión (max+1); los campos omitidos se copian de la última.

        ``poisson_ratio`` y ``standard_edition`` son opcionales en el material: ``None`` los
        borra y ``KEEP`` (por defecto) los conserva. Las barras conservan su referencia
        anterior. Contenido idéntico a la última revisión: sin cambio. Cambiar la familia:
        ``INPUT_OUT_OF_RANGE``.
        """
        latest = self._materials.latest(material_id)
        revised = build_material(
            material_id,
            latest.revision + 1,
            name=latest.name if name is None else name,
            family=latest.family if family is None else family,
            source=latest.source if source is None else source,
            elastic_modulus=(
                Quantity(latest.elastic_modulus, STRESS)
                if elastic_modulus is None
                else elastic_modulus
            ),
            shear_modulus=(
                Quantity(latest.shear_modulus, STRESS) if shear_modulus is None else shear_modulus
            ),
            density=Quantity(latest.density, DENSITY) if density is None else density,
            poisson_ratio=latest.poisson_ratio if poisson_ratio is KEEP else poisson_ratio,
            standard_edition=(
                latest.standard_edition if standard_edition is KEEP else standard_edition
            ),
        )
        if replace(revised, revision=latest.revision) == latest:
            return latest
        return self._materials.register(revised)

    def register_material(self, revision: MaterialRevision) -> MaterialRevision:
        """Alta de una revisión construida fuera (catálogo/persistencia futuros)."""
        if not isinstance(revision, MaterialRevision):
            raise _invalid_type("material", "MaterialRevision", revision)
        return self._materials.register(revision)

    def add_rectangular_section(
        self, *, b: Quantity, h: Quantity, name: str = ""
    ) -> SectionRevision:
        """Crea una sección rectangular maciza (revisión 1). No altera ``model_revision``."""
        return self._sections.register(
            build_rectangular_section(new_entity_id(), 1, b=b, h=h, name=name)
        )

    def revise_rectangular_section(
        self,
        section_id: UUID,
        *,
        b: Quantity | None = None,
        h: Quantity | None = None,
        name: str | None = None,
    ) -> SectionRevision:
        """Nueva revisión (max+1) con otras dimensiones; las barras conservan la anterior."""
        latest = self._sections.latest(section_id)
        revised = build_rectangular_section(
            section_id,
            latest.revision + 1,
            b=Quantity(latest.parameters.b, LENGTH) if b is None else b,
            h=Quantity(latest.parameters.h, LENGTH) if h is None else h,
            name=latest.name if name is None else name,
        )
        if replace(revised, revision=latest.revision) == latest:
            return latest
        return self._sections.register(revised)

    def register_section(self, revision: SectionRevision) -> SectionRevision:
        """Alta de una revisión construida fuera (catálogo/persistencia futuros)."""
        if not isinstance(revision, SectionRevision):
            raise _invalid_type("section", "SectionRevision", revision)
        return self._sections.register(revision)

    def assign_material(self, member_id: UUID, material: RevisionRef) -> Member:
        """Reasignación explícita: nueva revisión de barra y ``model_revision`` +1."""
        current = self.member(member_id)
        ref = self._resolve_material(material, member_id)
        if ref == current.material:
            return current
        updated = replace(current, revision=current.revision + 1, material=ref)
        self._members[member_id] = updated
        self._model_revision += 1
        return updated

    def assign_section(self, member_id: UUID, section: RevisionRef) -> Member:
        """Reasignación explícita: nueva revisión de barra y ``model_revision`` +1."""
        current = self.member(member_id)
        ref = self._resolve_section(section, member_id)
        if ref == current.section:
            return current
        updated = replace(current, revision=current.revision + 1, section=ref)
        self._members[member_id] = updated
        self._model_revision += 1
        return updated

    def linear_mass(self, member_id: UUID) -> Quantity:
        """Masa lineal ρ·A (kg/m) de una barra con su material y sección asignados."""
        member = self.member(member_id)
        return linear_mass(
            self.material(member.material),
            self.section(member.section),
            Location(entity_id=member.id, revision=member.revision),
        )

    # --- Apoyos (T-005) -----------------------------------------------------------------

    @property
    def supports(self) -> MappingProxyType[UUID, Support]:
        """Revisión actual de cada apoyo por ``Support.id`` (vista de solo lectura)."""
        return MappingProxyType(self._supports)

    def support_at(self, node_id: UUID) -> Support | None:
        """Apoyo actual del nudo, o ``None``; nudo inexistente: ``REFERENCE_NOT_FOUND``."""
        self.node(node_id)
        support_id = self._support_by_node.get(node_id)
        return None if support_id is None else self._supports[support_id]

    def _require_support(self, node_id: UUID) -> Support:
        support = self.support_at(node_id)
        if support is None:
            raise _not_found("apoyo", node_id)
        return support

    def set_support(
        self,
        node_id: UUID,
        restraints: object,
        *,
        basis: SupportBasis = SupportBasis.GLOBAL,
        label: str | Keep = KEEP,
    ) -> Support:
        """Crea o revisa el apoyo del nudo (seis estados en la base ``basis``).

        Sin apoyo previo: revisión 1 con id nuevo. Con apoyo: revisión n+1 del mismo id;
        ``KEEP`` conserva la etiqueta. Contenido idéntico: sin cambio. Solo la etiqueta
        distinta: nueva revisión sin incrementar ``model_revision``.
        """
        self.node(node_id)
        current = self.support_at(node_id)
        if current is None:
            support_id, revision, old_label = new_entity_id(), 1, ""
        else:
            support_id, revision, old_label = current.id, current.revision + 1, current.label
        location = Location(entity_id=support_id, revision=revision)
        checked = check_restraints(restraints, location)
        if not isinstance(basis, SupportBasis):
            raise _invalid_type("basis", "SupportBasis", basis, location)
        new_label = old_label if label is KEEP else label
        candidate = Support(support_id, revision, new_label, node_id, basis, checked)
        if current is not None:
            self._check_settlement_coherence(current, candidate.basis, candidate.restraints)
        if current is None:
            self._supports[support_id] = candidate
            self._support_by_node[node_id] = support_id
            self._model_revision += 1
            return candidate
        if replace(candidate, revision=current.revision) == current:
            return current
        self._supports[support_id] = candidate
        if (candidate.basis, candidate.restraints) != (current.basis, current.restraints):
            self._model_revision += 1
        return candidate

    def relabel_support(self, node_id: UUID, label: str) -> Support:
        """Nueva revisión con otra etiqueta; no altera ``model_revision``."""
        current = self._require_support(node_id)
        if not isinstance(label, str):
            raise _invalid_type("label", "texto", label)
        if label == current.label:
            return current
        relabeled = replace(current, revision=current.revision + 1, label=label)
        self._supports[current.id] = relabeled
        return relabeled

    def remove_support(self, node_id: UUID) -> Support:
        """Retira el apoyo del nudo (``model_revision`` +1) y devuelve su última revisión."""
        current = self._require_support(node_id)
        if self._settlements_of(current.id):
            raise self._settlement_conflict(
                current, "retirada del apoyo", self._settlements_of(current.id)[0]
            )
        del self._supports[current.id]
        del self._support_by_node[node_id]
        self._model_revision += 1
        return current

    def _settlements_of(self, support_id: UUID) -> list[SupportSettlement]:
        return [
            load
            for load in self._loads.values()
            if isinstance(load, SupportSettlement) and load.support_id == support_id
        ]

    def _settlement_conflict(
        self, support: Support, detail: str, settlement: SupportSettlement
    ) -> InputValueError:
        return InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "load.settlement_support_conflict",
                {"detail": detail, "case": self.load_case(settlement.case_id).name},
                location=Location(entity_id=support.id, revision=support.revision),
            )
        )

    def _check_settlement_coherence(
        self, current: Support, basis: SupportBasis, restraints: Restraints
    ) -> None:
        """Rechaza (sin mutar) un cambio del apoyo incoherente con sus asientos (T-016, D2-A)."""
        for settlement in self._settlements_of(current.id):
            if basis != current.basis:
                raise self._settlement_conflict(current, "cambio de base", settlement)
            values = (*settlement.translation.components(), *settlement.rotation.components())
            for dof, value in zip(SupportDof, values, strict=True):
                if value != 0.0 and restraints[dof.value].kind is not RestraintKind.FIXED:
                    raise self._settlement_conflict(
                        current, f"GDL {dof.label} deja de ser FIXED", settlement
                    )

    # --- Casos de carga y cargas (T-006) ------------------------------------------------

    @property
    def load_cases(self) -> MappingProxyType[UUID, LoadCase]:
        """Revisión actual de cada caso de carga (vista de solo lectura)."""
        return MappingProxyType(self._load_cases)

    def load_case(self, case_id: UUID) -> LoadCase:
        """Revisión actual de un caso o ``REFERENCE_NOT_FOUND``."""
        try:
            return self._load_cases[case_id]
        except (KeyError, TypeError):
            raise _not_found("caso de carga", case_id) from None

    def add_load_case(
        self, name: str, category: LoadCategory, *, description: str = ""
    ) -> LoadCase:
        """Crea un caso de carga (revisión 1); ``model_revision`` +1."""
        case = LoadCase(new_entity_id(), 1, name, category, description)
        self._load_cases[case.id] = case
        self._model_revision += 1
        return case

    def revise_load_case(
        self,
        case_id: UUID,
        *,
        name: str | Keep = KEEP,
        category: LoadCategory | Keep = KEEP,
        description: str | Keep = KEEP,
    ) -> LoadCase:
        """Nueva revisión del caso; solo un cambio de categoría incrementa ``model_revision``.

        Un caso con el peso propio no puede dejar de ser permanente.
        """
        current = self.load_case(case_id)
        candidate = LoadCase(
            current.id,
            current.revision + 1,
            current.name if name is KEEP else name,
            current.category if category is KEEP else category,
            current.description if description is KEEP else description,
        )
        if replace(candidate, revision=current.revision) == current:
            return current
        if candidate.category is not LoadCategory.PERMANENT and self._self_weight_case() == case_id:
            raise _self_weight_category(candidate)
        self._load_cases[case_id] = candidate
        if candidate.category is not current.category:
            self._model_revision += 1
        return candidate

    def remove_load_case(self, case_id: UUID) -> tuple[LoadCase, tuple[Load, ...]]:
        """Retira el caso y todas sus cargas; ``model_revision`` +1 una sola vez."""
        case = self.load_case(case_id)
        removed = self.loads_in_case(case_id)
        for load in removed:
            del self._loads[load.id]
            if load.id == self._self_weight_id:
                self._self_weight_id = None
        del self._load_cases[case_id]
        self._model_revision += 1
        return case, removed

    @property
    def loads(self) -> MappingProxyType[UUID, Load]:
        """Revisión actual de cada carga, en orden de creación (vista de solo lectura)."""
        return MappingProxyType(self._loads)

    def loads_in_case(self, case_id: UUID) -> tuple[Load, ...]:
        """Cargas del caso en orden de creación."""
        self.load_case(case_id)
        return tuple(load for load in self._loads.values() if load.case_id == case_id)

    def add_nodal_load(  # noqa: PLR0913 (seis componentes opcionales por nombre)
        self,
        case_id: UUID,
        node_id: UUID,
        *,
        fx: Quantity | None = None,
        fy: Quantity | None = None,
        fz: Quantity | None = None,
        mx: Quantity | None = None,
        my: Quantity | None = None,
        mz: Quantity | None = None,
    ) -> NodalLoad:
        """Fuerza y momento globales en un nudo (componente omitida = 0); +1."""
        self.load_case(case_id)
        self.node(node_id)
        load_id = new_entity_id()
        location = Location(entity_id=load_id, revision=1)
        force = _vector({"fx": fx, "fy": fy, "fz": fz}, FORCE, location)
        moment = _vector({"mx": mx, "my": my, "mz": mz}, MOMENT, location)
        load = NodalLoad(load_id, 1, case_id, node_id, force, moment)
        self._loads[load.id] = load
        self._model_revision += 1
        return load

    def add_member_uniform_load(  # noqa: PLR0913 (tres componentes opcionales por nombre)
        self,
        case_id: UUID,
        member_id: UUID,
        *,
        basis: LoadBasis = LoadBasis.GLOBAL,
        qx: Quantity | None = None,
        qy: Quantity | None = None,
        qz: Quantity | None = None,
    ) -> MemberUniformLoad:
        """Carga uniforme en toda la barra, por metro de longitud real; +1."""
        self.load_case(case_id)
        self.member(member_id)
        load_id = new_entity_id()
        location = Location(entity_id=load_id, revision=1)
        intensity = _vector({"qx": qx, "qy": qy, "qz": qz}, FORCE_PER_LENGTH, location)
        load = MemberUniformLoad(load_id, 1, case_id, member_id, basis, intensity)
        self._loads[load.id] = load
        self._model_revision += 1
        return load

    def add_support_settlement(  # noqa: PLR0913 (seis componentes opcionales por nombre)
        self,
        case_id: UUID,
        node_id: UUID,
        *,
        ux: Quantity | None = None,
        uy: Quantity | None = None,
        uz: Quantity | None = None,
        rx: Quantity | None = None,
        ry: Quantity | None = None,
        rz: Quantity | None = None,
    ) -> SupportSettlement:
        """Asiento impuesto en la base del apoyo del nudo (componente omitida = 0); +1.

        Cada componente no nula exige el GDL FIXED en la revisión actual del apoyo; como
        máximo uno por (caso, apoyo).
        """
        case = self.load_case(case_id)
        support = self._require_support(node_id)
        load_id = new_entity_id()
        location = Location(entity_id=load_id, revision=1)
        translation = _vector({"ux": ux, "uy": uy, "uz": uz}, LENGTH, location)
        rotation = _vector({"rx": rx, "ry": ry, "rz": rz}, ANGLE, location)
        load = SupportSettlement(load_id, 1, case_id, support.id, translation, rotation)
        values = (*translation.components(), *rotation.components())
        for dof, value in zip(SupportDof, values, strict=True):
            state = support.restraint(dof).kind
            if value != 0.0 and state is not RestraintKind.FIXED:
                raise InputValueError(
                    Diagnostic.create(
                        DiagnosticCode.INPUT_OUT_OF_RANGE,
                        "load.settlement_dof_not_fixed",
                        {"dof": dof.label, "state": str(state)},
                        location=location,
                    )
                )
        if any(s.case_id == case_id for s in self._settlements_of(support.id)):
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.INPUT_OUT_OF_RANGE,
                    "load.settlement_duplicate",
                    {"case": case.name},
                    location=location,
                )
            )
        self._loads[load.id] = load
        self._model_revision += 1
        return load

    @property
    def self_weight(self) -> SelfWeight | None:
        """Peso propio del modelo, o ``None``."""
        if self._self_weight_id is None:
            return None
        load = self._loads[self._self_weight_id]
        if not isinstance(load, SelfWeight):  # pragma: no cover (invariante interna)
            raise TypeError("el peso propio registrado no es SelfWeight")
        return load

    def _self_weight_case(self) -> UUID | None:
        current = self.self_weight
        return None if current is None else current.case_id

    def set_self_weight(self, case_id: UUID, *, gravity: Quantity = STANDARD_GRAVITY) -> SelfWeight:
        """Define o revisa el peso propio (uno por modelo, en un caso permanente); +1.

        Sin peso propio: revisión 1 con id nuevo. En el mismo caso: revisión n+1; contenido
        idéntico: sin cambio. En otro caso: ``INPUT_OUT_OF_RANGE``.
        """
        case = self.load_case(case_id)
        current = self.self_weight
        if current is None:
            load_id, revision = new_entity_id(), 1
        else:
            load_id, revision = current.id, current.revision + 1
        location = Location(entity_id=load_id, revision=revision, field="gravity")
        if not isinstance(gravity, Quantity):
            raise _invalid_type("gravity", "Quantity", gravity, location)
        g = gravity.require(ACCELERATION).si_value
        if current is not None and current.case_id != case_id:
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.INPUT_OUT_OF_RANGE,
                    "load.self_weight_duplicate",
                    {"case_id": str(current.case_id)},
                    location=Location(entity_id=load_id, revision=revision),
                )
            )
        if case.category is not LoadCategory.PERMANENT:
            raise _self_weight_category(case)
        candidate = SelfWeight(load_id, revision, case_id, g)
        if current is not None and replace(candidate, revision=current.revision) == current:
            return current
        self._loads[load_id] = candidate
        self._self_weight_id = load_id
        self._model_revision += 1
        return candidate

    def self_weight_load(self, member_id: UUID) -> tuple[Quantity, Quantity, Quantity]:
        """Peso propio de la barra en base global (N/m): (+0, +0, -ρ·A·g)."""
        member = self.member(member_id)
        current = self.self_weight
        if current is None:
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.REFERENCE_NOT_FOUND,
                    "load.self_weight_missing",
                    {},
                    location=Location(entity_id=member.id, revision=member.revision),
                )
            )
        q = self_weight_intensity(
            self.linear_mass(member_id), Quantity(current.gravity, ACCELERATION)
        )
        zero = Quantity(0.0, FORCE_PER_LENGTH)
        return (zero, zero, -q)

    def remove_load(self, load_id: UUID) -> Load:
        """Retira una carga (nodal, de barra o peso propio); ``model_revision`` +1."""
        try:
            load = self._loads.pop(load_id)
        except (KeyError, TypeError):
            raise _not_found("carga", load_id) from None
        if load_id == self._self_weight_id:
            self._self_weight_id = None
        self._model_revision += 1
        return load

    def _member_load_global(self, load: MemberUniformLoad) -> Vec3:
        if load.basis is LoadBasis.GLOBAL:
            return load.intensity
        return self.axes(load.member_id).to_global(load.intensity)

    def _uniform_part(self, member_id: UUID, q_global: Vec3) -> tuple[Vec3, Vec3]:
        member = self._members[member_id]
        return uniform_contribution(
            self._nodes[member.node_i].position, self._nodes[member.node_j].position, q_global
        )

    def case_resultant(self, case_id: UUID) -> LoadResultant:
        """ΣF y ΣM respecto al origen global de las acciones prescritas del caso.

        Comprobación de la entrada, no un resultado de análisis. Una barra cuyo peso
        propio no se pueda derivar hace fallar la suma; nunca se omite.
        """
        loads = self.loads_in_case(case_id)
        force = moment = Vec3(0.0, 0.0, 0.0)
        for load in loads:
            parts: list[tuple[Vec3, Vec3]]
            if isinstance(load, NodalLoad):
                position = self._nodes[load.node_id].position
                parts = [point_contribution(position, load.force, load.moment)]
            elif isinstance(load, MemberUniformLoad):
                parts = [self._uniform_part(load.member_id, self._member_load_global(load))]
            elif isinstance(load, SupportSettlement):
                parts = []  # Sin fuerza prescrita: cuenta en load_count, no en ΣF ni ΣM.
            elif isinstance(load, SelfWeight):
                parts = [
                    self._uniform_part(
                        member_id, Vec3(0.0, 0.0, self.self_weight_load(member_id)[2].si_value)
                    )
                    for member_id in self._members
                ]
            else:  # pragma: no cover (unión Load cerrada)
                raise TypeError(f"tipo de carga no contemplado: {type(load).__name__}")
            for f, m in parts:
                force, moment = force + f, moment + m
        return LoadResultant.from_vectors(force, moment, len(loads))


def _vector(values: dict[str, Quantity | None], dimension: Dimension, location: Location) -> Vec3:
    components: list[float] = []
    for field, value in values.items():
        if value is None:
            components.append(0.0)
            continue
        if not isinstance(value, Quantity):
            raise _invalid_type(field, "Quantity", value, location)
        components.append(value.require(dimension).si_value)
    return Vec3(*components)


def _self_weight_category(case: LoadCase) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "load.self_weight_category",
            {"case": case.name, "category": str(case.category)},
            location=Location(entity_id=case.id, revision=case.revision, field="category"),
        )
    )
