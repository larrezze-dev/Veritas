"""Gates G-N1…G-N7 de la normalización con OpenSeesPy real (plan T-008).

Valores esperados a mano con ADR-001 §2, independientes de la Q del código (revisión B2):
L = 2 m, P = 1000 N, empotramiento en I salvo indicación. Tolerancia relativa 1e-9; 1e-6
con penalización; un esperado 0 admite 1e-9·|P| o 1e-9·|P·L| (revisión M2). Verifica la
transformación, no la formulación de la barra (T-009).
"""

import math
from collections.abc import Callable

import pytest

from fem_support import Frame, inclined
from test_fem_singularity import LEGITIMATE
from veritas.core.loads.loads import LoadBasis
from veritas.core.supports.support import (
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
    roller_restraints,
)
from veritas.core.units import Quantity
from veritas.fem import (
    ComponentStatus,
    MemberEndForces,
    PlanarResults,
    ReactionSource,
    SupportReaction,
    analyze_planar_xz,
    normalize_planar_xz,
)
from veritas.fem.results import normalized

pytestmark = pytest.mark.solver

REL = 1e-9
L = 2.0
P = 1000.0
Q = 1000.0
NC = ComponentStatus.NOT_CALCULATED
NA = ComponentStatus.NOT_APPLICABLE


def results(frame: Frame) -> PlanarResults:
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.raw is not None, run.diagnostics
    return normalize_planar_xz(run, frame.model)


def approx(value: float | None, expected: float, scale: float = P, rel: float = REL) -> bool:
    assert value is not None
    return value == pytest.approx(expected, rel=rel, abs=rel * scale)


def check(
    values: tuple[float | None, ...], expected: dict[int, float], scale: float = P, rel: float = REL
) -> None:
    """Componentes indicadas (índice ADR-001 → valor); momentos con escala P·L."""
    for index, value in expected.items():
        assert approx(values[index], value, scale * (L if index >= 3 else 1.0), rel), (
            index,
            values[index],
            value,
        )


def member(res: PlanarResults, index: int = 0) -> MemberEndForces:
    return res.members[index]


def reaction(res: PlanarResults, node_id: object) -> SupportReaction:
    return next(r for r in res.reactions if r.node_id == node_id)


def cantilever(
    end: tuple[float, float], *, alpha: float | None = None
) -> tuple[Frame, object, object]:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(*end)
    frame.member(a, b, alpha=alpha)
    frame.support(a, fixed_restraints())
    return frame, a, b


def test_gn1_tension() -> None:
    frame, a, b = cantilever((L, 0))
    frame.nodal(b, fx=P)  # type: ignore[arg-type]
    res = results(frame)
    check(member(res).p_i.values(), {0: -P})
    check(member(res).p_j.values(), {0: P})
    check(member(res).section_start.values(), {0: P})
    check(reaction(res, a.id).global_.values(), {0: -P})  # type: ignore[attr-defined]


def test_gn2_cantilever_tip_load() -> None:
    frame, a, b = cantilever((L, 0))
    frame.nodal(b, fz=-P)  # type: ignore[arg-type]
    res = results(frame)
    m = member(res)
    check(reaction(res, a.id).global_.values(), {2: P, 4: -P * L})  # type: ignore[attr-defined]
    check(m.p_i.values(), {0: 0.0, 2: P, 4: -P * L})
    check(m.p_j.values(), {2: -P, 4: 0.0})
    check(m.section_start.values(), {2: -P, 4: P * L})
    check(m.section_end.values(), {2: -P, 4: 0.0})
    assert [c.status for c in m.p_i.components[1::2]] == [NC, NC, NC]  # Fy, Mx, Mz


def test_gn3a_cantilever_uniform_load_includes_fixed_end_forces() -> None:
    frame, _a, _b = cantilever((L, 0))
    (member_id,) = frame.model.members
    frame.model.add_member_uniform_load(frame.case_id, member_id, qz=Quantity.of(-Q, "N/m"))
    m = member(results(frame))
    check(m.p_i.values(), {2: Q * L, 4: -Q * L * L / 2})
    check(m.p_j.values(), {0: 0.0, 2: 0.0, 4: 0.0})


