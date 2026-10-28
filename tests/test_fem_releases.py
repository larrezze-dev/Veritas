"""Extremos liberados y con resorte con OpenSeesPy real (plan T-010, D2-D4 y D6).

Fórmulas Euler–Bernoulli cerradas, independientes del código; E = 210 GPa, R 200×400
(``fem_support``). Tolerancia relativa 1e-9 frente a la escala declarada de cada magnitud
(revisión M4: un esperado 0 se compara con esa escala, nunca con 0 exacto). Los
resultados se buscan por nudo o barra: su orden sigue a los tags, no a la creación.
"""

from uuid import UUID

import pytest

from fem_support import E_PA, IY, Frame, inclined
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, Severity
from veritas.core.geometry.entities import Node
from veritas.core.supports.support import (
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
    roller_restraints,
)
from veritas.core.units import Quantity
from veritas.fem import (
    Component,
    ComponentStatus,
    MemberEndForces,
    PlanarResults,
    RunStatus,
    analyze_planar_xz,
    normalize_planar_xz,
)
from veritas.fem.adapters.planar_xz import translate_planar_xz

pytestmark = pytest.mark.solver

REL = 1e-9
L = 4.0
Q = 10_000.0  # N/m, hacia −Z
P = 10_000.0  # N, hacia −Z
EI = E_PA * IY
FZ, MY = 2, 4  # Índices en (Fx, Fy, Fz, Mx, My, Mz) y (Ux, Uy, Uz, Rx, Ry, Rz).

RELEASED_RY = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)


def spring_ry(k: float) -> EndConnection:
    return EndConnection.semi_rigid(Dof.RY, Quantity(k, Dof.RY.stiffness_dimension))


def close(value: float | None, expected: float, scale: float) -> None:
    assert value is not None
    assert abs(value - expected) <= REL * scale, (value, expected)


def solve(frame: Frame) -> PlanarResults:
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.SUCCEEDED, run.diagnostics
    return normalize_planar_xz(run, frame.model)


def reaction(results: PlanarResults, node: Node, index: int) -> Component:
    return next(r for r in results.reactions if r.node_id == node.id).global_.components[index]


def displacement(results: PlanarResults, node: Node, index: int) -> Component:
    return next(n for n in results.nodes if n.node_id == node.id).displacement.components[index]


def forces(results: PlanarResults, member_id: UUID) -> MemberEndForces:
    return next(m for m in results.members if m.member_id == member_id)


def end_moment(results: PlanarResults, member_id: UUID, end: MemberEnd) -> float:
    member = forces(results, member_id)
    value = (member.p_i if end is MemberEnd.I else member.p_j).components[MY].value
    assert value is not None
    return abs(value)


class Propped:
    """REF-01: empotramiento en N1, apoyo deslizante (Uz) en N2, q hacia abajo."""

    def __init__(self, end_i: EndConnection) -> None:
        self.frame = Frame()
        self.a, self.b = self.frame.node(0, 0), self.frame.node(L, 0)
        self.member = self.frame.member(self.a, self.b)
        self.frame.support(self.a, fixed_restraints())
        self.frame.support(self.b, build_restraints([SupportDof.UZ]))
        self.frame.model.add_member_uniform_load(
            self.frame.case_id, self.member.id, qz=Quantity.of(-Q, "N/m")
        )
        self.frame.model.set_member_end(self.member.id, MemberEnd.I, end_i)
        self.results = solve(self.frame)


@pytest.mark.parametrize("ratio", [1e-6, 0.1, 1.0, 10.0, 100.0, 1e6])
def test_ref01_rotational_spring(ratio: float) -> None:
    k = ratio * EI / L
    case = Propped(spring_ry(k))
    m_i = (Q * L**2 / 8) / (1 + 3 * EI / (k * L))
    scale = Q * L**2 / 8
    close(end_moment(case.results, case.member.id, MemberEnd.I), m_i, scale)
    close(end_moment(case.results, case.member.id, MemberEnd.J), 0.0, scale)
    close(reaction(case.results, case.a, FZ).value, Q * L / 2 + m_i / L, Q * L)
    close(reaction(case.results, case.b, FZ).value, Q * L / 2 - m_i / L, Q * L)
    close(abs(reaction(case.results, case.a, MY).value or 0.0), m_i, scale)


