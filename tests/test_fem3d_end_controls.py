"""Controles y normalización con extremos no rígidos (plan T-018, pieza E2.5; ADR-004 §4 y §6).

Desplazamientos del elemento con aux, reacciones sumadas con sus aux (hecho 17), enlaces de
extremo (alias, F = k·Δ y equilibrio de cada aux con las cargas correctoras), contraste
exacto de la penalización iterada frente a la K reducida de la salvaguarda, diagnóstico
singular en un extremo (m1) y corridas completas sin la guarda E2.2-d. Los workers
manipulados ejecutan el worker real y tocan su respuesta: cada control da su clave.
"""

import dataclasses
import math
import random
from pathlib import Path

import pytest

import test_fem3d_singularity as cases
from fem_support import Frame
from test_fem3d_analyze import analyze, assert_failed, wrapper
from test_fem3d_end_penalty import grid_frame, translate
from test_fem3d_solver import run
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode
from veritas.core.loads.loads import LoadBasis
from veritas.core.supports.support import fixed_restraints
from veritas.core.units import Quantity
from veritas.fem.adapters.singularity import pivot_factorization, solve_factored
from veritas.fem.results.normalized import ComponentStatus
from veritas.fem.results.run import RunStatus
from veritas.fem.spatial import normalize_spatial, postconditions, provenance_constants
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.postconditions import (
    CONTROL_AUX_FORCE,
    CONTROL_AUX_MOMENT,
    CONTROL_CONTRAST_DISPLACEMENT,
    CONTROL_CONTRAST_FORCE,
    CONTROL_END_ALIAS,
    CONTROL_END_SPRING_FORCE,
    CONTROL_END_SPRING_GAP,
    all_displacements,
    contrast_reference,
    element_displacement,
    end_link_deviations,
    end_penalty_contrast,
    node_reactions,
    summed_reactions,
)
from veritas.fem.spatial.protocol import SpatialWorkerResponse
from veritas.fem.spatial.results import PROVENANCE_KEYS
from veritas.fem.spatial.stiffness import RELEASE_Z_INDICES, check_singularity, force_scale

pytestmark = pytest.mark.solver

KINDS = ("uy", "uz_free", "uyuz", "rz", "ryrz")


def _config(frame: Frame) -> SpatialAnalysisConfiguration:
    return SpatialAnalysisConfiguration(frame.case_id)


# --- Sustitución con la factorización de la salvaguarda (E2.5-b) ---------------------------


def test_solve_factored_matches_a_known_solution() -> None:
    """Escalado de Jacobi muy desigual y pivotaje (orden permutado)."""
    rng = random.Random(3)
    size = 9
    a = [[rng.uniform(-1, 1) for _ in range(size)] for _ in range(size)]
    scale = [10.0 ** (3 * (i % 3)) for i in range(size)]
    matrix = [
        [
            (sum(a[k][i] * a[k][j] for k in range(size)) + (size if i == j else 0.0))
            * math.sqrt(scale[i] * scale[j])
            for j in range(size)
        ]
        for i in range(size)
    ]
    x = [rng.uniform(-1, 1) for _ in range(size)]
    rhs = [sum(m * v for m, v in zip(row, x, strict=True)) for row in matrix]
    _, _, singular, factor = pivot_factorization(matrix, 1e-12)
    assert not singular
    assert factor is not None
    assert factor.order != list(range(size))
    solution = solve_factored(matrix, factor, rhs)
    assert max(abs(a - b) for a, b in zip(solution, x, strict=True)) <= 1e-12


def test_report_keeps_the_factorization_only_with_end_penalty() -> None:
    config = SpatialAnalysisConfiguration(grid_frame(50, "uy").case_id)
    penalized = translate(grid_frame(50, "uy"))
    rigid = translate(grid_frame(50, "rigid"))
    assert penalized.end_penalties
    assert not rigid.end_penalties
    assert check_singularity(penalized, config.tol_pivot).factor is not None
    report = check_singularity(rigid, config.tol_pivot)
    assert report.factor is None
    assert report.reduced is None