def test_gn3b_fixed_fixed_uniform_load() -> None:
    # Dos barras: una biempotrada de un solo elemento no tiene GDL libres (límite de T-007).
    frame = Frame()
    a, c, b = frame.node(0, 0), frame.node(L / 2, 0), frame.node(L, 0)
    first, second = frame.member(a, c), frame.member(c, b)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    for item in (first, second):
        frame.model.add_member_uniform_load(frame.case_id, item.id, qz=Quantity.of(-Q, "N/m"))
    res = results(frame)
    by_id = {m.member_id: m for m in res.members}
    end_moment = Q * L**2 / 12
    check(by_id[first.id].p_i.values(), {2: Q * L / 2, 4: -end_moment})
    check(by_id[second.id].p_j.values(), {2: Q * L / 2, 4: end_moment})
    check(by_id[first.id].section_start.values(), {4: end_moment})
    check(by_id[second.id].section_end.values(), {4: end_moment})
    # Centro del vano: momento positivo de vano qL²/24 con signo opuesto al de los extremos.
    check(by_id[first.id].section_end.values(), {4: -Q * L**2 / 24})


def test_gn3c_inclined_member_axial_fixed_end_force() -> None:
    angle = math.radians(30)
    frame, _a, _b = cantilever((L * math.cos(angle), L * math.sin(angle)))
    (member_id,) = frame.model.members
    frame.model.add_member_uniform_load(frame.case_id, member_id, qz=Quantity.of(-Q, "N/m"))
    m = member(results(frame))
    # Peso Q·L hacia −Z: componente axial −Q·L·sin30 que el nudo I compensa con +Q·L·sin30.
    check(m.p_i.values(), {0: Q * L * math.sin(angle), 2: Q * L * math.cos(angle)})
    check(m.p_i.values(), {4: -Q * L * L / 2 * math.cos(angle)})
    check(m.p_j.values(), {0: 0.0, 2: 0.0, 4: 0.0})


def test_gn4_load_on_supported_node() -> None:
    frame, a, b = cantilever((L, 0))
    frame.nodal(a, fz=-300.0)  # type: ignore[arg-type]
    frame.nodal(b, fz=-P)  # type: ignore[arg-type]
    check(reaction(results(frame), a.id).global_.values(), {2: P + 300.0})  # type: ignore[attr-defined]


ORIENTATIONS: dict[
    str,
    tuple[tuple[float, float], float | None, dict[str, float], dict[int, float], dict[int, float]],
] = {
    # nombre: (J, alfa, carga en J, reacción global esperada, pI local esperado)
    "gn5a_minus_x": ((-L, 0), None, {"fz": -P}, {2: P, 4: P * L}, {2: P, 4: -P * L}),
    "gn5b_vertical_up": ((0, L), None, {"fx": P}, {0: -P, 4: -P * L}, {2: P, 4: -P * L}),
    "gn5c_vertical_down": ((0, -L), None, {"fx": P}, {0: -P, 4: P * L}, {2: P, 4: -P * L}),
    "gn5d_alpha_pi_2": ((L, 0), math.pi / 2, {"fz": -P}, {2: P, 4: -P * L}, {1: P, 5: P * L}),
    "gn5e_alpha_minus_pi_2": (
        (L, 0),
        -math.pi / 2,
        {"fz": -P},
        {2: P, 4: -P * L},
        {1: -P, 5: -P * L},
    ),
    "gn5f_alpha_pi": ((L, 0), math.pi, {"fz": -P}, {2: P, 4: -P * L}, {2: -P, 4: P * L}),
}


@pytest.mark.parametrize("name", list(ORIENTATIONS))
def test_gn5_orientations(name: str) -> None:
    end, alpha, load, expected_reaction, expected_p_i = ORIENTATIONS[name]
    frame, a, b = cantilever(end, alpha=alpha)
    frame.nodal(b, **load)  # type: ignore[arg-type]
    res = results(frame)
    check(reaction(res, a.id).global_.values(), expected_reaction)  # type: ignore[attr-defined]
    m = member(res)
    check(m.p_i.values(), expected_p_i)
    in_plane = {0, *expected_p_i}
    for index, component in enumerate(m.p_i.components):
        assert (component.status is NC) == (index not in in_plane), (index, component)


def test_gn5d_iz_branch_section_forces() -> None:
    frame, _a, b = cantilever((L, 0), alpha=math.pi / 2)
    frame.nodal(b, fz=-P)  # type: ignore[arg-type]
    m = member(results(frame))
    assert m.inertia_axis == "Iz"
    check(m.section_start.values(), {1: -P, 5: -P * L})
    assert m.section_start.components[4].status is NC  # My


