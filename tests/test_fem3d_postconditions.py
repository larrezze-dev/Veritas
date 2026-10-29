"""Controles de la solución espacial (plan T-018, pieza 5a; D3 y D6).

Las corridas reales dan la (petición, respuesta) de referencia; los casos negativos la
manipulan a mano y llaman a cada control sin volver al solver. Cada control no superado da
su clave; la batería legítima (contraste, penalización y Obs. 8) pasa con margen.
"""

import dataclasses
import math
import sys
from collections.abc import Callable
from functools import cache

import pytest

import test_fem3d_singularity as cases
from fem_support import Frame
from veritas.core.supports.support import build_restraints, fixed_restraints
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.postconditions import (
    CHAIN_ALIAS_FACTOR,
    CONTROL_CHAIN_ALIAS,
    CONTROL_CHAIN_LINK,
    CONTROL_CHAIN_NORMAL,
    CONTROL_CHAIN_SUPPORT_FORCE,
    CONTROL_CHAIN_SUPPORT_MOMENT,
    chain_checks,
    chain_deviations,
    displacements,
    equilibrium_residual,
    member_checks,
    node_reactions,
    penalty_check,
    penalty_deviations,
    residual_check,
    solution_checks,
    support_deviations,
    translation_scale,
)
from veritas.fem.spatial.protocol import SpatialRequest, SpatialWorkerResponse
from veritas.fem.spatial.stiffness import spring_direction
from veritas.fem.spatial.translator import translate_spatial

pytestmark = pytest.mark.solver

EPS = sys.float_info.epsilon
Pair = tuple[SpatialRequest, SpatialWorkerResponse, SpatialAnalysisConfiguration]


def _pure_torsion() -> Frame:
    """Torsión pura con dos cadenas en el extremo: la reacción de las cadenas es ruido."""
    frame = Frame()
    a = frame.node(0.0, 0.0)
    b = cases.along(frame, a, cases.OBLIQUE, 2.0)
    member = frame.member(a, b, alpha=0.37)
    frame.support(a, fixed_restraints())
    frame.support(b, build_restraints((cases.UX, cases.UY)), cases.PENALTY_BASIS)
    ex = frame.model.axes(member.id).ex.components()
    frame.nodal(b, mx=1000.0 * ex[0], my=1000.0 * ex[1], mz=1000.0 * ex[2])
    return frame


def _roller_in_plane_load() -> Frame:
    """Rodillo inclinado con la carga del extremo perpendicular a su normal."""
    frame = cases.oblique_cantilever(2.0)
    tip = cases.tip(frame)
    frame.support(tip, build_restraints((cases.UX,)), cases.PENALTY_BASIS)
    _nx, ny, nz = cases.NORMAL
    size = math.hypot(ny, nz)
    frame.nodal(tip, fy=1000.0 * nz / size, fz=-1000.0 * ny / size)
    return frame


BATTERY: list[tuple[str, Callable[[], Frame]]] = [
    *cases.CONTRAST,
    *cases.PENALTY,
    ("cadena larga rodillo", lambda: cases._long_chain(2000.0, (cases.UX,))),
    ("cadena larga deslizador", lambda: cases._long_chain(2000.0, (cases.UX, cases.UY))),
    ("rodillo L/h=5000", lambda: cases._roller_frame(0.4 * 5000)),
    ("torsión pura con dos rodillos", _pure_torsion),
    ("rodillo con carga en su plano", _roller_in_plane_load),
]
NAMES = [name for name, _ in BATTERY]
# Casos con cadena para el control de R (D-a); la cota de alias tiene su propia lista.
CHAIN_NAMES = [n for n in NAMES if "cadena" in n or "deslizador" in n or "rodillo" in n]


@cache
def solved(name: str, penalty_factor: float = 1e8) -> Pair:
    build = dict(BATTERY)[name]
    frame = build()
    config = SpatialAnalysisConfiguration(frame.case_id, penalty_factor=penalty_factor)
    request = translate_spatial(frame.model, config).request
    return request, cases._run(request), config


def replace_node(
    response: SpatialWorkerResponse, tag: int, change: Callable[[list[float]], None]
) -> SpatialWorkerResponse:
    nodes = []
    for node_tag, disp, reaction in response.node_results:
        values = list(disp)
        if node_tag == tag:
            change(values)
        a, b, c, d, e, f = values
        nodes.append((node_tag, (a, b, c, d, e, f), reaction))
    return dataclasses.replace(response, node_results=tuple(nodes))


# --- Batería legítima ---------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_legitimate_battery_passes_every_control(name: str) -> None:
    request, response, config = solved(name)
    assert equilibrium_residual(request, response, config).ok
    assert solution_checks(request, response, config) is None


@pytest.mark.parametrize("name", [n for n in NAMES if "cadena" in n or "deslizador" in n])
def test_chain_alias_margin(name: str) -> None:
    """D3: c medida ≤ 1,4 en la batería; c_alias = 1e3 deja un margen de al menos 10×."""
    request, response, _ = solved(name)
    scale = EPS * translation_scale(displacements(response))
    worst = max(value for _, _, value in chain_deviations(request, response))
    assert worst <= CHAIN_ALIAS_FACTOR / 10 * scale