# --- Desplazamientos y reacciones con aux --------------------------------------------------


def test_element_displacement_takes_the_aux_end() -> None:
    request = translate(grid_frame(50, "uy"))
    response = run(request)
    displacement = all_displacements(response)
    (aux,) = request.aux_nodes
    beam = next(b for b in request.beams if b.tag == aux.beam)
    values = element_displacement(request, beam, displacement)
    assert aux.end == "J"
    assert values[:6] == list(displacement[beam.node_i])
    assert values[6:] == list(displacement[aux.tag])
    assert values[6:] != list(displacement[beam.node_j])  # Uy con resorte: el aux se separa.
    # Sin el aux (normalización, E2.5-f), el del maestro.
    domain = {tag: d for tag, d, _r in response.node_results}
    assert element_displacement(request, beam, domain)[6:] == list(domain[beam.node_j])


def test_reaction_sums_the_aux_multiplier() -> None:
    """Hecho 17: en el nudo con cadena y aux, nodeReaction(N) solo lleva el multiplicador del
    equalDOF; la suma con el aux es la reacción del rodillo (según n, sin momento)."""
    frame = grid_frame(50, "rz", chain=True)
    request = translate(frame)
    response = run(request)
    (chain,) = request.chains
    normal = chain.normal
    n = math.sqrt(sum(v * v for v in normal))
    raw = {tag: r for tag, _d, r in response.node_results}[chain.node]
    summed = summed_reactions(request, response)[chain.node]
    force = max(abs(v) for v in summed[:3])

    def tangential(r: list[float] | tuple[float, ...]) -> float:
        along = sum(r[k] * normal[k] for k in range(3)) / n
        return max(abs(r[k] - along * normal[k] / n) for k in range(3))

    assert tangential(summed) <= 1e-9 * force
    assert max(abs(v) for v in summed[3:]) <= 1e-9 * force * force_scale(request)[1]
    assert tangential(raw) > 1e-3 * force  # Sin la suma, el multiplicador aparece.
    published = node_reactions(request, response)[chain.node]
    assert published is not None
    assert max(abs(a - b) for a, b in zip(published, summed, strict=True)) <= 1e-9 * force


# --- Enlaces de extremo --------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_link_deviations_are_within_bounds(kind: str) -> None:
    frame = grid_frame(50, kind)
    request = translate(frame)
    response = run(request)
    controls = set()
    for _aux, control, value, bound in end_link_deviations(request, response, _config(frame)):
        controls.add(control)
        assert value <= bound, (control, value, bound)
    assert CONTROL_AUX_FORCE in controls
    assert CONTROL_END_SPRING_FORCE in controls


def _horizontal_rz_spring() -> Frame:
    """Barra horizontal según X con Rz en resorte en J (ez = Z, caso coordenado de E2-a):
    aux exacto con equalDOF y un zeroLength sin penalización."""
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(3.0, 0.0)
    c = frame.node(3.0, 0.0, y=2.0)
    m1 = frame.member(a, b, alpha=0.0)
    frame.member(b, c, alpha=0.0)
    frame.support(a, fixed_restraints())
    frame.support(c, fixed_restraints())
    frame.nodal(b, fx=500.0, fy=-800.0, fz=-1000.0, mz=300.0)
    spring = Quantity.of(2e6, "N*m/rad")
    frame.model.set_member_end(
        m1.id, MemberEnd.J, EndConnection.rigid().with_dof(Dof.RZ, DofState.SPRING, spring)
    )
    return frame


def test_coordinate_spring_case_is_exact_and_succeeds() -> None:
    frame = _horizontal_rz_spring()
    request = translate(frame)
    assert request.end_equals
    assert request.end_springs
    assert not request.end_penalties
    run_ = analyze(frame)
    assert run_.status is RunStatus.SUCCEEDED, run_.diagnostics