def test_gn6_vertical_spring() -> None:
    k = 1e6
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(b, build_restraints(springs={SupportDof.UZ: Quantity.of(k, "N/m")}))
    frame.nodal(b, fz=-P)
    res = results(frame)
    spring = reaction(res, b.id)
    check(spring.global_.values(), {2: P})
    assert spring.global_.components[0].status is NA
    assert spring.sources == ((SupportDof.UZ, ReactionSource.SPRING),)
    node = next(n for n in res.nodes if n.node_id == b.id)
    check(node.displacement.values(), {2: -P / k}, scale=P / k)


def test_gn7_inclined_roller_by_penalty() -> None:
    angle = math.radians(30)
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    beam = frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(b, roller_restraints(), inclined(30.0))  # Uz′ fijo, x′ a 30°
    frame.model.add_member_uniform_load(frame.case_id, beam.id, qz=Quantity.of(-Q, "N/m"))
    res = results(frame)
    roller = reaction(res, b.id)
    normal = Q * L / (2 * math.cos(angle))
    assert roller.support_basis is not None
    local = roller.support_basis.values()
    assert approx(local[2], normal, Q * L, rel=1e-6)
    assert roller.support_basis.components[0].status is NA
    assert roller.sources == ((SupportDof.UZ, ReactionSource.PENALTY),)
    # Proyección numérica sobre ex′ (libre) ≈ 0, con la tolerancia de O6.
    force = roller.global_.values()
    assert force[0] is not None
    assert force[2] is not None
    along_x = force[0] * math.cos(angle) + force[2] * math.sin(angle)
    assert abs(along_x) <= 1e-6 * normal
    # z′ = (−sin30, cos30): R_X(B) = −R·sin30, que la articulación equilibra.
    check(force, {0: -normal * math.sin(angle), 2: Q * L / 2}, scale=Q * L, rel=1e-6)
    check(
        reaction(res, a.id).global_.values(), {0: normal * math.sin(angle)}, scale=Q * L, rel=1e-6
    )
    assert "fem.hypothesis_support_projection" in res.hypotheses


def test_inclined_springs_report_sources_per_support_dof() -> None:
    """Resortes inclinados más Ry FIXED (revisión M3): orígenes y valores a mano."""
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    k1, k2 = 1e6, 2e6
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    frame.member(a, b)
    springs = build_restraints(
        fixed=(SupportDof.RY,),
        springs={SupportDof.UX: Quantity.of(k1, "N/m"), SupportDof.UZ: Quantity.of(k2, "N/m")},
    )
    frame.support(a, springs, inclined(30.0))  # B libre: voladizo que se traslada como sólido.
    frame.nodal(a, fx=P * c, fz=P * s)  # Solo según x′: la barra no toma carga.
    rea = reaction(results(frame), a.id)
    assert rea.sources == (
        (SupportDof.UX, ReactionSource.SPRING),
        (SupportDof.UZ, ReactionSource.SPRING),
        (SupportDof.RY, ReactionSource.FIX),
    )
    assert rea.support_basis is not None
    check(rea.support_basis.values(), {0: -P, 2: 0.0, 4: 0.0})
    check(rea.global_.values(), {0: -P * c, 2: -P * s, 4: 0.0})


def test_global_support_fixed_and_spring() -> None:
    """GLOBAL con Ux FIXED y Uz SPRING (M3): X y Z calculadas, orígenes FIX y SPRING."""
    k = 1e6
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(
        b, build_restraints(fixed=(SupportDof.UX,), springs={SupportDof.UZ: Quantity.of(k, "N/m")})
    )
    frame.nodal(b, fx=-P, fz=-P)
    rea = reaction(results(frame), b.id)
    assert rea.sources == (
        (SupportDof.UX, ReactionSource.FIX),
        (SupportDof.UZ, ReactionSource.SPRING),
    )
    # La articulación A no toma X: la barra no tiene axil si el apoyo B fija Ux.
    rea_x = rea.global_.values()[0]
    reaction_a = reaction(results(frame), a.id).global_.values()[0]
    assert rea_x is not None
    assert reaction_a is not None
    assert rea_x + reaction_a == pytest.approx(P, rel=REL)
    check(rea.global_.values(), {2: P})
    assert rea.global_.components[4].status is NA
    assert rea.support_basis is None
    assert rea.axes is None


