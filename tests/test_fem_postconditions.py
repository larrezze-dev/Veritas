"""Controles de la solución del plan T-017, D1: alias, esfuerzos por barra y extremos.

Las corridas reales dan la (petición, respuesta) de referencia; los casos negativos la
manipulan a mano y llaman a ``member_checks`` sin solver. Cada control no superado debe
dar su clave; la batería legítima debe pasar.
"""

import dataclasses
import math
from collections.abc import Callable
from functools import partial

import pytest

from fem_support import E_PA, IY, Frame, solved_pair
from veritas import fem
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode
from veritas.core.loads.loads import LoadBasis
from veritas.core.messages_es import render_message
from veritas.core.supports.support import fixed_restraints
from veritas.core.units import Quantity
from veritas.fem import RunStatus, postconditions
from veritas.fem.adapters.protocol import PlanarRequest, WorkerResponse
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.postconditions import (
    CONTROL_ALIAS,
    CONTROL_AUX_REACTION,
    CONTROL_FORCES,
    CONTROL_RELEASED,
    CONTROL_SPRING,
    END_MOMENT_INDEX,
    CheckFailure,
    member_checks,
)

pytestmark = pytest.mark.solver

L = 4.0
EI = E_PA * IY
RELEASED_RY = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)
END_KEY = "fem.member_end_postcondition_failed"


def spring_ry(ratio: float) -> EndConnection:
    k = ratio * EI / L
    return EndConnection.semi_rigid(Dof.RY, Quantity(k, Dof.RY.stiffness_dimension))


def uniform(frame: Frame, member_id: object, qx: float, qz: float) -> None:
    frame.model.add_member_uniform_load(
        frame.case_id,
        member_id,  # type: ignore[arg-type]
        basis=LoadBasis.GLOBAL,
        qx=Quantity.of(qx, "N/m"),
        qz=Quantity.of(qz, "N/m"),
    )


def clamped(
    end: MemberEnd | None, connection: EndConnection | None, *, only_b2: bool = False
) -> Frame:
    """Biempotrada N1-N3-N2 de 4 m (B1, B2) con q = −10 kN/m en Z; ``end`` es de B2.

    ``only_b2``: carga solo en B2, de modo que N3 gira (sin simetría, θ maestro ≠ 0).
    """
    frame = Frame()
    a, m, b = frame.node(0, 0), frame.node(L / 2, 0), frame.node(L, 0)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    for member in (frame.member(a, m), frame.member(m, b)):
        if not only_b2 or member.node_i == m.id:
            uniform(frame, member.id, 0.0, -10_000.0)
    if end is not None and connection is not None:
        frame.model.set_member_end(member.id, end, connection)
    return frame


def inclined_frame(variant: str) -> Frame:
    """Barra B1 inclinada 30° y barra B2 horizontal, ambas con qx = +3 kN/m y qz = −8 kN/m.

    ``variant``: «rígida», «B1 liberada en I» o «B2 liberada en I y J».
    """
    frame = Frame()
    c, s = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
    n1, n2, n3 = frame.node(0, 0), frame.node(L * c, L * s), frame.node(L * c + 3.0, L * s)
    b1, b2 = frame.member(n1, n2), frame.member(n2, n3)
    frame.support(n1, fixed_restraints())
    frame.support(n3, fixed_restraints())
    for member in (b1, b2):
        uniform(frame, member.id, 3_000.0, -8_000.0)
    if variant == "B1 liberada en I":
        frame.model.set_member_end(b1.id, MemberEnd.I, RELEASED_RY)
    elif variant == "B2 liberada en I y J":
        frame.model.set_member_end(b2.id, MemberEnd.I, RELEASED_RY)
        frame.model.set_member_end(b2.id, MemberEnd.J, RELEASED_RY)
    return frame


LEGITIMATE: dict[str, Callable[[], Frame]] = {
    "biempotrada rígida": lambda: clamped(None, None),
    "liberada en I con carga": lambda: clamped(MemberEnd.I, RELEASED_RY),
    "liberada en J con carga": lambda: clamped(MemberEnd.J, RELEASED_RY),
    **{
        f"resorte en J, kL/EI = {ratio:g}": partial(clamped, MemberEnd.J, spring_ry(ratio))
        for ratio in (1.0, 1e6, 1e8, 1e10, 1e12)
    },
    "resorte en I, kL/EI = 3": lambda: clamped(MemberEnd.I, spring_ry(3.0)),
    # Revisión E1, 2: maestro que gira y resorte muy rígido (cancelación en θm − θa).
    **{
        f"resorte en I, maestro girando, kL/EI = {ratio:g}": partial(
            clamped, MemberEnd.I, spring_ry(ratio), only_b2=True
        )
        for ratio in (1e8, 1e12)
    },
    **{
        f"inclinada 30°, {variant}": partial(inclined_frame, variant)
        for variant in ("rígida", "B1 liberada en I", "B2 liberada en I y J")
    },
}