@pytest.mark.parametrize(
    ("after", "control"),
    [
        # Alias: una componente del equalDOF del aux deja de coincidir con el maestro.
        ("data['aux'][0][1][0] += 1e-3", CONTROL_END_ALIAS),
        # Coherencia: basicForce ≠ k·basicDeformation en el zeroLength (sin penalización).
        ("data['end_springs'][0][1] += 1.0", CONTROL_END_SPRING_FORCE),
        # Δ ≠ b·(u_aux − u_N): Δ y F escalados juntos (F = k·Δ se mantiene).
        (
            "s = data['end_springs'][0]; s[1] *= 1.001; s[2] *= 1.001",
            CONTROL_END_SPRING_GAP,
        ),
    ],
    ids=["alias", "F=k·Δ", "Δ"],
)
def test_manipulated_link_is_failed(tmp_path: Path, after: str, control: str) -> None:
    run_ = analyze(_horizontal_rz_spring(), wrapper(tmp_path, after=after))
    assert_failed(run_, DiagnosticCode.ANALYSIS_FAILED, "fem3d.end_link_check_failed")
    params = run_.diagnostics[0].params_dict()
    assert params["control"] == control
    assert params["end"] == "J"


@pytest.mark.parametrize(
    ("index", "control"), [(1, CONTROL_AUX_FORCE), (4, CONTROL_AUX_MOMENT)], ids=["F", "M"]
)
def test_manipulated_aux_reaction_is_failed(tmp_path: Path, index: int, control: str) -> None:
    """J libre: la reacción del aux no entra en el equilibrio global (N sin restricción) y
    solo la detecta el equilibrio del aux (ADR-004 §6)."""
    after = f"data['aux'][0][2][{index}] += 10.0"
    run_ = analyze(grid_frame(50, "uy"), wrapper(tmp_path, after=after))
    assert_failed(run_, DiagnosticCode.ANALYSIS_FAILED, "fem3d.end_link_check_failed")
    assert run_.diagnostics[0].params_dict()["control"] == control


def _fixed_bar(kind: str) -> Frame:
    """Barra oblicua entre dos empotramientos con el mismo resorte en I y en J y solo carga
    de barra: los nudos de dominio no se mueven y solo los aux (revisión de E2.5, M1)."""
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(3.0, 1.2, y=0.8)
    member = frame.member(a, b, alpha=0.37)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_member_uniform_load(
        frame.case_id,
        member.id,
        basis=LoadBasis.LOCAL,
        qx=Quantity.of(0.0, "N/m"),
        qy=Quantity.of(-2e3, "N/m"),
        qz=Quantity.of(-5e3, "N/m"),
    )
    dof = {"ry": Dof.RY, "rz": Dof.RZ, "uy": Dof.UY}[kind]
    unit = "N/m" if kind == "uy" else "N*m/rad"
    spring = EndConnection.rigid().with_dof(dof, DofState.SPRING, Quantity.of(1e6, unit))
    frame.model.set_member_end(member.id, MemberEnd.I, spring)
    frame.model.set_member_end(member.id, MemberEnd.J, spring)
    return frame


@pytest.mark.parametrize("kind", ["ry", "rz", "uy"])
def test_all_domain_nodes_fixed_succeeds(kind: str) -> None:
    """M1: con las escalas solo de dominio (≈ 0) los enlaces daban FAILED espurio."""
    frame = _fixed_bar(kind)
    assert translate(frame).end_penalties
    run_ = analyze(frame)
    assert run_.status is RunStatus.SUCCEEDED, run_.diagnostics
    normalize_spatial(run_, frame.model)


def test_supported_node_reaction_sums_its_aux() -> None:
    """Hecho 17 en nudos con ``fix`` y aux: la reacción publicada es la suma (proyección
    identidad con seis GDL fijos); nodeReaction(N) sola lleva el multiplicador."""
    frame = _fixed_bar("ry")
    request = translate(frame)
    response = run(request)
    raw = {tag: r for tag, _d, r in response.node_results}
    published = node_reactions(request, response)
    summed = summed_reactions(request, response)
    for node in request.nodes:
        reaction = published[node.tag]
        assert reaction is not None
        force = max(abs(v) for v in summed[node.tag][:3])
        assert max(abs(a - b) for a, b in zip(reaction, summed[node.tag], strict=True)) <= (
            1e-12 * force * force_scale(request)[1]
        )
        assert max(abs(a - b) for a, b in zip(raw[node.tag], summed[node.tag], strict=True)) > (
            1e-3 * force
        )


