"""Referencias analíticas T-FEM-001…004 (plan T-009, D1-A), T-CON-REF-01…04 (T-010, D6) y
T-FEM3D-01…05 con el contraste cruzado espacial-plano (T-018, D1 y D8).

Cada caso plano construye su modelo XZ, lo analiza, normaliza los resultados (T-008) y los
compara con fórmulas cerradas de Euler-Bernoulli derivadas por cuerpo libre en el plan.
Las fórmulas planas usan E, b y h del caso, no las propiedades de sección del código. Las
espaciales toman A, Iy, Iz y J de ``rectangular_properties``, la función que alimenta el
modelo (J de Saint-Venant no tiene forma cerrada; plan T-018, T-FEM3D-03), y E y G del caso.

Criterio por magnitud (revisión M1 del plan): |calculado − referencia| ≤ tol·max(|ref|,
S_dim), con S_dim el mayor |ref| de la misma magnitud dentro del caso, o la escala declarada por el
caso si es mayor (T-010, revisión M4: un momento de referencia nulo en un extremo liberado
se compara con P·L o qL²/8, nunca con 0 exacto). «Coincide» significa solo eso: no valida
la aplicación ni un modelo de usuario.

Los tests de referencia (``tests/test_fem_reference.py``) no usan este módulo; un test aparte
comprueba que sus referencias coinciden con los literales del plan.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from veritas.core import units as u
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticError, ParamValue
from veritas.core.geometry.model import StructuralModel
from veritas.core.loads.load_case import LoadCategory
from veritas.core.loads.loads import LoadBasis
from veritas.core.materials.material import MaterialFamily
from veritas.core.presentation import QuantityKind
from veritas.core.sections.rectangular import rectangular_properties
from veritas.core.supports.support import (
    FIXED,
    FREE,
    Restraints,
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
    roller_restraints,
)
from veritas.fem import (
    AnalysisConfiguration,
    AnalysisRun,
    ComponentStatus,
    PlanarResults,
    RunStatus,
    Vector6,
    analyze_planar_xz,
    normalize_planar_xz,
)
from veritas.fem.spatial import (
    SpatialAnalysisConfiguration,
    SpatialAnalysisRun,
    SpatialResults,
    analyze_spatial,
    normalize_spatial,
)

E: Final = 210e9  # Pa
G: Final = 81e9  # Pa
B: Final = 0.2  # m, según y local
H: Final = 0.4  # m, según z local
EI: Final = E * B * H**3 / 12  # 2,24e8 N·m²
EA: Final = E * B * H  # 1,68e10 N
P: Final = 10_000.0  # N, hacia −Z
W: Final = 10_000.0  # N/m, hacia −Z
SPAN: Final = 4.0  # m, T-FEM-001, -003 y -004
CANTILEVER: Final = 2.0  # m, T-FEM-002
SPRING_K: Final = 1e6  # N/m, T-FEM-003
ANGLE_DEG: Final = 30.0  # plano del rodillo y del resorte inclinados

TOL_EXACT: Final = 1e-9
TOL_PENALTY: Final = 1e-6

TRUSS_RISE: Final = 1.5  # m, T-CON-REF-04 (barras inclinadas de 2,5 m)
SPRING_RATIO: Final = 3.0  # k·L/(EI) de T-CON-REF-01 b y T-CON-REF-02

CASES: Final = (
    "T-FEM-001",
    "T-FEM-002",
    "T-FEM-003",
    "T-FEM-004",
    "T-CON-REF-01",
    "T-CON-REF-02",
    "T-CON-REF-03",
    "T-CON-REF-04",
)


@dataclass(frozen=True, slots=True)
class Text:
    """Texto por clave de ``messages_es`` con sus parámetros (NFR-009; plan T-009, P5)."""

    key: str
    params: tuple[tuple[str, ParamValue], ...] = ()

    @classmethod
    def of(cls, key: str, **params: ParamValue) -> "Text":
        return cls(key, tuple(params.items()))


HYPOTHESES: Final = (
    Text.of("benchmark.hypothesis_formulation"),
    Text.of("benchmark.hypothesis_data", modulus_gpa=E / 1e9, b_mm=B * 1000, h_mm=H * 1000),
)

_SI_UNITS: Final = {
    QuantityKind.DISPLACEMENT: "m",
    QuantityKind.ANGLE: "rad",
    QuantityKind.FORCE: "N",
    QuantityKind.MOMENT: "N*m",
}


@dataclass(frozen=True, slots=True)
class ReferenceCheck:
    """Una magnitud contrastada (SI)."""

    quantity: str
    formula: str
    kind: QuantityKind
    reference: float
    computed: float | None
    tolerance: float
    scale: float

    @property
    def relative_error(self) -> float | None:
        if self.computed is None:
            return None
        return abs(self.computed - self.reference) / self.scale

    @property
    def matches(self) -> bool:
        error = self.relative_error
        return error is not None and error <= self.tolerance

    def quantity_value(self, value: float) -> u.Quantity:
        return u.Quantity.of(value, _SI_UNITS[self.kind])


Row = tuple[str, str, QuantityKind, float, float | None]


def evaluate(
    rows: Sequence[Row],
    tolerance: float,
    scales: Mapping[QuantityKind, float] | None = None,
) -> tuple[ReferenceCheck, ...]:
    """Contrasta filas (magnitud, fórmula, tipo, referencia, calculado) con S_dim por tipo.

    ``scales`` declara una escala mínima por tipo (T-010, revisión M4).
    """
    largest: dict[QuantityKind, float] = dict(scales or {})
    for _name, _formula, kind, reference, _computed in rows:
        largest[kind] = max(largest.get(kind, 0.0), abs(reference))
    checks = []
    for name, formula, kind, reference, computed in rows:
        scale = max(abs(reference), largest[kind])
        if scale == 0.0:
            raise ValueError(f"magnitud {kind} sin escala en el caso")
        checks.append(ReferenceCheck(name, formula, kind, reference, computed, tolerance, scale))
    return tuple(checks)


@dataclass(frozen=True, slots=True)
class ReferenceOutcome:
    """Resultado del contraste de un caso. Sin resultados, ``checks`` queda vacío."""

    case_id: str
    title: Text
    description: Text
    labels: dict[UUID, str]
    run: AnalysisRun
    results: PlanarResults | None
    failure: DiagnosticError | None
    checks: tuple[ReferenceCheck, ...]
    notes: tuple[Text, ...]

    @property
    def matches(self) -> bool:
        return (
            self.results is not None
            and bool(self.checks)
            and all(check.matches for check in self.checks)
        )


class _Case:
    """Modelo de referencia con acceso a resultados por etiqueta."""

    def __init__(self, category: LoadCategory = LoadCategory.VARIABLE) -> None:
        self.model = StructuralModel()
        self.material = self.model.add_material(
            name="Acero de referencia",
            family=MaterialFamily.STEEL,
            source="plan T-009",
            elastic_modulus=u.Quantity.of(E, "Pa"),
            shear_modulus=u.Quantity.of(G, "Pa"),
            density=u.Quantity.of(7850, "kg/m³"),
        )
        self.section = self.model.add_rectangular_section(
            b=u.Quantity.of(B, "m"), h=u.Quantity.of(H, "m"), name="R 200×400"
        )
        self.load_case = self.model.add_load_case("Q", category)
        self.ids: dict[str, UUID] = {}
        self.results: PlanarResults | SpatialResults | None = None

    def node(self, label: str, x: float, z: float = 0.0, y: float = 0.0) -> UUID:
        m = u.Quantity.of
        node = self.model.add_node(m(x, "m"), m(y, "m"), m(z, "m"), label=label)
        self.ids[label] = node.id
        return node.id

    def member(self, label: str, i: str, j: str, alpha: float | None = None) -> UUID:
        member = self.model.add_member(
            self.ids[i],
            self.ids[j],
            material=self.material.ref,
            section=self.section.ref,
            alpha=None if alpha is None else u.Quantity.of(alpha, "rad"),
            label=label,
        )
        self.ids[label] = member.id
        return member.id

    def support(
        self,
        node: str,
        restraints: Restraints,
        label: str,
        basis: SupportBasis = SupportBasis.GLOBAL,
    ) -> None:
        self.model.set_support(self.ids[node], restraints, basis=basis, label=label)

    @property
    def labels(self) -> dict[UUID, str]:
        model = self.model
        labels = {node.id: node.label for node in model.nodes.values()}
        labels |= {member.id: member.label for member in model.members.values()}
        labels |= {support.id: support.label for support in model.supports.values()}
        return labels

    def _res(self) -> PlanarResults | SpatialResults:
        assert self.results is not None  # Solo se consulta tras normalizar.
        return self.results

    def disp(self, node: str, index: int) -> float | None:
        item = next(n for n in self._res().nodes if n.node_id == self.ids[node])
        return item.displacement.components[index].value

    def reaction(self, node: str, index: int, *, support_basis: bool = False) -> float | None:
        item = next(r for r in self._res().reactions if r.node_id == self.ids[node])
        vector = item.support_basis if support_basis else item.global_
        return None if vector is None else vector.components[index].value

    def reaction_status(self, node: str, index: int) -> ComponentStatus | None:
        item = next(r for r in self._res().reactions if r.node_id == self.ids[node])
        vector = item.support_basis
        return None if vector is None else vector.components[index].status

    def cut(self, member: str, index: int, *, at_end: bool) -> float | None:
        item = next(m for m in self._res().members if m.member_id == self.ids[member])
        vector = item.section_end if at_end else item.section_start
        return vector.components[index].value


# Índices ADR-001 de un Vector6.
UX, UZ, RY = 0, 2, 4
FX, FZ, MY = 0, 2, 4
N, VZ = 0, 2

DISP, ROT, FORCE, MOMENT = (
    QuantityKind.DISPLACEMENT,
    QuantityKind.ANGLE,
    QuantityKind.FORCE,
    QuantityKind.MOMENT,
)

_SIN = math.sin(math.radians(ANGLE_DEG))
_COS = math.cos(math.radians(ANGLE_DEG))
_TAN = math.tan(math.radians(ANGLE_DEG))


def _inclined() -> SupportBasis:
    return SupportBasis.inclined((_COS, 0.0, _SIN), (-_SIN, 0.0, _COS))


def _two_span(case: _Case, support3: Restraints, label3: str, basis3: SupportBasis) -> None:
    case.node("N1", 0.0)
    case.node("N2", SPAN / 2)
    case.node("N3", SPAN)
    case.member("B1", "N1", "N2")
    case.member("B2", "N2", "N3")
    case.support("N1", pinned_restraints(), "Articulación")
    case.support("N3", support3, label3, basis3)
    case.model.add_nodal_load(case.load_case.id, case.ids["N2"], fz=u.Quantity.of(-P, "N"))


@dataclass(frozen=True, slots=True)
class _Spec:
    case_id: str
    title: Text
    description: Text
    tolerance: float
    build: Callable[[_Case], None]
    rows: Callable[[_Case], list[Row]]
    notes: Callable[[_Case], list[Text]]
    scales: Mapping[QuantityKind, float] | None = None
    category: LoadCategory = LoadCategory.VARIABLE


def _no_notes(_case: _Case) -> list[Text]:
    return []


# --- T-FEM-001 ---------------------------------------------------------------------------


def _build_001(case: _Case) -> None:
    _two_span(case, roller_restraints(), "Rodillo", SupportBasis.GLOBAL)


def _rows_001(c: _Case) -> list[Row]:
    bend = P * SPAN**2 / (16 * EI)
    return [
        ("R_Z N1", "+P/2", FORCE, P / 2, c.reaction("N1", FZ)),
        ("R_Z N3", "+P/2", FORCE, P / 2, c.reaction("N3", FZ)),
        ("R_X N1", "0", FORCE, 0.0, c.reaction("N1", FX)),
        ("My(L−) B1", "−PL/4", MOMENT, -P * SPAN / 4, c.cut("B1", MY, at_end=True)),
        ("My(0+) B2", "−PL/4", MOMENT, -P * SPAN / 4, c.cut("B2", MY, at_end=False)),
        ("Vz(L−) B1", "−P/2", FORCE, -P / 2, c.cut("B1", VZ, at_end=True)),
        ("Vz(0+) B2", "+P/2", FORCE, P / 2, c.cut("B2", VZ, at_end=False)),
        ("Uz N2", "−PL³/(48EI)", DISP, -P * SPAN**3 / (48 * EI), c.disp("N2", UZ)),
        ("Ry N1", "+PL²/(16EI)", ROT, bend, c.disp("N1", RY)),
        ("Ry N3", "−PL²/(16EI)", ROT, -bend, c.disp("N3", RY)),
        ("Ry N2", "0", ROT, 0.0, c.disp("N2", RY)),
    ]


# --- T-FEM-002 ---------------------------------------------------------------------------


def _build_002(case: _Case) -> None:
    case.node("N1", 0.0)
    case.node("N2", CANTILEVER / 2)
    case.node("N3", CANTILEVER)
    case.member("B1", "N1", "N2")
    case.member("B2", "N2", "N3")
    case.support("N1", fixed_restraints(), "Empotramiento")
    for member in ("B1", "B2"):
        case.model.add_member_uniform_load(
            case.load_case.id, case.ids[member], qz=u.Quantity.of(-W, "N/m")
        )


def _rows_002(c: _Case) -> list[Row]:
    length, a = CANTILEVER, CANTILEVER / 2
    return [
        ("R_Z N1", "+wL", FORCE, W * length, c.reaction("N1", FZ)),
        ("R_My N1", "−wL²/2", MOMENT, -W * length**2 / 2, c.reaction("N1", MY)),
        ("My(0+) B1", "+wL²/2", MOMENT, W * length**2 / 2, c.cut("B1", MY, at_end=False)),
        ("Vz(0+) B1", "−wL", FORCE, -W * length, c.cut("B1", VZ, at_end=False)),
        (
            "My(0+) B2",
            "+w(L−a)²/2",
            MOMENT,
            W * (length - a) ** 2 / 2,
            c.cut("B2", MY, at_end=False),
        ),
        ("My(L−) B2", "0", MOMENT, 0.0, c.cut("B2", MY, at_end=True)),
        (
            "Uz N2",
            "−wa²(6L²−4La+a²)/(24EI)",
            DISP,
            -W * a**2 * (6 * length**2 - 4 * length * a + a**2) / (24 * EI),
            c.disp("N2", UZ),
        ),
        ("Uz N3", "−wL⁴/(8EI)", DISP, -W * length**4 / (8 * EI), c.disp("N3", UZ)),
        ("Ry N3", "+wL³/(6EI)", ROT, W * length**3 / (6 * EI), c.disp("N3", RY)),
    ]


# --- T-FEM-003 a y b ---------------------------------------------------------------------


def _spring() -> Restraints:
    return build_restraints(springs={SupportDof.UZ: u.Quantity.of(SPRING_K, "N/m")})


def _build_003a(case: _Case) -> None:
    _two_span(case, _spring(), "Resorte Uz", SupportBasis.GLOBAL)


def _rows_003a(c: _Case) -> list[Row]:
    u3 = -P / (2 * SPRING_K)
    return [
        ("R_Z N1", "+P/2", FORCE, P / 2, c.reaction("N1", FZ)),
        ("R_Z N3", "+P/2", FORCE, P / 2, c.reaction("N3", FZ)),
        ("Uz N3", "−P/(2k)", DISP, u3, c.disp("N3", UZ)),
        (
            "Uz N2",
            "−PL³/(48EI) − P/(4k)",
            DISP,
            -P * SPAN**3 / (48 * EI) + u3 / 2,
            c.disp("N2", UZ),
        ),
        (
            "Ry N1",
            "+PL²/(16EI) + P/(2kL)",
            ROT,
            P * SPAN**2 / (16 * EI) - u3 / SPAN,
            c.disp("N1", RY),
        ),
    ]


def _build_003b(case: _Case) -> None:
    _two_span(case, _spring(), "Resorte Uz′ 30°", _inclined())


def _along_ez(c: _Case, node: str) -> float | None:
    ux, uz = c.disp(node, UX), c.disp(node, UZ)
    return None if ux is None or uz is None else -_SIN * ux + _COS * uz


def _rows_003b(c: _Case) -> list[Row]:
    rn = P / (2 * _COS)
    return [
        ("R_z′ N3", "P/(2·cos30°)", FORCE, rn, c.reaction("N3", UZ, support_basis=True)),
        ("R_X N1", "+(P/2)·tan30°", FORCE, P / 2 * _TAN, c.reaction("N1", FX)),
        ("R_Z N3", "+P/2", FORCE, P / 2, c.reaction("N3", FZ)),
        ("u·ez′ N3", "−R_z′/k", DISP, -rn / SPRING_K, _along_ez(c, "N3")),
    ]


def _notes_inclined(c: _Case) -> list[Text]:
    status = c.reaction_status("N3", UX)
    if status is ComponentStatus.NOT_APPLICABLE:
        return [Text.of("benchmark.note_in_plane_not_applicable")]
    return [Text.of("benchmark.note_in_plane_status", status=str(status))]


# --- T-FEM-004 ---------------------------------------------------------------------------


def _build_004(case: _Case) -> None:
    _two_span(case, roller_restraints(), "Rodillo 30°", _inclined())


def _rows_004(c: _Case) -> list[Row]:
    rn = P / (2 * _COS)
    axial = -(P / 2) * _TAN
    ux3 = axial * SPAN / EA
    uz3 = _TAN * ux3
    rx, rz = c.reaction("N3", FX), c.reaction("N3", FZ)
    in_plane = None if rx is None or rz is None else rx * _COS + rz * _SIN
    return [
        ("R_z′ N3", "P/(2·cos30°)", FORCE, rn, c.reaction("N3", UZ, support_basis=True)),
        ("R global N3 · ex′", "0", FORCE, 0.0, in_plane),
        ("R_X N1", "+(P/2)·tan30°", FORCE, -axial, c.reaction("N1", FX)),
        ("R_Z N1", "+P/2", FORCE, P / 2, c.reaction("N1", FZ)),
        ("N(0+) B1", "−(P/2)·tan30°", FORCE, axial, c.cut("B1", N, at_end=False)),
        ("Ux N3", "N·L/(EA)", DISP, ux3, c.disp("N3", UX)),
        ("Uz N3", "tan30°·Ux", DISP, uz3, c.disp("N3", UZ)),
        (
            "Uz N2",
            "−PL³/(48EI) + Uz(N3)/2",
            DISP,
            -P * SPAN**3 / (48 * EI) + uz3 / 2,
            c.disp("N2", UZ),
        ),
    ]


# --- T-CON-REF (T-010, D6) -----------------------------------------------------------
# Giro del plano liberado (RELEASED) o con resorte k en el extremo indicado; k = 3·EI/L.

_K: Final = SPRING_RATIO * EI / SPAN
_RELEASED: Final = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)
_SPRING: Final = EndConnection.semi_rigid(Dof.RY, u.Quantity(_K, Dof.RY.stiffness_dimension))
_SLIDER: Final = build_restraints([SupportDof.UZ])


def _end(case: _Case, member: str, end: MemberEnd, connection: EndConnection) -> None:
    case.model.set_member_end(case.ids[member], end, connection)


def _end_moment(c: _Case, member: str, end: MemberEnd) -> float | None:
    item = next(m for m in c._res().members if m.member_id == c.ids[member])
    return (item.p_i if end is MemberEnd.I else item.p_j).components[MY].value


def _propped(case: _Case, connection: EndConnection) -> None:
    case.node("N1", 0.0)
    case.node("N2", SPAN)
    case.member("B1", "N1", "N2")
    case.support("N1", fixed_restraints(), "Empotramiento")
    case.support("N2", _SLIDER, "Deslizadera Uz")
    case.model.add_member_uniform_load(
        case.load_case.id, case.ids["B1"], qz=u.Quantity.of(-W, "N/m")
    )
    _end(case, "B1", MemberEnd.I, connection)


def _rows_ref01(c: _Case, m_i: float) -> list[Row]:
    return [
        ("R_Z N1", "wL/2 + M_I/L", FORCE, W * SPAN / 2 + m_i / SPAN, c.reaction("N1", FZ)),
        ("R_Z N2", "wL/2 − M_I/L", FORCE, W * SPAN / 2 - m_i / SPAN, c.reaction("N2", FZ)),
        ("R_My N1", "−M_I", MOMENT, -m_i, c.reaction("N1", MY)),
        ("|M| extremo I B1", "M_I", MOMENT, m_i, _abs(_end_moment(c, "B1", MemberEnd.I))),
        (
            "Ry N2",
            "−wL³/(24EI) + M_I·L/(6EI)",
            ROT,
            -W * SPAN**3 / (24 * EI) + m_i * SPAN / (6 * EI),
            c.disp("N2", RY),
        ),
    ]


def _abs(value: float | None) -> float | None:
    return None if value is None else abs(value)


def _rows_ref01a(c: _Case) -> list[Row]:
    return _rows_ref01(c, 0.0)


def _rows_ref01b(c: _Case) -> list[Row]:
    return _rows_ref01(c, (W * SPAN**2 / 8) / (1 + 3 / SPRING_RATIO))


def _build_ref02(case: _Case) -> None:
    case.node("N1", 0.0)
    case.node("N2", SPAN)
    case.member("B1", "N1", "N2")
    case.support("N1", fixed_restraints(), "Empotramiento")
    case.model.add_nodal_load(case.load_case.id, case.ids["N2"], fz=u.Quantity.of(-P, "N"))
    _end(case, "B1", MemberEnd.I, _SPRING)


def _rows_ref02(c: _Case) -> list[Row]:
    return [
        ("R_Z N1", "+P", FORCE, P, c.reaction("N1", FZ)),
        ("R_My N1", "−PL", MOMENT, -P * SPAN, c.reaction("N1", MY)),
        (
            "Uz N2",
            "−(PL³/(3EI) + PL²/k)",
            DISP,
            -(P * SPAN**3 / (3 * EI) + P * SPAN**2 / _K),
            c.disp("N2", UZ),
        ),
        (
            "Ry N2",
            "+(PL²/(2EI) + PL/k)",
            ROT,
            P * SPAN**2 / (2 * EI) + P * SPAN / _K,
            c.disp("N2", RY),
        ),
    ]


def _build_ref03(case: _Case) -> None:
    case.node("N1", 0.0)
    case.node("N2", SPAN)
    case.node("N3", 2 * SPAN)
    case.member("B1", "N1", "N2")
    case.member("B2", "N2", "N3")
    case.support("N1", fixed_restraints(), "Empotramiento")
    case.support("N3", _SLIDER, "Deslizadera Uz")
    case.model.add_nodal_load(case.load_case.id, case.ids["N2"], fz=u.Quantity.of(-P, "N"))
    _end(case, "B1", MemberEnd.J, _RELEASED)


def _rows_ref03(c: _Case) -> list[Row]:
    tip = P * SPAN**3 / (3 * EI)
    return [
        ("R_Z N1", "+P", FORCE, P, c.reaction("N1", FZ)),
        ("R_Z N3", "0", FORCE, 0.0, c.reaction("N3", FZ)),
        ("R_My N1", "−PL", MOMENT, -P * SPAN, c.reaction("N1", MY)),
        ("M extremo J B1 (rótula)", "0", MOMENT, 0.0, _end_moment(c, "B1", MemberEnd.J)),
        ("M extremo I B2", "0", MOMENT, 0.0, _end_moment(c, "B2", MemberEnd.I)),
        ("Uz N2", "−PL³/(3EI)", DISP, -tip, c.disp("N2", UZ)),
        ("Ry N3", "−PL²/(3EI)", ROT, -tip / SPAN, c.disp("N3", RY)),
    ]


_DIAGONAL: Final = math.hypot(SPAN / 2, TRUSS_RISE)
_SIN_T: Final = TRUSS_RISE / _DIAGONAL
_COS_T: Final = (SPAN / 2) / _DIAGONAL


def _build_ref04(case: _Case) -> None:
    case.node("N1", 0.0)
    case.node("N2", SPAN)
    case.node("N3", SPAN / 2, TRUSS_RISE)
    for label, i, j in (("B1", "N1", "N2"), ("B2", "N1", "N3"), ("B3", "N2", "N3")):
        case.member(label, i, j)
        _end(case, label, MemberEnd.I, _RELEASED)
        _end(case, label, MemberEnd.J, _RELEASED)
    case.support("N1", pinned_restraints(), "Articulación")
    case.support("N2", roller_restraints(), "Rodillo")
    case.model.add_nodal_load(case.load_case.id, case.ids["N3"], fz=u.Quantity.of(-P, "N"))


def _rows_ref04(c: _Case) -> list[Row]:
    diagonal = -P / (2 * _SIN_T)  # Compresión.
    chord = -diagonal * _COS_T  # Tracción.
    work = chord**2 * SPAN + 2 * diagonal**2 * _DIAGONAL
    rows: list[Row] = [
        ("R_Z N1", "+P/2", FORCE, P / 2, c.reaction("N1", FZ)),
        ("R_Z N2", "+P/2", FORCE, P / 2, c.reaction("N2", FZ)),
        ("R_X N1", "0", FORCE, 0.0, c.reaction("N1", FX)),
        ("N B1", "+P/(2·tan a)", FORCE, chord, c.cut("B1", N, at_end=False)),
        ("N B2", "−P/(2·sin a)", FORCE, diagonal, c.cut("B2", N, at_end=False)),
        ("N B3", "−P/(2·sin a)", FORCE, diagonal, c.cut("B3", N, at_end=False)),
        ("Uz N3", "−Σ N²L/(P·EA)", DISP, -work / (P * EA), c.disp("N3", UZ)),
    ]
    for member in ("B1", "B2", "B3"):
        for end in MemberEnd:
            rows.append(
                (f"M extremo {end} {member}", "0", MOMENT, 0.0, _end_moment(c, member, end))
            )
    return rows


def _notes_orphans(c: _Case) -> list[Text]:
    nodes = sum(
        1
        for n in c._res().nodes
        if n.displacement.components[RY].status is ComponentStatus.NOT_APPLICABLE
    )
    return [Text.of("benchmark.note_orphan_rotations", nodes=nodes)]


_BEAM: Final = {"span": SPAN, "load_kn": P / 1000}
_MOMENT_SCALE_Q: Final = {MOMENT: W * SPAN**2 / 8}
_MOMENT_SCALE_P: Final = {MOMENT: P * SPAN}

SPECS: Final[dict[str, tuple[_Spec, ...]]] = {
    "T-FEM-001": (
        _Spec(
            "T-FEM-001",
            Text.of("benchmark.title_simply_supported"),
            Text.of("benchmark.description_simply_supported", **_BEAM),
            TOL_EXACT,
            _build_001,
            _rows_001,
            _no_notes,
        ),
    ),
    "T-FEM-002": (
        _Spec(
            "T-FEM-002",
            Text.of("benchmark.title_cantilever"),
            Text.of("benchmark.description_cantilever", span=CANTILEVER, load_kn_m=W / 1000),
            TOL_EXACT,
            _build_002,
            _rows_002,
            _no_notes,
        ),
    ),
    "T-FEM-003": (
        _Spec(
            "T-FEM-003 a",
            Text.of("benchmark.title_spring_global"),
            Text.of("benchmark.description_spring_global", **_BEAM, stiffness=SPRING_K),
            TOL_EXACT,
            _build_003a,
            _rows_003a,
            _no_notes,
        ),
        _Spec(
            "T-FEM-003 b",
            Text.of("benchmark.title_spring_inclined"),
            Text.of(
                "benchmark.description_spring_inclined",
                **_BEAM,
                stiffness=SPRING_K,
                angle=ANGLE_DEG,
            ),
            TOL_EXACT,
            _build_003b,
            _rows_003b,
            _notes_inclined,
        ),
    ),
    "T-FEM-004": (
        _Spec(
            "T-FEM-004",
            Text.of("benchmark.title_inclined_roller"),
            Text.of("benchmark.description_inclined_roller", **_BEAM, angle=ANGLE_DEG),
            TOL_PENALTY,
            _build_004,
            _rows_004,
            _notes_inclined,
        ),
    ),
    "T-CON-REF-01": (
        _Spec(
            "T-CON-REF-01 a",
            Text.of("benchmark.title_propped_released"),
            Text.of("benchmark.description_propped_released", span=SPAN, load_kn_m=W / 1000),
            TOL_EXACT,
            lambda case: _propped(case, _RELEASED),
            _rows_ref01a,
            _no_notes,
            _MOMENT_SCALE_Q,
        ),
        _Spec(
            "T-CON-REF-01 b",
            Text.of("benchmark.title_propped_spring"),
            Text.of(
                "benchmark.description_propped_spring",
                span=SPAN,
                load_kn_m=W / 1000,
                ratio=SPRING_RATIO,
            ),
            TOL_EXACT,
            lambda case: _propped(case, _SPRING),
            _rows_ref01b,
            _no_notes,
            _MOMENT_SCALE_Q,
        ),
    ),
    "T-CON-REF-02": (
        _Spec(
            "T-CON-REF-02",
            Text.of("benchmark.title_cantilever_spring"),
            Text.of("benchmark.description_cantilever_spring", **_BEAM, ratio=SPRING_RATIO),
            TOL_EXACT,
            _build_ref02,
            _rows_ref02,
            _no_notes,
        ),
    ),
    "T-CON-REF-03": (
        _Spec(
            "T-CON-REF-03",
            Text.of("benchmark.title_internal_hinge"),
            Text.of("benchmark.description_internal_hinge", **_BEAM),
            TOL_EXACT,
            _build_ref03,
            _rows_ref03,
            _no_notes,
            _MOMENT_SCALE_P,
        ),
    ),
    "T-CON-REF-04": (
        _Spec(
            "T-CON-REF-04",
            Text.of("benchmark.title_truss"),
            Text.of("benchmark.description_truss", **_BEAM, rise=TRUSS_RISE),
            TOL_EXACT,
            _build_ref04,
            _rows_ref04,
            _notes_orphans,
            _MOMENT_SCALE_P,
        ),
    ),
}


def _run_spec(spec: _Spec, timeout_s: float | None) -> ReferenceOutcome:
    return _planar_case(spec, timeout_s)[1]


def _planar_case(spec: _Spec, timeout_s: float | None) -> tuple[_Case, ReferenceOutcome]:
    case = _Case()
    spec.build(case)
    config = AnalysisConfiguration(case.load_case.id)
    if timeout_s is not None:
        config = AnalysisConfiguration(case.load_case.id, timeout_s=timeout_s)
    run = analyze_planar_xz(case.model, config)
    results, failure = None, None
    if run.status is RunStatus.SUCCEEDED:
        try:
            results = normalize_planar_xz(run, case.model)
        except DiagnosticError as error:
            failure = error
    checks: tuple[ReferenceCheck, ...] = ()
    notes: tuple[Text, ...] = ()
    if results is not None:
        case.results = results
        checks = evaluate(spec.rows(case), spec.tolerance, spec.scales)
        notes = tuple(spec.notes(case))
    return case, ReferenceOutcome(
        spec.case_id,
        spec.title,
        spec.description,
        case.labels,
        run,
        results,
        failure,
        checks,
        notes,
    )


def run_reference(case_id: str, timeout_s: float | None = None) -> tuple[ReferenceOutcome, ...]:
    """Ejecuta un caso de ``CASES`` (T-FEM-003 da dos variantes)."""
    if case_id not in SPECS:
        raise ValueError(f"caso de referencia desconocido: {case_id}")
    return tuple(_run_spec(spec, timeout_s) for spec in SPECS[case_id])


# === Referencias espaciales (plan T-018, D8) y contraste cruzado (D1) ===================


def _calculated(value: float | None) -> float:
    if value is None:  # pragma: no cover (rectangular_properties las calcula todas)
        raise ValueError("propiedad de sección no disponible")
    return value


_PROPS: Final = rectangular_properties(B, H)
AREA_3D: Final = _calculated(_PROPS.area.value)
IY_3D: Final = _calculated(_PROPS.iy.value)
IZ_3D: Final = _calculated(_PROPS.iz.value)
J_3D: Final = _calculated(_PROPS.torsion_constant.value)  # Saint-Venant, 7,3178e-4 m⁴

SPATIAL_CASES: Final = ("T-FEM3D-01", "T-FEM3D-02", "T-FEM3D-03", "T-FEM3D-04", "T-FEM3D-05")
CROSS_CASES: Final = (
    "T-FEM-001",
    "T-FEM-002",
    "T-FEM-003",
    "T-FEM-004",
    "T-CON-REF-01",
    "T-CON-REF-02",
    "T-CON-REF-03",
    "T-CON-REF-04",
)

SPATIAL_HYPOTHESES: Final = (
    Text.of("benchmark.hypothesis_spatial_formulation"),
    Text.of(
        "benchmark.hypothesis_spatial_data",
        modulus_gpa=E / 1e9,
        shear_gpa=G / 1e9,
        b_mm=B * 1000,
        h_mm=H * 1000,
        torsion=J_3D,
    ),
)

# Índices ADR-001 que el plano no publica.
UY, RX, RZ = 1, 3, 5
_OUT_OF_PLANE: Final = (UY, RX, RZ)
PLANAR: Final = "plano"  # fórmula de una fila 3D frente a plano (D1)
OUT_OF_PLANE_ZERO: Final = "0 (fuera del plano)"

Matrix3 = Sequence[Sequence[float]]


@dataclass(frozen=True, slots=True)
class SpatialReferenceOutcome:
    """Resultado de un caso espacial; mismos campos que ``ReferenceOutcome``.

    En el contraste cruzado (D1), ``planar`` es la corrida plana del mismo caso y ``checks``
    reúne las fórmulas del caso plano sobre el resultado 3D y la comparación 3D-plano.
    """

    case_id: str
    title: Text
    description: Text
    labels: dict[UUID, str]
    run: SpatialAnalysisRun
    results: SpatialResults | None
    failure: DiagnosticError | None
    checks: tuple[ReferenceCheck, ...]
    notes: tuple[Text, ...]
    planar: ReferenceOutcome | None = None

    @property
    def matches(self) -> bool:
        return (
            self.results is not None
            and bool(self.checks)
            and all(check.matches for check in self.checks)
            and (self.planar is None or self.planar.matches)
        )


def _q(c: _Case, member: str) -> tuple[tuple[float, float, float], ...]:
    """Q del dominio (filas ex, ey, ez), aceptada en T-003."""
    return c.model.axes(c.ids[member]).matrix()


def _rotate(q: Matrix3, values: Sequence[float]) -> list[float]:
    """Q·v por bloques de tres."""
    result: list[float] = []
    for start in range(0, len(values), 3):
        block = values[start : start + 3]
        result += [sum(q[r][k] * block[k] for k in range(3)) for r in range(3)]
    return result


def _to_global(q: Matrix3, local: Sequence[float]) -> list[float]:
    """Qᵀ·v por bloques de tres."""
    result: list[float] = []
    for start in range(0, len(local), 3):
        block = local[start : start + 3]
        result += [sum(q[r][k] * block[r] for r in range(3)) for k in range(3)]
    return result


def _disp6(c: _Case, node: str) -> tuple[float | None, ...]:
    item = next(n for n in c._res().nodes if n.node_id == c.ids[node])
    return item.displacement.values()


def _reaction6(c: _Case, node: str) -> tuple[float | None, ...]:
    item = next(r for r in c._res().reactions if r.node_id == c.ids[node])
    return item.global_.values()


def _p_i(c: _Case, member: str) -> tuple[float | None, ...]:
    item = next(m for m in c._res().members if m.member_id == c.ids[member])
    return item.p_i.values()


def _local_disp(c: _Case, node: str, member: str) -> list[float | None]:
    """Desplazamiento del nudo en la base local de la barra; None si falta una componente."""
    values = _disp6(c, node)
    if any(value is None for value in values):
        return [None] * 6
    return list(_rotate(_q(c, member), [value or 0.0 for value in values]))


def _six_rows(  # noqa: PLR0913, PLR0917 (seis secuencias paralelas de la misma fila)
    where: str,
    names: Sequence[str],
    formulas: Sequence[str],
    kinds: Sequence[QuantityKind],
    references: Sequence[float],
    computed: Sequence[float | None],
) -> list[Row]:
    return [
        (f"{name} {where}", formula, kind, reference, value)
        for name, formula, kind, reference, value in zip(
            names, formulas, kinds, references, computed, strict=True
        )
    ]


_U_NAMES: Final = ("ux", "uy", "uz", "θx", "θy", "θz")
_U_KINDS: Final = (DISP, DISP, DISP, ROT, ROT, ROT)
_F_KINDS: Final = (FORCE, FORCE, FORCE, MOMENT, MOMENT, MOMENT)
_P_NAMES: Final = ("pI_x", "pI_y", "pI_z", "pI_Mx", "pI_My", "pI_Mz")
_R_NAMES: Final = ("R_X", "R_Y", "R_Z", "R_MX", "R_MY", "R_MZ")
T = 3  # índice del torsor en un Vector6 de barra


# --- T-FEM3D-01: voladizo espacial ------------------------------------------------------

CANTILEVER_3D: Final = 3.5  # m
OBLIQUE_TIP: Final = (1.0, -1.5, 3.0)  # m, |OJ| = 3,5 (dirección (2, −3, 6)/7)
OBLIQUE_ALPHA: Final = 0.37  # rad
TIP_LOCAL: Final = (20_000.0, 5_000.0, -10_000.0, 3_000.0)  # N, Py, Pz (N) y T (N·m)


def _build_3d01(tip: tuple[float, float, float], alpha: float | None) -> Callable[[_Case], None]:
    def build(case: _Case) -> None:
        case.node("O", 0.0)
        case.node("J", tip[0], tip[2], y=tip[1])
        case.member("B", "O", "J", alpha)
        case.support("O", fixed_restraints(), "Empotramiento")
        normal, py, pz, torque = TIP_LOCAL
        f = _to_global(_q(case, "B"), (normal, py, pz, torque, 0.0, 0.0))
        m = u.Quantity.of
        case.model.add_nodal_load(
            case.load_case.id,
            case.ids["J"],
            fx=m(f[0], "N"),
            fy=m(f[1], "N"),
            fz=m(f[2], "N"),
            mx=m(f[3], "N*m"),
            my=m(f[4], "N*m"),
            mz=m(f[5], "N*m"),
        )

    return build


def _rows_3d01(c: _Case) -> list[Row]:
    length = CANTILEVER_3D
    normal, py, pz, torque = TIP_LOCAL
    disp = (
        normal * length / (E * AREA_3D),
        py * length**3 / (3 * E * IZ_3D),
        pz * length**3 / (3 * E * IY_3D),
        torque * length / (G * J_3D),
        -pz * length**2 / (2 * E * IY_3D),
        py * length**2 / (2 * E * IZ_3D),
    )
    p_i = (-normal, -py, -pz, -torque, pz * length, -py * length)
    return [
        *_six_rows(
            "J (local)",
            _U_NAMES,
            ("NL/(EA)", "PyL³/(3EIz)", "PzL³/(3EIy)", "TL/(GJ)", "−PzL²/(2EIy)", "+PyL²/(2EIz)"),
            _U_KINDS,
            disp,
            _local_disp(c, "J", "B"),
        ),
        *_six_rows(
            "B",
            _P_NAMES,
            ("−N", "−Py", "−Pz", "−T", "+Pz·L", "−Py·L"),
            _F_KINDS,
            p_i,
            _p_i(c, "B"),
        ),
        *_six_rows(
            "O", _R_NAMES, ("Qᵀ·pI",) * 6, _F_KINDS, _to_global(_q(c, "B"), p_i), _reaction6(c, "O")
        ),
    ]


# --- T-FEM3D-02: ménsula acodada ---------------------------------------------------------

ARM_1: Final = 3.0  # m, B1 = O→A en +X
ARM_2: Final = 2.0  # m, B2 = A→C en +Y (ex = Y, ey = −X, ez = Z)


def _build_bracket(case: _Case) -> None:
    case.node("O", 0.0)
    case.node("A", ARM_1)
    case.node("C", ARM_1, y=ARM_2)
    case.member("B1", "O", "A")
    case.member("B2", "A", "C")
    case.support("O", fixed_restraints(), "Empotramiento")


def _build_3d02a(case: _Case) -> None:
    _build_bracket(case)
    case.model.add_nodal_load(case.load_case.id, case.ids["C"], fz=u.Quantity.of(-P, "N"))


def _build_3d02b(case: _Case) -> None:
    _build_bracket(case)
    case.model.add_nodal_load(case.load_case.id, case.ids["C"], fx=u.Quantity.of(P, "N"))


def _rows_3d02a(c: _Case) -> list[Row]:
    l1, l2 = ARM_1, ARM_2
    deflection = P * l1**3 / (3 * E * IY_3D) + P * l2**3 / (3 * E * IY_3D)
    deflection += P * l1 * l2**2 / (G * J_3D)
    o, b2 = _reaction6(c, "O"), _p_i(c, "B2")
    return [
        (
            "Uz C",
            "−(PL1³/(3EIy) + PL2³/(3EIy) + P·L1·L2²/(GJ))",
            DISP,
            -deflection,
            c.disp("C", UZ),
        ),
        ("Ux C", "0", DISP, 0.0, c.disp("C", UX)),
        ("Uy C", "0", DISP, 0.0, c.disp("C", UY)),
        ("R_Z O", "+P", FORCE, P, o[2]),
        ("R_MX O", "+P·L2", MOMENT, P * l2, o[3]),
        ("R_MY O", "−P·L1", MOMENT, -P * l1, o[4]),
        ("R_MZ O", "0", MOMENT, 0.0, o[5]),
        ("T(0+) B1", "−P·L2", MOMENT, -P * l2, c.cut("B1", T, at_end=False)),
        ("My(0+) B1", "+P·L1", MOMENT, P * l1, c.cut("B1", MY, at_end=False)),
        ("Vz(0+) B1", "−P", FORCE, -P, c.cut("B1", VZ, at_end=False)),
        ("pI_z B2", "+P", FORCE, P, b2[2]),
        ("pI_Mx B2", "0", MOMENT, 0.0, b2[3]),
        ("pI_My B2", "−P·L2", MOMENT, -P * l2, b2[4]),
    ]


def _rows_3d02b(c: _Case) -> list[Row]:
    l1, l2 = ARM_1, ARM_2
    ux = P * l2**3 / (3 * E * IZ_3D) + P * l1 / (E * AREA_3D) + P * l1 * l2**2 / (E * IZ_3D)
    o = _reaction6(c, "O")
    return [
        ("Ux C", "HL2³/(3EIz) + HL1/(EA) + H·L1·L2²/(EIz)", DISP, ux, c.disp("C", UX)),
        ("Uz C", "0", DISP, 0.0, c.disp("C", UZ)),
        ("R_X O", "−H", FORCE, -P, o[0]),
        ("R_MX O", "0", MOMENT, 0.0, o[3]),
        ("R_MY O", "0", MOMENT, 0.0, o[4]),
        ("R_MZ O", "+H·L2", MOMENT, P * l2, o[5]),
    ]


# --- T-FEM3D-03: pórtico biempotrado (derivación revisada del plan T-018) ----------------

PORTAL_SPAN: Final = 4.0  # m
PORTAL_HEIGHT: Final = 3.0  # m


def _build_portal(case: _Case) -> None:
    case.node("O1", 0.0)
    case.node("O2", PORTAL_SPAN)
    case.node("A1", 0.0, PORTAL_HEIGHT)
    case.node("A2", PORTAL_SPAN, PORTAL_HEIGHT)
    case.member("C1", "O1", "A1")
    case.member("C2", "O2", "A2")
    case.member("D", "A1", "A2")
    case.support("O1", fixed_restraints(), "Empotramiento 1")
    case.support("O2", fixed_restraints(), "Empotramiento 2")


def _build_3d03a(case: _Case) -> None:
    _build_portal(case)
    for node in ("A1", "A2"):
        case.model.add_nodal_load(case.load_case.id, case.ids[node], fx=u.Quantity.of(P / 2, "N"))


def _build_3d03b(case: _Case) -> None:
    _build_portal(case)
    case.model.add_nodal_load(case.load_case.id, case.ids["A1"], fy=u.Quantity.of(P, "N"))


def _rows_3d03a(c: _Case) -> list[Row]:
    span, height, load = PORTAL_SPAN, PORTAL_HEIGHT, P
    col, beam, axial = E * IY_3D / height, E * IY_3D / span, E * AREA_3D / height
    det = col * axial + 6 * axial * beam + 24 * col * beam / span**2
    sway = load * height**2 * (4 * col * axial + 6 * axial * beam + 96 * col * beam / span**2)
    sway /= 24 * col * det
    theta = load * height * (axial + 24 * beam / span**2) / (4 * det)
    lift = 3 * load * height * beam / (span * det)
    base = -6 * col * sway / height + 2 * col * theta
    beam_moment = -12 * beam * lift / span + 6 * beam * theta
    o1, o2 = _reaction6(c, "O1"), _reaction6(c, "O2")
    p_c1, p_d = _p_i(c, "C1"), _p_i(c, "D")
    return [
        ("Ux A1", "Δ", DISP, sway, c.disp("A1", UX)),
        ("Ux A2", "Δ", DISP, sway, c.disp("A2", UX)),
        ("Uz A1", "+v", DISP, lift, c.disp("A1", UZ)),
        ("Uz A2", "−v", DISP, -lift, c.disp("A2", UZ)),
        ("Ry A1", "θ", ROT, theta, c.disp("A1", RY)),
        ("Ry A2", "θ", ROT, theta, c.disp("A2", RY)),
        ("Uy A1", "0", DISP, 0.0, c.disp("A1", UY)),
        ("Rx A1", "0", ROT, 0.0, c.disp("A1", RX)),
        ("Rz A1", "0", ROT, 0.0, c.disp("A1", RZ)),
        ("R_X O1", "−H/2", FORCE, -load / 2, o1[0]),
        ("R_Z O1", "−a·v", FORCE, -axial * lift, o1[2]),
        ("R_MY O1", "−6cΔ/h + 2cθ", MOMENT, base, o1[4]),
        ("R_X O2", "−H/2", FORCE, -load / 2, o2[0]),
        ("R_Z O2", "+a·v", FORCE, axial * lift, o2[2]),
        ("R_MY O2", "−6cΔ/h + 2cθ", MOMENT, base, o2[4]),
        ("R_Y O1", "0", FORCE, 0.0, o1[1]),
        ("R_MX O1", "0", MOMENT, 0.0, o1[3]),
        ("R_MZ O1", "0", MOMENT, 0.0, o1[5]),
        ("pI_x C1", "−a·v", FORCE, -axial * lift, p_c1[0]),
        ("pI_z C1", "+H/2", FORCE, load / 2, p_c1[2]),
        ("pI_My C1", "−6cΔ/h + 2cθ", MOMENT, base, p_c1[4]),
        ("pI_x D", "0", FORCE, 0.0, p_d[0]),
        ("pI_z D", "−a·v", FORCE, -axial * lift, p_d[2]),
        ("pI_My D", "−12b·v/L + 6b·θ", MOMENT, beam_moment, p_d[4]),
    ]


def _rows_3d03b(c: _Case) -> list[Row]:
    span, height, load = PORTAL_SPAN, PORTAL_HEIGHT, P
    iw, ibz = E * IZ_3D / height, E * IZ_3D / span
    tb, tc = G * J_3D / span, G * J_3D / height
    sym_u = load * height**3 / (6 * E * IZ_3D)
    sym_t = -load * height**2 / (4 * E * IZ_3D)
    stiffness = 6 * iw * (iw + 2 * tb) / (height**2 * (2 * iw + tb))
    stiffness += 24 * ibz * tc / (span**2 * (6 * ibz + tc))
    anti_u = load / 2 / stiffness
    anti_t = -3 * iw * anti_u / (height * (2 * iw + tb))
    anti_z = -12 * ibz * anti_u / (span * (6 * ibz + tc))
    uy1, uy2 = sym_u + anti_u, sym_u - anti_u
    tx1, tx2 = sym_t + anti_t, sym_t - anti_t
    shear = 12 * iw / height**2 * anti_u + 6 * iw / height * anti_t
    o1, o2 = _reaction6(c, "O1"), _reaction6(c, "O2")
    return [
        ("Uy A1", "δs + δa", DISP, uy1, c.disp("A1", UY)),
        ("Uy A2", "δs − δa", DISP, uy2, c.disp("A2", UY)),
        ("Rx A1", "θx,s + θx,a", ROT, tx1, c.disp("A1", RX)),
        ("Rx A2", "θx,s − θx,a", ROT, tx2, c.disp("A2", RX)),
        ("Rz A1", "θz,a", ROT, anti_z, c.disp("A1", RZ)),
        ("Rz A2", "θz,a", ROT, anti_z, c.disp("A2", RZ)),
        ("Ux A1", "0", DISP, 0.0, c.disp("A1", UX)),
        ("Uz A1", "0", DISP, 0.0, c.disp("A1", UZ)),
        ("Ry A1", "0", ROT, 0.0, c.disp("A1", RY)),
        ("R_Y O1", "−P/2 − S", FORCE, -load / 2 - shear, o1[1]),
        ("R_Y O2", "−P/2 + S", FORCE, -load / 2 + shear, o2[1]),
        ("R_MX O1", "6iw/h·uy + 2iw·θx", MOMENT, 6 * iw / height * uy1 + 2 * iw * tx1, o1[3]),
        ("R_MX O2", "6iw/h·uy + 2iw·θx", MOMENT, 6 * iw / height * uy2 + 2 * iw * tx2, o2[3]),
        ("R_MZ O1", "−tc·θz,a", MOMENT, -tc * anti_z, o1[5]),
        ("R_MZ O2", "−tc·θz,a", MOMENT, -tc * anti_z, o2[5]),
        ("R_X O1", "0", FORCE, 0.0, o1[0]),
        ("R_Z O1", "0", FORCE, 0.0, o1[2]),
        ("R_MY O1", "0", MOMENT, 0.0, o1[4]),
        ("pI_Mx D", "2tb·θx,a", MOMENT, 2 * tb * anti_t, _p_i(c, "D")[T]),
        ("pI_Mx C1", "−tc·θz,a", MOMENT, -tc * anti_z, _p_i(c, "C1")[T]),
        ("pI_Mx C2", "−tc·θz,a", MOMENT, -tc * anti_z, _p_i(c, "C2")[T]),
    ]


# --- T-FEM3D-05: voladizo oblicuo con cargas de barra -----------------------------------

OBLIQUE_TIP_05: Final = (-3.0, 1.0, 1.5)  # m, |OJ| = 3,5 (dirección (−6, 2, 3)/7)
OBLIQUE_ALPHA_05: Final = -0.6  # rad
LOAD_LOCAL_05: Final = (4_000.0, -3_000.0, 2_000.0)  # N/m, base LOCAL (Wx ≠ 0)
LOAD_GLOBAL_05: Final = (1_000.0, 2_000.0, -5_000.0)  # N/m, base GLOBAL


def _build_oblique(case: _Case) -> None:
    tip = OBLIQUE_TIP_05
    case.node("O", 0.0)
    case.node("J", tip[0], tip[2], y=tip[1])
    case.member("B", "O", "J", OBLIQUE_ALPHA_05)
    case.support("O", fixed_restraints(), "Empotramiento")


def _build_3d05a(case: _Case) -> None:
    _build_oblique(case)
    case.model.set_self_weight(case.load_case.id)


def _build_3d05b(case: _Case) -> None:
    _build_oblique(case)
    member = case.ids["B"]
    for basis, values in ((LoadBasis.LOCAL, LOAD_LOCAL_05), (LoadBasis.GLOBAL, LOAD_GLOBAL_05)):
        qx, qy, qz = (u.Quantity.of(value, "N/m") for value in values)
        case.model.add_member_uniform_load(
            case.load_case.id, member, basis=basis, qx=qx, qy=qy, qz=qz
        )


def _rows_oblique(c: _Case, q_global: Sequence[float]) -> list[Row]:
    """Voladizo empotrado en O con q uniforme ``q_global`` (N/m, base global) en toda la barra."""
    q = _q(c, "B")
    wx, wy, wz = _rotate(q, q_global)
    length = math.dist((0.0, 0.0, 0.0), OBLIQUE_TIP_05)
    disp = (
        wx * length**2 / (2 * E * AREA_3D),
        wy * length**4 / (8 * E * IZ_3D),
        wz * length**4 / (8 * E * IY_3D),
        0.0,
        -wz * length**3 / (6 * E * IY_3D),
        wy * length**3 / (6 * E * IZ_3D),
    )
    total = [value * length for value in q_global]
    mid = [coordinate / 2 for coordinate in OBLIQUE_TIP_05]
    moment = (
        mid[1] * total[2] - mid[2] * total[1],
        mid[2] * total[0] - mid[0] * total[2],
        mid[0] * total[1] - mid[1] * total[0],
    )
    reaction = [-value for value in (*total, *moment)]
    return [
        *_six_rows(
            "J (local)",
            _U_NAMES,
            ("wxL²/(2EA)", "wyL⁴/(8EIz)", "wzL⁴/(8EIy)", "0", "−wzL³/(6EIy)", "+wyL³/(6EIz)"),
            _U_KINDS,
            disp,
            _local_disp(c, "J", "B"),
        ),
        *_six_rows(
            "O",
            _R_NAMES,
            ("−q·L",) * 3 + ("−r_medio × q·L",) * 3,
            _F_KINDS,
            reaction,
            _reaction6(c, "O"),
        ),
        *_six_rows("B", _P_NAMES, ("Q·R",) * 6, _F_KINDS, _rotate(q, reaction), _p_i(c, "B")),
    ]


def _rows_3d05a(c: _Case) -> list[Row]:
    # ρ·A·g del propio modelo (peso propio aceptado en T-005), no un literal.
    weight = c.model.self_weight_load(c.ids["B"])[2].si_value
    return _rows_oblique(c, (0.0, 0.0, weight))


def _rows_3d05b(c: _Case) -> list[Row]:
    from_local = _to_global(_q(c, "B"), LOAD_LOCAL_05)
    return _rows_oblique(c, [a + b for a, b in zip(from_local, LOAD_GLOBAL_05, strict=True)])


# --- T-FEM3D-04: trípode espacial (E2; derivación revisada en el plan) -------------------
# Bases empotradas; barras de base a vértice con Ry y Rz liberados en I y J (preset
# articulado). e_i = (V − base)/L_i son cuádruplas pitagóricas: det[e_A e_B e_C] = 5/9.

TRIPOD_APEX: Final = (0.0, 0.0, 4.0)  # m
TRIPOD_BASES: Final = {
    "A": (1.0, 1.5, 1.0),  # e = (−2, −3, 6)/7, L = 3,5 m
    "B": (-2.0, 2.0, 0.5),  # e = (4, −4, 7)/9, L = 4,5 m
    "C": (-0.5, -2.0, 0.0),  # e = (1, 4, 8)/9, L = 4,5 m
}
TRIPOD_LOAD_GENERAL: Final = (6_000.0, -4_000.0, -15_000.0)  # N, global
TRIPOD_LOAD_VERTICAL: Final = (0.0, 0.0, -10_000.0)  # N, global


def _tripod_axes() -> dict[str, tuple[tuple[float, float, float], float]]:
    """(e_i, L_i) por base, con e_i de la base al vértice."""
    axes = {}
    for label, base in TRIPOD_BASES.items():
        length = math.dist(base, TRIPOD_APEX)
        direction = tuple((a - b) / length for a, b in zip(TRIPOD_APEX, base, strict=True))
        axes[label] = ((direction[0], direction[1], direction[2]), length)
    return axes


def _det3(m: Sequence[Sequence[float]]) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def _cramer(m: Sequence[Sequence[float]], rhs: Sequence[float]) -> list[float]:
    """Solución de m·x = rhs (3×3) por la regla de Cramer."""
    det = _det3(m)
    solution = []
    for column in range(3):
        replaced = [[rhs[r] if k == column else m[r][k] for k in range(3)] for r in range(3)]
        solution.append(_det3(replaced) / det)
    return solution


def _tripod_reference(load: Sequence[float]) -> tuple[list[float], list[float]]:
    """(N_i, u_V): Σ N_i·e_i = P y e_i·u = N_i·L_i/(EA), tracción positiva."""
    axes = _tripod_axes()
    labels = list(TRIPOD_BASES)
    columns = [[axes[label][0][r] for label in labels] for r in range(3)]
    forces = _cramer(columns, load)
    rows = [list(axes[label][0]) for label in labels]
    stretch = [n * axes[label][1] / (E * AREA_3D) for n, label in zip(forces, labels, strict=True)]
    return forces, _cramer(rows, stretch)


def _build_tripod(load: Sequence[float]) -> Callable[[_Case], None]:
    def build(case: _Case) -> None:
        apex = TRIPOD_APEX
        case.node("V", apex[0], apex[2], y=apex[1])
        for label, base in TRIPOD_BASES.items():
            case.node(label, base[0], base[2], y=base[1])
            case.support(label, fixed_restraints(), f"Empotramiento {label}")
            member = case.member(f"B{label}", label, "V")
            for end in MemberEnd:
                case.model.set_member_end(member, end, EndConnection.pinned())
        m = u.Quantity.of
        case.model.add_nodal_load(
            case.load_case.id,
            case.ids["V"],
            fx=m(load[0], "N"),
            fy=m(load[1], "N"),
            fz=m(load[2], "N"),
        )

    return build


def _rows_tripod(load: Sequence[float]) -> Callable[[_Case], list[Row]]:
    def rows(c: _Case) -> list[Row]:
        forces, disp = _tripod_reference(load)
        axes = _tripod_axes()
        result = _six_rows(
            "V",
            _U_NAMES,
            ("Eᵀ·u = N·L/(EA)",) * 3 + ("0",) * 3,
            _U_KINDS,
            (*disp, 0.0, 0.0, 0.0),
            _disp6(c, "V"),
        )
        for normal, (label, (direction, _length)) in zip(forces, axes.items(), strict=True):
            result += _six_rows(
                f"B{label}",
                _P_NAMES,
                ("−N_i (Σ N·e = P)",) + ("0",) * 5,
                _F_KINDS,
                (-normal, 0.0, 0.0, 0.0, 0.0, 0.0),
                _p_i(c, f"B{label}"),
            )
            result += _six_rows(
                label,
                _R_NAMES,
                ("−N_i·e_i",) * 3 + ("0",) * 3,
                _F_KINDS,
                (*(-normal * value for value in direction), 0.0, 0.0, 0.0),
                _reaction6(c, label),
            )
        # Control energético (revisión, o1): trabajo con el u calculado frente a Σ N²L/(EA).
        norm = math.hypot(*load)
        energy = sum(
            n * n * length / (E * AREA_3D)
            for n, (_direction, length) in zip(forces, axes.values(), strict=True)
        )
        computed = _disp6(c, "V")[:3]
        work = (
            None
            if any(value is None for value in computed)
            else sum(p * (value or 0.0) for p, value in zip(load, computed, strict=True))
        )
        result.append(
            (
                "P·u/|P| V",
                "Σ N²L/(EA)/|P|",
                DISP,
                energy / norm,
                None if work is None else work / norm,
            )
        )
        return result

    return rows


def _tripod_scales(load: Sequence[float]) -> dict[QuantityKind, float]:
    """Escalas de las filas nulas: |P|, |P|·L_máx y |u_ref|/L_máx (giros)."""
    norm = math.hypot(*load)
    longest = max(length for _direction, length in _tripod_axes().values())
    _forces, disp = _tripod_reference(load)
    return {FORCE: norm, MOMENT: norm * longest, ROT: math.hypot(*disp) / longest}


def _point(point: Sequence[float]) -> str:
    return ", ".join(f"{value:g}" for value in point)


_TRIPOD: Final = {
    "apex": _point(TRIPOD_APEX),
    **{label.lower(): _point(base) for label, base in TRIPOD_BASES.items()},
}
_TIP: Final = {
    "length": CANTILEVER_3D,
    "n_kn": TIP_LOCAL[0] / 1000,
    "py_kn": TIP_LOCAL[1] / 1000,
    "pz_kn": TIP_LOCAL[2] / 1000,
    "t_knm": TIP_LOCAL[3] / 1000,
}
_BRACKET: Final = {"l1": ARM_1, "l2": ARM_2, "load_kn": P / 1000}
_PORTAL: Final = {"span": PORTAL_SPAN, "height": PORTAL_HEIGHT, "load_kn": P / 1000}
_PORTAL_SCALES: Final = {FORCE: P, MOMENT: P * PORTAL_SPAN}
_OBLIQUE_05: Final = {
    "length": math.dist((0.0, 0.0, 0.0), OBLIQUE_TIP_05),
    "alpha": OBLIQUE_ALPHA_05,
}


def _kn(values: Sequence[float]) -> str:
    return ", ".join(f"{value / 1000:g}" for value in values)


SPATIAL_SPECS: Final[dict[str, tuple[_Spec, ...]]] = {
    "T-FEM3D-01": (
        _Spec(
            "T-FEM3D-01 a",
            Text.of("benchmark.title_spatial_cantilever_y"),
            Text.of("benchmark.description_spatial_cantilever_y", **_TIP),
            TOL_EXACT,
            _build_3d01((0.0, CANTILEVER_3D, 0.0), None),
            _rows_3d01,
            _no_notes,
        ),
        _Spec(
            "T-FEM3D-01 b",
            Text.of("benchmark.title_spatial_cantilever_oblique"),
            Text.of(
                "benchmark.description_spatial_cantilever_oblique",
                **_TIP,
                x=OBLIQUE_TIP[0],
                y=OBLIQUE_TIP[1],
                z=OBLIQUE_TIP[2],
                alpha=OBLIQUE_ALPHA,
            ),
            TOL_EXACT,
            _build_3d01(OBLIQUE_TIP, OBLIQUE_ALPHA),
            _rows_3d01,
            _no_notes,
        ),
    ),
    "T-FEM3D-02": (
        _Spec(
            "T-FEM3D-02 a",
            Text.of("benchmark.title_bracket_vertical"),
            Text.of("benchmark.description_bracket_vertical", **_BRACKET),
            TOL_EXACT,
            _build_3d02a,
            _rows_3d02a,
            _no_notes,
        ),
        _Spec(
            "T-FEM3D-02 b",
            Text.of("benchmark.title_bracket_horizontal"),
            Text.of("benchmark.description_bracket_horizontal", **_BRACKET),
            TOL_EXACT,
            _build_3d02b,
            _rows_3d02b,
            _no_notes,
        ),
    ),
    "T-FEM3D-03": (
        _Spec(
            "T-FEM3D-03 a",
            Text.of("benchmark.title_portal_in_plane"),
            Text.of("benchmark.description_portal_in_plane", **_PORTAL),
            TOL_EXACT,
            _build_3d03a,
            _rows_3d03a,
            _no_notes,
            _PORTAL_SCALES,
        ),
        _Spec(
            "T-FEM3D-03 b",
            Text.of("benchmark.title_portal_out_of_plane"),
            Text.of("benchmark.description_portal_out_of_plane", **_PORTAL),
            TOL_EXACT,
            _build_3d03b,
            _rows_3d03b,
            _no_notes,
            _PORTAL_SCALES,
        ),
    ),
    "T-FEM3D-04": (
        _Spec(
            "T-FEM3D-04 a",
            Text.of("benchmark.title_tripod_general"),
            Text.of("benchmark.description_tripod", load_kn=_kn(TRIPOD_LOAD_GENERAL), **_TRIPOD),
            TOL_EXACT,
            _build_tripod(TRIPOD_LOAD_GENERAL),
            _rows_tripod(TRIPOD_LOAD_GENERAL),
            _no_notes,
            _tripod_scales(TRIPOD_LOAD_GENERAL),
        ),
        _Spec(
            "T-FEM3D-04 b",
            Text.of("benchmark.title_tripod_vertical"),
            Text.of("benchmark.description_tripod", load_kn=_kn(TRIPOD_LOAD_VERTICAL), **_TRIPOD),
            TOL_EXACT,
            _build_tripod(TRIPOD_LOAD_VERTICAL),
            _rows_tripod(TRIPOD_LOAD_VERTICAL),
            _no_notes,
            _tripod_scales(TRIPOD_LOAD_VERTICAL),
        ),
    ),
    "T-FEM3D-05": (
        _Spec(
            "T-FEM3D-05 a",
            Text.of("benchmark.title_oblique_self_weight"),
            Text.of("benchmark.description_oblique_self_weight", **_OBLIQUE_05),
            TOL_EXACT,
            _build_3d05a,
            _rows_3d05a,
            _no_notes,
            category=LoadCategory.PERMANENT,
        ),
        _Spec(
            "T-FEM3D-05 b",
            Text.of("benchmark.title_oblique_member_loads"),
            Text.of(
                "benchmark.description_oblique_member_loads",
                **_OBLIQUE_05,
                local_kn_m=_kn(LOAD_LOCAL_05),
                global_kn_m=_kn(LOAD_GLOBAL_05),
            ),
            TOL_EXACT,
            _build_3d05b,
            _rows_3d05b,
            _no_notes,
        ),
    ),
}


def _spatial_run(
    case: _Case, timeout_s: float | None
) -> tuple[SpatialAnalysisRun, SpatialResults | None, DiagnosticError | None]:
    config = SpatialAnalysisConfiguration(case.load_case.id)
    if timeout_s is not None:
        config = SpatialAnalysisConfiguration(case.load_case.id, timeout_s=timeout_s)
    run = analyze_spatial(case.model, config)
    results, failure = None, None
    if run.status is RunStatus.SUCCEEDED:
        try:
            results = normalize_spatial(run, case.model)
        except DiagnosticError as error:
            failure = error
    return run, results, failure


def _run_spatial_spec(spec: _Spec, timeout_s: float | None) -> SpatialReferenceOutcome:
    case = _Case(spec.category)
    spec.build(case)
    run, results, failure = _spatial_run(case, timeout_s)
    checks: tuple[ReferenceCheck, ...] = ()
    notes: tuple[Text, ...] = ()
    if results is not None:
        case.results = results
        checks = evaluate(spec.rows(case), spec.tolerance, spec.scales)
        notes = tuple(spec.notes(case))
    return SpatialReferenceOutcome(
        spec.case_id,
        spec.title,
        spec.description,
        case.labels,
        run,
        results,
        failure,
        checks,
        notes,
    )


def run_spatial_reference(
    case_id: str, timeout_s: float | None = None
) -> tuple[SpatialReferenceOutcome, ...]:
    """Ejecuta un caso de ``SPATIAL_CASES`` con el adaptador espacial (dos variantes cada uno)."""
    if case_id not in SPATIAL_SPECS:
        raise ValueError(f"caso de referencia espacial desconocido: {case_id}")
    return tuple(_run_spatial_spec(spec, timeout_s) for spec in SPATIAL_SPECS[case_id])


# --- Contraste cruzado espacial-plano (D1) --------------------------------------------


def _fix_out_of_plane(case: _Case) -> None:
    """Uy, Rx y Rz fijos en todos los nudos, en la base de su apoyo (D1; revisión, hallazgo 14).

    Los estados existentes (fijos y resortes) se conservan: solo los GDL libres pasan a fijos.
    Un resorte en Uy, Rx o Rz se mantendría (no ocurre en T-FEM-001…004 ni en T-CON-REF-01…04
    y no rompe el desacoplamiento). Los resortes de extremo de T-CON-REF (Ry, barra en +X)
    usan un aux coordenado sin coordenadas propias fuera del plano (R6). En un apoyo
    inclinado del plano XZ, ey′ = Y: Uy′ es un ``fix`` y Rx′ y Rz′ con Ry′ libre forman el
    complemento alineado (PROP-1), así que no se añade penalización.
    """
    added = (SupportDof.UY, SupportDof.RX, SupportDof.RZ)
    for node_id in case.model.nodes:
        current = case.model.support_at(node_id)
        if current is None:
            case.model.set_support(node_id, build_restraints(added), label="Fuera del plano")
            continue
        states = list(current.restraints)
        for dof in added:
            if states[dof.value] == FREE:
                states[dof.value] = FIXED
        case.model.set_support(node_id, tuple(states), basis=current.basis)


_D_NAMES: Final = ("Ux", "Uy", "Uz", "Rx", "Ry", "Rz")
_M_NAMES: Final = ("N", "Vy", "Vz", "T", "My", "Mz")


def _component_rows(
    names: Sequence[str],
    kinds: Sequence[QuantityKind],
    planar: Vector6 | None,
    spatial: Vector6 | None,
) -> list[Row]:
    """Filas 3D frente a plano de un ``Vector6``; fuera del plano, frente a 0.

    Una componente del plano que no es CALCULATED no se compara (no tiene valor); una que sí
    lo es se compara aunque el 3D no la publique (``computed`` None: no coincide), también si
    falta la entidad entera (``spatial`` None).
    """
    rows: list[Row] = []
    for index in range(6):
        value = None if spatial is None else spatial.components[index].value
        component = None if planar is None else planar.components[index]
        if component is not None and component.status is ComponentStatus.CALCULATED:
            assert component.value is not None  # CALCULATED siempre lleva valor.
            rows.append((names[index], PLANAR, kinds[index], component.value, value))
        elif index in _OUT_OF_PLANE:
            rows.append((names[index], OUT_OF_PLANE_ZERO, kinds[index], 0.0, value))
    return rows


def _cross_rows(planar: _Case, spatial: _Case) -> list[Row]:
    """Desplazamientos, reacciones globales y pI/pJ del 3D frente al plano, por etiqueta.

    Las reacciones en la base de un apoyo inclinado no entran por sí mismas: en el plano son
    una proyección con hipótesis y en 3D son exactas por GDL. No queda hueco: Fx y Fz globales
    (CALCULATED en el plano) se comparan y, por rotación, determinan R_x′ y R_z′; Fy, Mx y Mz
    se comparan con 0, y R_z′ entra además en las filas de fórmula.
    """
    flat, space = planar._res(), spatial._res()
    flat_nodes = {planar.labels[n.node_id]: n.displacement for n in flat.nodes}
    flat_reactions = {planar.labels[r.node_id]: r.global_ for r in flat.reactions}
    flat_members = {planar.labels[m.member_id]: m for m in flat.members}
    space_nodes = {spatial.labels[n.node_id]: n.displacement for n in space.nodes}
    space_reactions = {spatial.labels[r.node_id]: r.global_ for r in space.reactions}
    space_members = {spatial.labels[m.member_id]: m for m in space.members}
    rows: list[Row] = []
    # Unión de etiquetas: una entidad del plano que el 3D no publica da filas sin valor
    # (no coincide); una reacción solo del 3D (apoyo fuera del plano) se compara con 0.
    for label in sorted(flat_nodes.keys() | space_nodes.keys()):
        rows += _component_rows(
            [f"{name} {label}" for name in _D_NAMES],
            _U_KINDS,
            flat_nodes.get(label),
            space_nodes.get(label),
        )
    for label in sorted(flat_reactions.keys() | space_reactions.keys()):
        rows += _component_rows(
            [f"{name} {label}" for name in _R_NAMES],
            _F_KINDS,
            flat_reactions.get(label),
            space_reactions.get(label),
        )
    for label in sorted(flat_members.keys() | space_members.keys()):
        mine, theirs = flat_members.get(label), space_members.get(label)
        for end in ("pI", "pJ"):
            rows += _component_rows(
                [f"{end}_{name} {label}" for name in _M_NAMES],
                _F_KINDS,
                None if mine is None else (mine.p_i if end == "pI" else mine.p_j),
                None if theirs is None else (theirs.p_i if end == "pI" else theirs.p_j),
            )
    return rows


def _cross_scales(
    planar: _Case, rows: Sequence[Row], declared: Mapping[QuantityKind, float] | None
) -> dict[QuantityKind, float]:
    """Escalas de las filas 3D frente a plano (E2.6; revisión, m1).

    Las declaradas del caso (wL²/8, P·L) valen también aquí: si todos los momentos de
    referencia son ≈ 0 (extremos liberados), la escala no puede ser el ruido del plano.
    Un tipo sin escala (todas las traslaciones o todos los giros de referencia nulos) toma
    la acoplada del otro: L_máx·máx|θ| o máx|u|/L_máx (precedente DEV-2). Solo se usan
    valores de referencia (del plano) y la geometría, nunca valores calculados en 3D.
    """
    scales = dict(declared or {})
    largest = {
        kind: max((abs(row[3]) for row in rows if row[2] is kind), default=0.0)
        for kind in (DISP, ROT)
    }
    model = planar.model
    longest = max(
        (model.nodes[m.node_j].position - model.nodes[m.node_i].position).norm()
        for m in model.members.values()
    )
    if largest[DISP] == 0.0 and DISP not in scales and largest[ROT] > 0.0:
        scales[DISP] = longest * largest[ROT]
    if largest[ROT] == 0.0 and ROT not in scales and largest[DISP] > 0.0:
        scales[ROT] = largest[DISP] / longest
    return scales


def _run_cross_spec(spec: _Spec, timeout_s: float | None) -> SpatialReferenceOutcome:
    planar_case, planar = _planar_case(spec, timeout_s)
    case = _Case(spec.category)
    spec.build(case)
    _fix_out_of_plane(case)
    run, results, failure = _spatial_run(case, timeout_s)
    checks: tuple[ReferenceCheck, ...] = ()
    if results is not None and planar.results is not None:
        case.results = results
        # Fórmulas del plano sobre el 3D, exactas (el rodillo inclinado es exacto en 3D), y
        # 3D frente a plano con la tolerancia del plano (1e-6 donde este penaliza).
        checks = evaluate(spec.rows(case), TOL_EXACT, spec.scales)
        rows = _cross_rows(planar_case, case)
        checks += evaluate(rows, spec.tolerance, _cross_scales(planar_case, rows, spec.scales))
    return SpatialReferenceOutcome(
        spec.case_id,
        spec.title,
        spec.description,
        case.labels,
        run,
        results,
        failure,
        checks,
        (Text.of("benchmark.note_cross_planar", tolerance=spec.tolerance),),
        planar,
    )


def run_cross_planar(
    case_id: str, timeout_s: float | None = None
) -> tuple[SpatialReferenceOutcome, ...]:
    """Contraste D1 de un caso de ``CROSS_CASES``: fórmulas en 3D y 3D frente a plano."""
    if case_id not in CROSS_CASES:
        raise ValueError(f"caso de contraste desconocido: {case_id}")
    return tuple(_run_cross_spec(spec, timeout_s) for spec in SPECS[case_id])
