"""Modo de mecanismo determinista del plan T-017, D3 (CON-009, FEM-008).

Sin solver: con singularidad, la corrida termina antes de lanzar el worker. Comprueba la
reproducibilidad con UUID nuevos, la nulidad, la unión de mecanismos independientes, la
lista truncada, la diagonal nula, la invariancia ante permutación y la vuelta al pivote
con aviso cuando el modo no es concluyente.
"""

import itertools
import math
import random
from uuid import UUID

import pytest

from fem_support import Frame, inclined
from veritas import cli
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, Severity
from veritas.core.geometry.entities import Node
from veritas.core.supports.support import pinned_restraints, roller_restraints
from veritas.core.units import Quantity
from veritas.fem import AnalysisConfiguration, AnalysisRun, RunStatus, analyze_planar_xz
from veritas.fem.adapters import singularity
from veritas.fem.adapters.planar_xz import translate_planar_xz
from veritas.fem.adapters.singularity import (
    check_singularity,
    mechanism_mode,
    reduced_stiffness,
)

RELEASED_RY = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)
TOL = 1e-12


def unions_mechanism() -> tuple[AnalysisRun, cli._Variant]:
    variant = cli._unions_variant("Mecanismo", None, mechanism=True)
    run = analyze_planar_xz(variant.model, AnalysisConfiguration(variant.case_id))
    return run, variant


def mode_text(run: AnalysisRun, labels: dict[UUID, str]) -> list[str]:
    return cli._mechanism_lines(run, labels)


def test_unions_mechanism_is_reproducible_with_new_uuids() -> None:
    """20 reconstrucciones: misma clave, misma ubicación y mismo modo (hecho 6 → 7)."""
    seen = set()
    for _ in range(20):
        run, variant = unions_mechanism()
        (error,) = [d for d in run.diagnostics if d.severity is Severity.ERROR]
        assert error.location is not None
        assert error.location.entity_id is not None
        location = variant.labels[error.location.entity_id]
        seen.add((error.message_key, location, tuple(mode_text(run, variant.labels))))
    assert len(seen) == 1
    ((key, location, lines),) = seen
    assert key == "fem.mechanism_member_end"
    assert location == "B1"
    assert lines == (
        "  Modo de mecanismo (nulidad 1; participación ≥ 1e-06): extremo J de B1, Ry (0.1); "
        "nudo N1, Ry (0.1); nudo N2, Ry (0.1); nudo N3, Uz (0.6); nudo N3, Ry (0.1).",
    )


def test_unions_mechanism_participation_values() -> None:
    run, _variant = unions_mechanism()
    assert run.pivot is not None
    assert run.pivot.nullity == 1
    values = sorted(item.participation for item in run.pivot.mechanism)
    assert values == pytest.approx([0.1, 0.1, 0.1, 0.1, 0.6], abs=1e-9)
    assert math.fsum(values) == pytest.approx(1.0, abs=1e-12)  # Traza de QQᵀ = nulidad.


def hinge(frame: Frame, x0: float) -> None:
    """Viga articulada-rodillo de 4 m con rótula interna en el centro (J de la primera)."""
    a, m, b = frame.node(x0, 0), frame.node(x0 + 2.0, 0), frame.node(x0 + 4.0, 0)
    first = frame.member(a, m)
    frame.member(m, b)
    frame.support(a, pinned_restraints())
    frame.support(b, roller_restraints())
    frame.model.set_member_end(first.id, MemberEnd.J, RELEASED_RY)
    frame.nodal(m, fz=-1000.0)


def test_t_con_004_mechanism_has_nullity_one_on_member_end() -> None:
    frame = Frame()
    hinge(frame, 0.0)
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.FAILED
    assert run.pivot is not None
    assert run.pivot.nullity == 1
    assert run.diagnostics[0].message_key == "fem.mechanism_member_end"
    ends = [item for item in run.pivot.mechanism if item.member_id is not None]
    assert [item.end for item in ends] == ["J"]