def test_rotational_spring_only() -> None:
    """Apoyo con solo resorte de giro (M3): X y Z no aplicables; My = −k·Ry."""
    k = 3e5
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(b, build_restraints(springs={SupportDof.RY: Quantity.of(k, "N*m/rad")}))
    frame.nodal(b, fz=-P)
    # El resorte de giro solo no da estabilidad a A-B: un tercer nudo con rodillo la completa.
    c = frame.node(2 * L, 0)
    frame.member(b, c)
    frame.support(c, roller_restraints())
    res = results(frame)
    rea = reaction(res, b.id)
    assert [x.status for x in rea.global_.components[0:5:2]] == [NA, NA, ComponentStatus.CALCULATED]
    assert rea.sources == ((SupportDof.RY, ReactionSource.SPRING),)
    rotation = next(n for n in res.nodes if n.node_id == b.id).displacement.values()[4]
    assert rotation is not None
    check(rea.global_.values(), {4: -k * rotation})


@pytest.mark.parametrize("angle", [30.0, 0.0])
def test_inclined_support_both_translations_fixed(angle: float) -> None:
    """Inclinado con Ux′ y Uz′ FIXED (fix exacto) o alineado a 0° (M3): FIX y proyección."""
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    frame.member(a, b)
    frame.support(a, build_restraints(fixed=(SupportDof.UX, SupportDof.UZ)), inclined(angle))
    frame.support(b, roller_restraints())
    frame.nodal(a, fx=-P)  # Toda la carga horizontal va a A: R = (+P, 0) global.
    rea = reaction(results(frame), a.id)
    assert rea.sources == (
        (SupportDof.UX, ReactionSource.FIX),
        (SupportDof.UZ, ReactionSource.FIX),
    )
    check(rea.global_.values(), {0: P, 2: 0.0})
    assert rea.support_basis is not None
    # x′ = (c, s), z′ = (−s, c): R′ = (P·c, −P·s).
    check(rea.support_basis.values(), {0: P * c, 2: -P * s})


def _with_self_weight(frame: Frame) -> None:
    frame.model.set_self_weight(frame.case_id)


def _with_local_load(frame: Frame) -> None:
    for member_id in frame.model.members:
        frame.model.add_member_uniform_load(
            frame.case_id,
            member_id,
            basis=LoadBasis.LOCAL,
            qx=Quantity.of(100.0, "N/m"),
            qz=Quantity.of(-500.0, "N/m"),
        )


def _with_nodal_moment(frame: Frame) -> None:
    node = max(frame.model.nodes.values(), key=lambda n: (n.position.x, n.position.z))
    frame.nodal(node, my=750.0)


LOADS: dict[str, Callable[[Frame], None]] = {
    "solo nodal": lambda _frame: None,
    "peso propio": _with_self_weight,
    "carga LOCAL": _with_local_load,
    "momento nodal": _with_nodal_moment,
}
# Residuo/cota máximo medido: 0,13 en 8 × 52 corridas (L/h = 1000 girada, peso propio). El
# orden de los tags depende de los UUID, así que el redondeo varía entre corridas.
D7_MARGIN = 0.25


@pytest.mark.parametrize("load", list(LOADS))
@pytest.mark.parametrize("name", list(LEGITIMATE))
def test_d7_has_margin_on_legitimate_battery(
    name: str, load: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D7 (revisión M1): sin falso fallo y con margen residuo/cota ≤ 0,25 en cada comprobación."""
    ratios: list[float] = []
    original = normalized._Normalizer._require

    def spy(
        self: normalized._Normalizer,
        where: str,
        entity: object,
        force: tuple[float, float],
        moment: tuple[float, float],
    ) -> None:
        for residual, limit in (force, moment):
            ratios.append(residual / limit if limit else (0.0 if residual == 0 else math.inf))
        original(self, where, entity, force, moment)  # type: ignore[arg-type]

    monkeypatch.setattr(normalized._Normalizer, "_require", spy)
    frame = Frame()
    LEGITIMATE[name](frame)
    LOADS[load](frame)
    res = results(frame)
    assert res.members
    assert max(ratios) <= D7_MARGIN, (name, load, max(ratios))