@pytest.mark.parametrize("name", list(LEGITIMATE))
def test_legitimate_runs_pass_member_checks(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    request, response, config, run = solved_pair(LEGITIMATE[name](), monkeypatch)
    assert run.status is RunStatus.SUCCEEDED, run.diagnostics
    assert member_checks(request, response, config) is None


Pair = tuple[PlanarRequest, WorkerResponse, AnalysisConfiguration]


def pair(name: str, monkeypatch: pytest.MonkeyPatch) -> Pair:
    request, response, config, _run = solved_pair(LEGITIMATE[name](), monkeypatch)
    return request, response, config


def with_end_aux(
    response: WorkerResponse, aux: int, disp: tuple[float, ...] | None, reaction: tuple[float, ...]
) -> WorkerResponse:
    end_aux = tuple(
        (tag, disp if tag == aux and disp is not None else d, reaction if tag == aux else r)
        for tag, d, r in response.end_aux
    )
    return dataclasses.replace(response, end_aux=end_aux)  # type: ignore[arg-type]


def with_force(response: WorkerResponse, beam: int, index: int, value: float) -> WorkerResponse:
    forces = []
    for tag, force in response.beam_forces:
        values = list(force)
        if tag == beam:
            values[index] = value
        forces.append((tag, tuple(values)))
    return dataclasses.replace(response, beam_forces=tuple(forces))


def assert_end_failure(failure: CheckFailure | None, control: str, end: str) -> None:
    assert failure is not None
    assert failure.key == END_KEY
    assert failure.params["control"] == control
    assert failure.params["end"] == end
    assert failure.member_id is not None


def test_end_moment_index_matches_link_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """D1-5: con el giro liberado, localForce[2] (I) o [5] (J) es nulo y el otro no."""
    for name, end in (("liberada en I con carga", "I"), ("liberada en J con carga", "J")):
        request, response, _config = pair(name, monkeypatch)
        (link,) = request.end_links
        assert link.end == end
        force = dict(response.beam_forces)[link.beam]
        other = END_MOMENT_INDEX["J" if end == "I" else "I"]
        assert abs(force[END_MOMENT_INDEX[end]]) <= 1e-9 * abs(force[other])
        assert abs(force[other]) > 1_000.0


def test_spring_sign_convention(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hecho 3: M_extremo = k·(θ maestro − θ aux), en I y en J."""
    for name in ("resorte en J, kL/EI = 1", "resorte en I, kL/EI = 3"):
        request, response, _config = pair(name, monkeypatch)
        (link,) = request.end_links
        assert link.stiffness is not None
        disp = {tag: d for tag, d, _r in response.node_results}
        disp |= {tag: d for tag, d, _r in response.end_aux}
        moment = dict(response.beam_forces)[link.beam][END_MOMENT_INDEX[link.end]]
        expected = link.stiffness * (disp[link.master][2] - disp[link.aux][2])
        assert abs(moment) > 1_000.0
        assert abs(moment - expected) <= 1e-9 * abs(moment)


def test_alias_differing_by_one_ulp_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    request, response, config = pair("liberada en J con carga", monkeypatch)
    (link,) = request.end_links
    disp, reaction = next((d, r) for tag, d, r in response.end_aux if tag == link.aux)
    shifted = (math.nextafter(disp[0], math.inf), disp[1], disp[2])
    bad = with_end_aux(response, link.aux, shifted, reaction)
    assert_end_failure(member_checks(request, bad, config), CONTROL_ALIAS, "J")


def test_moment_at_released_end_without_displacement_fails_force_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un momento ≠ 0 en J sin cambiar u: lo detecta el control 2 en la fila 5."""
    request, response, config = pair("liberada en J con carga", monkeypatch)
    (link,) = request.end_links
    force = dict(response.beam_forces)[link.beam]
    bad = with_force(response, link.beam, 5, force[5] + 1.0)
    assert_end_failure(member_checks(request, bad, config), CONTROL_FORCES, "J")


def test_released_moment_control_is_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    """El control de momento nulo usa localForce: con F_eq desplazado, falla él y no el 2."""
    request, response, config = pair("liberada en J con carga", monkeypatch)
    (link,) = request.end_links
    # Se elimina la carga de la barra liberada y su localForce pasa a k·T·u: el control 2
    # pasa, pero el extremo liberado ya no tiene M = 0.
    loads = tuple(load for load in request.beam_loads if load.beam != link.beam)
    unloaded = dataclasses.replace(request, beam_loads=loads)
    beam = next(item for item in request.beams if item.tag == link.beam)
    disp = {tag: d for tag, d, _r in response.node_results}
    disp |= {tag: d for tag, d, _r in response.end_aux}
    rows = postconditions._beam_rows(unloaded, beam, disp, [0.0] * 6)
    bad = response
    for index, value in enumerate(rows.expected):
        bad = with_force(bad, beam.tag, index, value)
    assert abs(rows.expected[5]) > 1_000.0
    assert_end_failure(member_checks(unloaded, bad, config), CONTROL_RELEASED, link.end)


@pytest.mark.parametrize("factor", [-1.0, 3.0])
def test_inconsistent_spring_relation_fails(factor: float, monkeypatch: pytest.MonkeyPatch) -> None:
    """Convención invertida (−k) y k distinto con k > 0 (3k): residuo 2|M| en ambos casos.

    Con 3k la cota no se reduce, así que el test no depende de un k negativo.
    """
    request, response, config = pair("resorte en I, maestro girando, kL/EI = 1e+08", monkeypatch)
    (link,) = request.end_links
    assert link.stiffness is not None
    changed = dataclasses.replace(link, stiffness=factor * link.stiffness)
    bad = dataclasses.replace(request, end_links=(changed,))
    assert_end_failure(member_checks(bad, response, config), CONTROL_SPRING, "I")


@pytest.mark.parametrize("name", ["liberada en J con carga", "resorte en J, kL/EI = 1"])
def test_large_aux_rotation_reaction_fails(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    request, response, config = pair(name, monkeypatch)
    (link,) = request.end_links
    reaction = next(r for tag, _d, r in response.end_aux if tag == link.aux)
    bad = with_end_aux(response, link.aux, None, (reaction[0], reaction[1], 1.0))
    assert_end_failure(member_checks(request, bad, config), CONTROL_AUX_REACTION, "J")


def test_perturbed_force_in_rigid_member_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    request, response, config = pair("biempotrada rígida", monkeypatch)
    beam = request.beams[0]
    force = dict(response.beam_forces)[beam.tag]
    row = max(range(6), key=lambda i: abs(force[i]))  # El orden de tags varía con los UUID.
    bad = with_force(response, beam.tag, row, force[row] * (1 + 1e-6))
    failure = member_checks(request, bad, config)
    assert failure is not None
    assert failure.key == "fem.member_force_check_failed"
    assert failure.params["component"] == row + 1
    assert failure.member_id == beam.entity_id


@pytest.mark.parametrize("index", [0, 5])
def test_nan_force_fails(index: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un NaN en localForce falla en cualquier fila (cota o residuo NaN; revisión, 9).

    En producción ``WorkerResponse.from_json`` ya rechaza los valores no finitos.
    """
    request, response, config = pair("biempotrada rígida", monkeypatch)
    beam = request.beams[0]
    bad = with_force(response, beam.tag, index, math.nan)
    failure = member_checks(request, bad, config)
    assert failure is not None
    assert failure.key == "fem.member_force_check_failed"


def test_inverted_equivalent_load_sign_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hecho 8: con F_eq de signo contrario el control 2 no pasa."""
    request, response, config = pair("inclinada 30°, rígida", monkeypatch)
    flipped = tuple(
        dataclasses.replace(load, wy=-load.wy, wx=-load.wx) for load in request.beam_loads
    )
    bad = dataclasses.replace(request, beam_loads=flipped)
    failure = member_checks(bad, response, config)
    assert failure is not None
    assert failure.key == "fem.member_force_check_failed"
    assert failure.params["value"] > 1e3 * failure.params["bound"]  # type: ignore[operator]


def test_failure_is_rendered_with_member_location(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integración: la corrida FAILED lleva la clave, la barra y ningún resultado."""
    frame = clamped(MemberEnd.J, spring_ry(1.0))
    original = member_checks

    def tampered(
        request: PlanarRequest, response: WorkerResponse, config: AnalysisConfiguration
    ) -> CheckFailure | None:
        (link,) = request.end_links
        assert link.stiffness is not None
        flipped = dataclasses.replace(link, stiffness=-link.stiffness)
        return original(dataclasses.replace(request, end_links=(flipped,)), response, config)

    monkeypatch.setattr(fem, "member_checks", tampered)
    run = fem.analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.FAILED
    assert run.raw is None
    (diagnostic,) = run.diagnostics
    assert diagnostic.code is DiagnosticCode.ANALYSIS_FAILED
    assert diagnostic.message_key == END_KEY
    assert diagnostic.location is not None
    member = frame.model.member(diagnostic.location.entity_id)  # type: ignore[arg-type]
    assert not member.end_j.is_rigid
    text = render_message(diagnostic)
    assert "extremo J" in text
    assert CONTROL_SPRING in text