def test_two_independent_mechanisms_give_the_union() -> None:
    frame = Frame()
    hinge(frame, 0.0)
    hinge(frame, 10.0)
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.pivot is not None
    assert run.pivot.nullity == 2
    ends = {item.member_id for item in run.pivot.mechanism if item.member_id is not None}
    assert len(ends) == 2
    # Unión por GDL: cada mitad conserva las participaciones de la rótula aislada.
    values = sorted(item.participation for item in run.pivot.mechanism)
    assert values == pytest.approx([0.1] * 8 + [0.6] * 2, abs=1e-9)


def labelled_chain(frame: Frame) -> list[Node]:
    """Cadena de 4 barras sin apoyos, nudos N0…N4 con etiqueta (simétrica respecto a N2)."""
    m = Quantity.of
    return [
        frame.model.add_node(m(2.0 * i, "m"), m(0, "m"), m(0, "m"), label=f"N{i}") for i in range(5)
    ]


def test_rigid_body_mechanism_list_is_truncated() -> None:
    """Sin apoyos: participan todos los nudos (sólido rígido); la CLI muestra 10 y «y N más»."""
    frame = Frame()
    nodes = labelled_chain(frame)
    for a, b in itertools.pairwise(nodes):
        frame.member(a, b)
    frame.nodal(nodes[2], fz=-1000.0)
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.pivot is not None
    assert run.pivot.nullity == 3
    assert len(run.pivot.mechanism) == 15
    assert run.diagnostics[0].message_key == "fem.singular"
    labels: dict[UUID, str] = {node.id: node.label for node in nodes}
    (line,) = mode_text(run, labels)
    assert line.endswith(" y 5 más.")
    assert line.count("nudo ") == cli.MECHANISM_LIST_LIMIT


def test_symmetric_rigid_body_location_is_reproducible() -> None:
    """Revisión E2, 1: participaciones empatadas (N0 y N4 simétricos) en 20 reconstrucciones
    con UUID nuevos dan siempre la misma ubicación y componente."""
    seen = set()
    for _ in range(20):
        frame = Frame()
        nodes = labelled_chain(frame)
        for a, b in itertools.pairwise(nodes):
            frame.member(a, b)
        frame.nodal(nodes[2], fz=-1000.0)
        run = analyze_planar_xz(frame.model, frame.config())
        error = run.diagnostics[0]
        assert error.location is not None
        assert error.location.entity_id is not None
        label = frame.model.node(error.location.entity_id).label
        seen.add((error.message_key, label, error.params_dict()["component"]))
    assert len(seen) == 1, seen


def test_oblique_tangent_is_named_in_domain_terms() -> None:
    """Dos rodillos paralelos inclinados 37° a lo largo de la barra: mecanismo en la tangente."""
    frame = Frame()
    m = Quantity.of
    c, s = math.cos(math.radians(37.0)), math.sin(math.radians(37.0))
    a = frame.model.add_node(m(0, "m"), m(0, "m"), m(0, "m"), label="A")
    b = frame.model.add_node(m(4 * c, "m"), m(0, "m"), m(4 * s, "m"), label="B")
    frame.member(a, b)
    frame.support(a, roller_restraints(), inclined(37.0))
    frame.support(b, roller_restraints(), inclined(37.0))
    frame.nodal(b, fz=-1000.0)
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.FAILED
    assert run.pivot is not None
    assert run.pivot.nullity == 1
    tangents = [item for item in run.pivot.mechanism if item.component == "t"]
    assert len(tangents) == 2
    (line,) = mode_text(run, {a.id: "A", b.id: "B"})
    assert "nudo A, traslación tangente del apoyo" in line
    assert "nudo B, traslación tangente del apoyo" in line


def test_zero_diagonal_is_an_isolated_null_coordinate() -> None:
    mode = mechanism_mode([[4.0, 0.0], [0.0, 0.0]], TOL)
    assert mode.conclusive
    assert mode.nullity == 1
    assert mode.participation == (0.0, 1.0)


def test_zero_diagonal_and_singular_rest_are_combined() -> None:
    matrix = [[2.0, -2.0, 0.0], [-2.0, 2.0, 0.0], [0.0, 0.0, 0.0]]
    mode = mechanism_mode(matrix, TOL)
    assert mode.conclusive
    assert mode.nullity == 2
    assert mode.participation == pytest.approx((0.5, 0.5, 1.0), abs=1e-15)