@pytest.mark.parametrize("name", CHAIN_NAMES)
def test_support_reaction_margin(name: str) -> None:
    """Hecho 8 (D-a): R da 0 en fuerzas y ≤ 0,34·eps·escala en momentos (medido, también en
    torsión pura y con la carga en el plano del rodillo); c_alias deja al menos 100×."""
    request, response, _ = solved(name)
    deviations = list(support_deviations(request, response))
    assert len(deviations) == 2 * len(request.chains)
    for _tag, control, value, scale in deviations:
        assert scale > 0.0
        if control == CONTROL_CHAIN_SUPPORT_FORCE:
            assert value == 0.0
        assert value <= CHAIN_ALIAS_FACTOR / 100 * EPS * scale


def test_chain_deviations_are_indexed_by_chain_tag() -> None:
    """Dos cadenas en el mismo nudo: cada desviación lleva el tag de su cadena."""
    request, response, _ = solved("deslizador con dos cadenas")
    tags = [chain.tag for chain in request.chains]
    assert len({chain.node for chain in request.chains}) == 1
    assert [tag for tag, _c, _v in chain_deviations(request, response)] == [
        tag for tag in tags for _ in range(3)
    ]
    assert [tag for tag, *_ in support_deviations(request, response)] == [
        tag for tag in tags for _ in range(2)
    ]


# --- Reacciones brutas --------------------------------------------------------------------


def test_node_reaction_is_projected_on_exact_constraints() -> None:
    """Fix en Uy y cadena con normal en XZ: la reacción queda en span(Y, n), sin giros."""
    request, response, _ = solved("fix y cadena en el mismo nudo")
    chain = request.chains[0]
    reaction = node_reactions(request, response)[chain.node]
    assert reaction is not None
    raw = dict((tag, r) for tag, _d, r in response.node_results)[chain.node]
    normal = chain.normal
    # Componente libre (Y × n) nula; Uy y la normal, las de nodeReaction.
    free = (normal[2], 0.0, -normal[0])
    size = max(abs(v) for v in raw[:3])
    assert abs(sum(a * b for a, b in zip(reaction[:3], free, strict=True))) <= 1e3 * EPS * size
    assert reaction[1] == pytest.approx(raw[1], abs=1e-12 * max(map(abs, raw)))
    assert reaction[3:] == (0.0, 0.0, 0.0)
    unrestrained = [tag for tag, r in node_reactions(request, response).items() if r is None]
    assert unrestrained == []  # Raíz empotrada y extremo con cadena.


def test_free_node_has_no_reaction() -> None:
    request, response, _ = solved("pórtico con peso propio")
    reactions = node_reactions(request, response)
    fixed = {fix.node for fix in request.fixes}
    assert {tag for tag, r in reactions.items() if r is not None} == fixed


# --- Respuestas manipuladas ---------------------------------------------------------------


def test_member_force_tampered_fails() -> None:
    request, response, config = solved("voladizo oblicuo")
    tag, force = response.beam_forces[0]
    tampered = (tag, (force[0] * (1 + 1e-6) + 1e-3, *force[1:]))
    response = dataclasses.replace(response, beam_forces=(tampered,))
    failure = member_checks(request, response, config)
    assert failure is not None
    assert failure.key == "fem.member_force_check_failed"
    assert failure.params["component"] == 1
    assert failure.member_id == request.beams[0].entity_id


def test_member_force_nan_fails() -> None:
    request, response, config = solved("voladizo oblicuo")
    tag, force = response.beam_forces[0]
    poisoned = (*force[:5], math.nan, *force[6:])
    response = dataclasses.replace(response, beam_forces=((tag, poisoned),))
    failure = member_checks(request, response, config)
    assert failure is not None
    assert failure.key == "fem.member_force_check_failed"


@pytest.mark.parametrize(
    ("field", "control"),
    [("c_disp", CONTROL_CHAIN_ALIAS), ("r_disp", CONTROL_CHAIN_LINK)],
)
def test_chain_tampered_fails(field: str, control: str) -> None:
    request, response, _ = solved("deslizador con dos cadenas")
    item = response.chain_results[0]
    values = list(getattr(item, field))
    index = 0 if field == "c_disp" else 3
    values[index] += 1e-9 * max(1.0, abs(values[index]))
    a, b, c, d, e, f = values
    tampered = dataclasses.replace(item, **{field: (a, b, c, d, e, f)})  # type: ignore[arg-type]
    response = dataclasses.replace(response, chain_results=(tampered, *response.chain_results[1:]))
    failure = chain_checks(request, response)
    assert failure is not None
    assert failure.key == "fem3d.chain_postcondition_failed"
    assert failure.params["control"] == control