def test_ref01_released_is_simply_supported() -> None:  # T-CON-002/003, límite liberado
    case = Propped(RELEASED_RY)
    scale = Q * L**2 / 8
    close(end_moment(case.results, case.member.id, MemberEnd.I), 0.0, scale)
    close(reaction(case.results, case.a, FZ).value, Q * L / 2, Q * L)
    close(reaction(case.results, case.b, FZ).value, Q * L / 2, Q * L)
    close(reaction(case.results, case.a, MY).value, 0.0, scale)
    # El nudo N1 sigue empotrado (su giro es 0) y el extremo J sigue rígido.
    assert displacement(case.results, case.a, MY).value == 0.0
    assert case.frame.model.member(case.member.id).end_j == EndConnection.rigid()


def test_ref01_spring_is_monotonic_between_limits() -> None:  # T-CON-003
    moments = []
    for ratio in (0.1, 1.0, 10.0, 100.0):
        case = Propped(spring_ry(ratio * EI / L))
        moments.append(end_moment(case.results, case.member.id, MemberEnd.I))
    assert moments == sorted(moments)
    assert moments[0] > 0.0
    assert moments[-1] < Q * L**2 / 8


class Cantilever:
    """REF-02: voladizo con resorte (o resorte de apoyo) en N1 y P en la punta N2."""

    def __init__(self, k: float, *, support_spring: bool = False) -> None:
        self.frame = Frame()
        self.a, self.b = self.frame.node(0, 0), self.frame.node(L, 0)
        member = self.frame.member(self.a, self.b)
        if support_spring:
            stiffness = {SupportDof.RY: Quantity(k, SupportDof.RY.stiffness_dimension)}
            translations = [SupportDof.UX, SupportDof.UY, SupportDof.UZ]
            self.frame.support(self.a, build_restraints(translations, stiffness))
        else:
            self.frame.support(self.a, fixed_restraints())
            self.frame.model.set_member_end(member.id, MemberEnd.I, spring_ry(k))
        self.frame.nodal(self.b, fz=-P)
        self.results = solve(self.frame)


@pytest.mark.parametrize("ratio", [0.5, 5.0, 50.0])
def test_ref02_cantilever_root_spring(ratio: float) -> None:
    k = ratio * EI / L
    case = Cantilever(k)
    deflection = P * L**3 / (3 * EI) + P * L**2 / k
    rotation = P * L**2 / (2 * EI) + P * L / k
    close(displacement(case.results, case.b, FZ).value, -deflection, deflection)
    close(abs(displacement(case.results, case.b, MY).value or 0.0), rotation, rotation)
    close(abs(reaction(case.results, case.a, MY).value or 0.0), P * L, P * L)
    close(reaction(case.results, case.a, FZ).value, P, P)


def test_ref02_matches_support_rotational_spring() -> None:
    # Comprobación cruzada (revisión m1): resorte de extremo sobre empotramiento =
    # resorte de giro de apoyo sobre apoyo articulado.
    k = 3.0 * EI / L
    end_spring, support_spring = Cantilever(k), Cantilever(k, support_spring=True)
    for index in (FZ, MY):
        expected = displacement(support_spring.results, support_spring.b, index).value
        assert expected is not None
        close(displacement(end_spring.results, end_spring.b, index).value, expected, abs(expected))


class Hinge:
    """REF-03 / mecanismo: dos barras colineales con rótula en N2 (J de B1 liberado)."""

    def __init__(self, *, mechanism: bool) -> None:
        self.frame = Frame()
        self.n1, self.n2, self.n3 = (self.frame.node(x, 0) for x in (0, L, 2 * L))
        self.b1 = self.frame.member(self.n1, self.n2)
        self.b2 = self.frame.member(self.n2, self.n3)
        if mechanism:
            self.frame.support(self.n1, pinned_restraints())
            self.frame.support(self.n3, roller_restraints())
        else:
            self.frame.support(self.n1, fixed_restraints())
            self.frame.support(self.n3, build_restraints([SupportDof.UZ]))
        self.frame.nodal(self.n2, fz=-P)
        self.frame.model.set_member_end(self.b1.id, MemberEnd.J, RELEASED_RY)


def test_ref03_internal_hinge() -> None:
    case = Hinge(mechanism=False)
    results = solve(case.frame)
    close(reaction(results, case.n1, FZ).value, P, P)
    close(reaction(results, case.n3, FZ).value, 0.0, P)
    close(abs(reaction(results, case.n1, MY).value or 0.0), P * L, P * L)
    deflection = P * L**3 / (3 * EI)
    close(displacement(results, case.n2, FZ).value, -deflection, deflection)
    # La rótula está en J de B1; el nudo N2 gira con B2 (extremo I rígido).
    assert displacement(results, case.n2, MY).status is ComponentStatus.CALCULATED
    close(end_moment(results, case.b1.id, MemberEnd.J), 0.0, P * L)
    close(end_moment(results, case.b2.id, MemberEnd.I), 0.0, P * L)