def test_zero_diagonal_coupled_to_the_rest_is_inconclusive() -> None:
    """Revisión E2, 3: una diagonal nula con fila no nula (K indefinida) no es concluyente."""
    mode = mechanism_mode([[4.0, 1.0], [1.0, 0.0]], TOL)
    assert not mode.conclusive


def test_stable_structure_does_not_compute_mode() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(4.0, 0)
    frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(b, roller_restraints())
    frame.nodal(b, fx=1000.0)
    request = translate_planar_xz(frame.model, frame.config()).request
    report = check_singularity(request, TOL)
    assert not report.singular
    assert report.mode is None


def test_participation_is_invariant_under_permutation() -> None:
    """Permutación a mano de la K reducida: mismas participaciones, con tolerancia."""
    variant = cli._unions_variant("Mecanismo", None, mechanism=True)
    request = translate_planar_xz(variant.model, AnalysisConfiguration(variant.case_id)).request
    matrix, _labels = reduced_stiffness(request)
    base = mechanism_mode(matrix, TOL)
    rng = random.Random(17)
    for _ in range(5):
        order = list(range(len(matrix)))
        rng.shuffle(order)
        permuted = [[matrix[i][j] for j in order] for i in order]
        mode = mechanism_mode(permuted, TOL)
        assert mode.nullity == base.nullity
        for position, original in enumerate(order):
            assert mode.participation[position] == pytest.approx(
                base.participation[original], abs=1e-9
            )


def test_guard_band_makes_mode_inconclusive() -> None:
    """Un vector nulo con una componente 1e-3 da p = 1e-6/(2 + 1e-6) ≈ 5e-7, en la banda."""
    v = [1.0, 1e-3, 1.0]
    norm2 = sum(x * x for x in v)
    matrix = [[(i == j) - v[i] * v[j] / norm2 for j in range(3)] for i in range(3)]
    mode = mechanism_mode(matrix, TOL)
    assert mode.nullity == 1
    assert mode.residual <= singularity.MECHANISM_RESIDUAL_FACTOR * TOL  # Causa: la banda.
    assert not mode.conclusive


def test_inconclusive_mode_falls_back_to_pivot_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(singularity, "MECHANISM_GUARD_BAND", (0.0, 1.0))
    run, _variant = unions_mechanism()
    assert run.status is RunStatus.FAILED
    assert run.pivot is not None
    assert run.pivot.nullity is None
    assert run.pivot.mechanism == ()
    keys = [d.message_key for d in run.diagnostics]
    assert "fem.mechanism_mode_inconclusive" in keys
    warning = next(d for d in run.diagnostics if d.message_key.endswith("inconclusive"))
    assert warning.severity is Severity.WARNING
    error = run.diagnostics[0]
    assert error.code is DiagnosticCode.ANALYSIS_SINGULAR
    assert error.location is not None
    # Vuelta al pivote (T-010): clave y ubicación las del GDL del pivote.
    if run.pivot.member_id is not None:
        assert error.message_key == "fem.mechanism_member_end"
        assert error.location.entity_id == run.pivot.member_id
    else:
        assert error.message_key == "fem.singular"
        assert error.location.entity_id == run.pivot.node_id


def test_mechanism_constants_are_recorded_in_provenance() -> None:
    run, _variant = unions_mechanism()
    assert run.configuration["mechanism_participation"] == 1e-6
    assert run.configuration["mechanism_guard_low"] == 1e-8
    assert run.configuration["mechanism_guard_high"] == 1e-4
    assert run.configuration["mechanism_residual_factor"] == 100.0


def test_fallback_component_is_in_domain_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Al volver al pivote, ``fem.singular`` también nombra la componente en dominio."""
    monkeypatch.setattr(singularity, "MECHANISM_GUARD_BAND", (0.0, 1.0))
    frame = Frame()
    nodes = labelled_chain(frame)
    for a, b in itertools.pairwise(nodes):
        frame.member(a, b)
    frame.nodal(nodes[2], fz=-1000.0)
    run = analyze_planar_xz(frame.model, frame.config())
    error = run.diagnostics[0]
    assert error.message_key == "fem.singular"
    assert error.params_dict()["component"] in {"Ux", "Uz", "Ry", "t"}