# --- Contraste exacto (ADR-004 §4) ---------------------------------------------------------


def test_contrast_reference_matches_the_worker() -> None:
    frame = grid_frame(500, "ryrz", both=True)
    request = translate(frame)
    response = run(request)
    report = check_singularity(request, _config(frame).tol_pivot)
    reference = contrast_reference(request, report)
    displacement = all_displacements(response)
    assert set(reference.displacement) == set(displacement)
    scale = max(abs(v) for d in reference.displacement.values() for v in d[:3])
    for tag, values in reference.displacement.items():
        assert max(abs(a - b) for a, b in zip(values, displacement[tag], strict=True)) <= (
            1e-8 * scale * max(1.0, force_scale(request)[1])
        )
    assert end_penalty_contrast(request, response, _config(frame), report) is None


# Desplazamiento rígido de todos los nudos y aux (alias, Δ y fuerzas intactos: solo lo ve el
# contraste) y fuerza de la barra B1 en I (sin aux: solo la ven el contraste y las barras).
SHIFT = (
    "s = max(abs(v) for n in data['nodes'] for v in n[1][:3]); "
    "[i[1].__setitem__(0, i[1][0] + 1e-4 * s) for i in data['nodes'] + data['aux']]"
)
FORCE = "f = data['beams'][0][1]; f[1] += 1e-4 * max(abs(v) for v in f)"


@pytest.mark.parametrize(
    ("after", "control"),
    [(SHIFT, CONTROL_CONTRAST_DISPLACEMENT), (FORCE, CONTROL_CONTRAST_FORCE)],
    ids=["desplazamiento", "fuerza"],
)
def test_manipulated_worker_fails_the_contrast(tmp_path: Path, after: str, control: str) -> None:
    run_ = analyze(grid_frame(50, "uy"), wrapper(tmp_path, after=after))
    assert_failed(run_, DiagnosticCode.ANALYSIS_FAILED, "fem3d.end_penalty_contrast_failed")
    assert run_.diagnostics[0].params_dict()["control"] == control


def test_force_floor_uses_the_case_force() -> None:
    """Suelo de las barras (m6): una desviación por debajo de tol_penalty·F pasa aunque la
    barra lleve fuerzas pequeñas; por encima de tol_penalty·máx(F, ‖f‖∞), FAILED."""
    frame = grid_frame(50, "uy")
    request = translate(frame)
    response = run(request)
    config = _config(frame)
    report = check_singularity(request, config.tol_pivot)
    force = force_scale(request)[0]
    tag, values = response.beam_forces[1]
    largest = max(abs(values[i]) for i in (0, 1, 2, 6, 7, 8))

    def shifted(delta: float) -> SpatialWorkerResponse:
        changed = list(values)
        changed[1] += delta
        beams = (response.beam_forces[0], (tag, tuple(changed)))
        return dataclasses.replace(response, beam_forces=beams)

    small = 0.5 * config.tol_penalty * force
    assert end_penalty_contrast(request, shifted(small), config, report) is None
    big = 2.0 * config.tol_penalty * max(force, largest)
    failure = end_penalty_contrast(request, shifted(big), config, report)
    assert failure is not None
    assert failure.params["control"] == CONTROL_CONTRAST_FORCE


def test_unreliable_reference_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(postconditions, "REFERENCE_MARGIN", 0.0)
    run_ = analyze(grid_frame(50, "rz"))
    assert_failed(run_, DiagnosticCode.ANALYSIS_FAILED, "fem3d.end_penalty_reference_unreliable")