def test_real_mechanism_is_singular_and_located() -> None:  # T-CON-004, revisión M5
    case = Hinge(mechanism=True)
    run = analyze_planar_xz(case.frame.model, case.frame.config())
    assert run.status is RunStatus.FAILED
    assert run.raw is None
    errors = [d for d in run.diagnostics if d.severity is Severity.ERROR]
    assert len(errors) == 1
    assert errors[0].code is DiagnosticCode.ANALYSIS_SINGULAR
    assert errors[0].message_key in {"fem.singular", "fem.mechanism_member_end"}
    assert errors[0].location is not None
    assert errors[0].location.entity_id in {case.n1.id, case.n2.id, case.n3.id, case.b1.id}


class PinnedOnPin:
    """Barra con I liberado sobre apoyo articulado: giro huérfano en N1."""

    def __init__(self, moment: float) -> None:
        self.frame = Frame()
        self.a, self.b = self.frame.node(0, 0), self.frame.node(L, 0)
        member = self.frame.member(self.a, self.b)
        self.frame.support(self.a, pinned_restraints())
        self.frame.support(self.b, roller_restraints())
        self.frame.model.add_member_uniform_load(
            self.frame.case_id, member.id, qz=Quantity.of(-Q, "N/m")
        )
        if moment:
            self.frame.nodal(self.a, my=moment)
        self.frame.model.set_member_end(member.id, MemberEnd.I, RELEASED_RY)


def test_orphan_rotation_restrained_with_warning() -> None:  # D3-A
    case = PinnedOnPin(0.0)
    run = analyze_planar_xz(case.frame.model, case.frame.config())
    assert run.status is RunStatus.SUCCEEDED
    warnings = [d for d in run.diagnostics if d.message_key == "fem.orphan_rotation_restrained"]
    assert len(warnings) == 1
    assert warnings[0].location is not None
    assert warnings[0].location.entity_id == case.a.id
    assert "fem.hypothesis_orphan_rotation" in run.hypotheses
    results = normalize_planar_xz(run, case.frame.model)
    assert displacement(results, case.a, MY).status is ComponentStatus.NOT_APPLICABLE
    assert displacement(results, case.b, MY).status is ComponentStatus.CALCULATED
    assert reaction(results, case.a, MY).status is ComponentStatus.NOT_APPLICABLE
    close(reaction(results, case.a, FZ).value, Q * L / 2, Q * L)
    close(reaction(results, case.b, FZ).value, Q * L / 2, Q * L)


def test_orphan_rotation_with_moment_is_mechanism() -> None:
    case = PinnedOnPin(500.0)
    run = analyze_planar_xz(case.frame.model, case.frame.config())
    assert run.status is RunStatus.FAILED
    assert run.diagnostics[0].code is DiagnosticCode.ANALYSIS_SINGULAR
    assert run.diagnostics[0].message_key == "fem.mechanism_orphan_rotation"
    assert run.diagnostics[0].location is not None
    assert run.diagnostics[0].location.entity_id == case.a.id


def test_released_end_on_inclined_penalty_support() -> None:  # revisión M3 (sonda 5)
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.support(b, roller_restraints(SupportDof.UZ), basis=inclined(30.0))
    frame.model.add_member_uniform_load(frame.case_id, member.id, qz=Quantity.of(-Q, "N/m"))
    frame.model.set_member_end(member.id, MemberEnd.J, spring_ry(EI / L))
    results = solve(frame)  # Equilibrio global, de nudos y de barras comprobado.
    total = sum(r.global_.components[FZ].value or 0.0 for r in results.reactions)
    assert abs(total - Q * L) <= 1e-6 * Q * L


def test_translator_end_link_tags_are_deterministic() -> None:
    case = Propped(spring_ry(EI / L))
    request = translate_planar_xz(case.frame.model, case.frame.config()).request
    (link,) = request.end_links
    assert link.end == "I"
    assert link.aux == len(request.nodes) + len(request.springs) + 1
    assert link.tag == len(request.beams) + len(request.springs) + 1
    assert link.stiffness == pytest.approx(EI / L)