@pytest.mark.parametrize(
    ("index", "control"),
    [(1, CONTROL_CHAIN_SUPPORT_FORCE), (4, CONTROL_CHAIN_SUPPORT_MOMENT), (5, None)],
    ids=["fuerza", "momento", "nan"],
)
def test_support_reaction_tampered_fails(index: int, control: str | None) -> None:
    """Una reacción en R (fuerza o momento, o NaN) por encima de la cota da fallo."""
    request, response, _ = solved("deslizador con dos cadenas")
    item = response.chain_results[1]
    _tag, _control, _value, scale = list(support_deviations(request, response))[2 + index // 3]
    values = list(item.r_reaction)
    values[index] = math.nan if control is None else 1e-9 * scale
    a, b, c, d, e, f = values
    tampered = dataclasses.replace(item, r_reaction=(a, b, c, d, e, f))
    response = dataclasses.replace(response, chain_results=(response.chain_results[0], tampered))
    failure = chain_checks(request, response)
    assert failure is not None
    assert failure.key == "fem3d.chain_postcondition_failed"
    assert failure.params["control"] == (control or CONTROL_CHAIN_SUPPORT_MOMENT)
    assert failure.node_id == next(
        n.entity_id for n in request.nodes if n.tag == request.chains[1].node
    )


def test_chain_normal_displacement_fails() -> None:
    """u_N con componente según n (y C movido igual, alias intacto): falla la normal."""
    request, response, _ = solved("cadena y resortes orientados")
    chain = request.chains[0]
    shift = 1e-6 * translation_scale(displacements(response))
    norm = math.sqrt(sum(v * v for v in chain.normal))
    move = [shift * v / norm for v in chain.normal]

    def push(values: list[float]) -> None:
        for k in range(3):
            values[k] += move[k]

    response = replace_node(response, chain.node, push)
    item = response.chain_results[0]
    c_disp = list(item.c_disp)
    push(c_disp)
    a, b, c, d, e, f = c_disp
    moved = dataclasses.replace(item, c_disp=(a, b, c, d, e, f))
    response = dataclasses.replace(response, chain_results=(moved,))
    failure = chain_checks(request, response)
    assert failure is not None
    assert failure.params["control"] == CONTROL_CHAIN_NORMAL


def test_displacement_tampered_fails_residual() -> None:
    request, response, config = solved("pórtico con peso propio")
    free = next(n.tag for n in request.nodes if n.tag not in {f.node for f in request.fixes})

    def scale(values: list[float]) -> None:
        values[0] *= 1 + 1e-6

    failure = residual_check(request, replace_node(response, free, scale), config)
    assert failure is not None
    assert failure.key == "fem.residual_failed"
    assert failure.params["component"] == "Ux"


def test_reaction_tampered_fails_equilibrium() -> None:
    request, response, config = solved("pórtico con peso propio")
    fixed = {fix.node for fix in request.fixes}
    nodes = list(response.node_results)
    position = next(i for i, (tag, _d, _r) in enumerate(nodes) if tag in fixed)
    tag, disp, reaction = nodes[position]
    nodes[position] = (tag, disp, (reaction[0] + 1e-3 * max(map(abs, reaction)), *reaction[1:]))
    response = dataclasses.replace(response, node_results=tuple(nodes))
    assert not equilibrium_residual(request, response, config).ok


def test_nan_displacement_fails_equilibrium() -> None:
    request, response, config = solved("voladizo oblicuo")
    tag = request.nodes[-1].tag

    def poison(values: list[float]) -> None:
        values[2] = math.nan

    assert not equilibrium_residual(request, replace_node(response, tag, poison), config).ok


# --- Estimador de penalización (D6) -------------------------------------------------------


def test_penalty_direction_tampered_fails() -> None:
    request, response, config = solved("dos giros")
    spring = next(s for s in request.springs if s.penalty)
    node, value, scale = next(penalty_deviations(request, response))
    assert value <= 1e-8 * scale  # Medido ≤ 1,2e-9 relativo.
    axis = spring_direction(spring.axis_x, spring.axis_yp, spring.dof)

    def rotate(values: list[float]) -> None:
        for k in range(3):
            values[3 + k] += 1e-5 * scale * axis[k]

    failure = penalty_check(request, replace_node(response, node, rotate), config)
    assert failure is not None
    assert failure.key == "fem3d.penalty_estimate_failed"


@pytest.mark.parametrize("penalty_factor", [1e2, 1e10], ids=["blanda", "rígida"])
def test_penalty_factor_out_of_range_fails(penalty_factor: float) -> None:
    """Medido: error real 1,6e-3 con 1e2 (modelo) y 2,3e-6 con 1e10 (redondeo ≈ eps·1e10),
    ambos > tol_penalty = 1e-6; el estimador combinado los rechaza."""
    request, response, config = solved("dos giros", penalty_factor)
    failure = penalty_check(request, response, config)
    assert failure is not None
    assert failure.key == "fem3d.penalty_estimate_failed"


def test_exact_runs_have_no_penalty_estimate() -> None:
    request, response, config = solved("deslizador con dos cadenas")
    assert list(penalty_deviations(request, response)) == []
    assert penalty_check(request, response, config) is None