def test_mixed_d4_and_end_penalty_succeeds() -> None:
    """Apoyo D4 con dos giros oblicuos por penalización y un resorte Ry oblicuo de extremo
    (penalización iterada): la referencia trata D4 como exacto (límite declarado, E2.5-a)."""
    frame = cases._penalty_frame(10.0)
    member_id = next(iter(frame.model.members))
    spring = Quantity.of(5e6, "N*m/rad")
    frame.model.set_member_end(
        member_id, MemberEnd.J, EndConnection.rigid().with_dof(Dof.RY, DofState.SPRING, spring)
    )
    request = translate(frame)
    assert any(s.penalty for s in request.springs)
    assert request.end_penalties
    run_ = analyze(frame)
    assert run_.status is RunStatus.SUCCEEDED, run_.diagnostics


# --- Corridas completas y normalización (E2-f) ---------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("layout", ["one", "both", "chain"])
def test_end_runs_succeed_and_normalize(kind: str, layout: str) -> None:
    """Sin la guarda E2.2-d: OK con todos los controles; la normalización publica y su
    equilibrio de nudos (aux sumados al nudo, cargas correctoras excluidas) se cumple."""
    frame = grid_frame(50, kind, both=layout == "both", chain=layout == "chain")
    run_ = analyze(frame)
    assert run_.status is RunStatus.SUCCEEDED, run_.diagnostics
    results = normalize_spatial(run_, frame.model)
    assert len(results.members) == 2
    assert {n.node_id for n in results.nodes} == set(frame.model.nodes)


def test_released_end_force_is_calculated_and_null() -> None:
    """E2-f: el momento liberado sale CALCULATED con valor ≈ 0 (``-releasez`` en J)."""
    frame = grid_frame(50, "rigid")
    member_id = next(iter(frame.model.members))
    frame.model.set_member_end(
        member_id, MemberEnd.J, EndConnection.rigid().with_dof(Dof.RZ, DofState.RELEASED)
    )
    run_ = analyze(frame)
    assert run_.status is RunStatus.SUCCEEDED, run_.diagnostics
    assert run_.raw is not None
    raw = next(m for m in run_.raw.members if m.member_id == member_id)
    largest = max(abs(v) for v in raw.local_force)
    assert abs(raw.local_force[RELEASE_Z_INDICES[1]]) <= 1e-9 * largest
    member = next(
        m for m in normalize_spatial(run_, frame.model).members if m.member_id == member_id
    )
    assert all(c.status is ComponentStatus.CALCULATED for c in member.p_j.components)


def test_series_torsion_spring_succeeds() -> None:
    frame = grid_frame(50, "rigid")
    member_id = next(iter(frame.model.members))
    spring = Quantity.of(1e5, "N*m/rad")
    frame.model.set_member_end(
        member_id, MemberEnd.J, EndConnection.rigid().with_dof(Dof.RX, DofState.SPRING, spring)
    )
    run_ = analyze(frame)
    assert run_.status is RunStatus.SUCCEEDED, run_.diagnostics


# --- m1: singularidad en un extremo (E2.5-d) y procedencia (E2.5-e) ------------------------


def test_singular_member_end_names_the_bar_and_end() -> None:
    """Uy liberado en los dos extremos de una barra biempotrada: traslación transversal
    libre de la barra; solo participan las coordenadas propias de los aux."""
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(3.0, 1.0, y=0.5)
    member = frame.member(a, b, alpha=0.37)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.nodal(b, fz=-1000.0)
    released = EndConnection.rigid().with_dof(Dof.UY, DofState.RELEASED)
    frame.model.set_member_end(member.id, MemberEnd.I, released)
    frame.model.set_member_end(member.id, MemberEnd.J, released)
    run_ = analyze(frame)
    assert_failed(run_, DiagnosticCode.ANALYSIS_SINGULAR, "fem3d.singular_member_end")
    error = run_.diagnostics[0]
    assert error.location is not None
    assert error.location.entity_id == member.id
    assert error.params_dict()["end"] in ("I", "J")
    assert error.params_dict()["component"] == "Uy"


def test_new_provenance_constants_are_recorded_and_filtered() -> None:
    names = {
        "end_penalty_min_stagnation_iteration",
        "end_penalty_stagnation_ratio",
        "end_contrast_reference_margin",
        "end_axial_load_tolerance",  # Revisión de E2, m2.
    }
    assert names <= set(provenance_constants())
    assert set(provenance_constants()) == PROVENANCE_KEYS
