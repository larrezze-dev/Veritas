"""Referencias analíticas T-FEM-001…004 para la demostración (plan T-009, D1-A).

Cada caso construye su modelo plano XZ, lo analiza, normaliza los resultados (T-008) y los
compara con fórmulas cerradas de Euler-Bernoulli derivadas por cuerpo libre en el plan.
Las fórmulas usan E, b y h del caso, no las propiedades de sección del código.

Criterio por magnitud (revisión M1 del plan): |calculado − referencia| ≤ tol·max(|ref|,
S_dim), con S_dim el mayor |ref| de la misma magnitud dentro del caso. «Coincide» significa
solo eso: no valida la aplicación ni un modelo de usuario.

Los tests de referencia (``tests/test_fem_reference.py``) no usan este módulo; un test aparte
comprueba que sus referencias coinciden con los literales del plan.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError, ParamValue
from veritas.core.geometry.model import StructuralModel
from veritas.core.loads.load_case import LoadCategory
from veritas.core.materials.material import MaterialFamily
from veritas.core.presentation import QuantityKind
from veritas.core.supports.support import (
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
    analyze_planar_xz,
    normalize_planar_xz,
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

CASES: Final = ("T-FEM-001", "T-FEM-002", "T-FEM-003", "T-FEM-004")


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


def evaluate(rows: Sequence[Row], tolerance: float) -> tuple[ReferenceCheck, ...]:
    """Contrasta filas (magnitud, fórmula, tipo, referencia, calculado) con S_dim por tipo."""
    largest: dict[QuantityKind, float] = {}
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

    def __init__(self) -> None:
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
        self.load_case = self.model.add_load_case("Q", LoadCategory.VARIABLE)
        self.ids: dict[str, UUID] = {}
        self.results: PlanarResults | None = None

    def node(self, label: str, x: float) -> UUID:
        m = u.Quantity.of
        node = self.model.add_node(m(x, "m"), m(0, "m"), m(0, "m"), label=label)
        self.ids[label] = node.id
        return node.id

    def member(self, label: str, i: str, j: str) -> UUID:
        member = self.model.add_member(
            self.ids[i],
            self.ids[j],
            material=self.material.ref,
            section=self.section.ref,
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

    def _res(self) -> PlanarResults:
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


_BEAM: Final = {"span": SPAN, "load_kn": P / 1000}

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
}


def _run_spec(spec: _Spec, timeout_s: float | None) -> ReferenceOutcome:
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
        checks = evaluate(spec.rows(case), spec.tolerance)
        notes = tuple(spec.notes(case))
    return ReferenceOutcome(
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
