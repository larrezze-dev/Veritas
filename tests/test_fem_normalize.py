"""Normalización a ADR-001 sin OpenSees, con corridas sintéticas (plan T-008).

La salida BRUTA se fabrica con la solución exacta del voladizo (x₁ = X, x₂ = Z, θ = −Ry):
así se prueban la frontera de errores, la correspondencia modelo↔corrida (M3), los estados
por componente y las postcondiciones (B1, D7) sin depender del solver.
"""

import math
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from fem_support import E_PA, IY, Frame, inclined
from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.geometry.vector import Vec3
from veritas.core.supports.support import fixed_restraints, roller_restraints
from veritas.fem import (
    AnalysisRun,
    Component,
    ComponentStatus,
    ResultBasis,
    ResultsNormalizationError,
    RunStatus,
    normalize_planar_xz,
)
from veritas.fem.adapters.planar_xz import translate_planar_xz
from veritas.fem.results import normalized
from veritas.fem.results.run import (
    RawMemberForces,
    RawNodeResult,
    RawPlanarResults,
    RawSpringReaction,
)

L = 2.0
P = 1000.0
EI = E_PA * IY


def synthetic_run(frame: Frame, raw: RawPlanarResults | None) -> AnalysisRun:
    config = frame.config()
    translation = translate_planar_xz(frame.model, config)
    return AnalysisRun(
        run_id=uuid4(),
        status=RunStatus.SUCCEEDED if raw is not None else RunStatus.FAILED,
        diagnostics=(),
        model_revision=frame.model.model_revision,
        case_id=frame.case_id,
        case_revision=translation.request.case_revision,
        configuration={**config.snapshot(), "penalty_equilibrium_factor": 10.0},
        request_json=translation.request.to_json(),
        versions={},
        hypotheses=translation.hypotheses,
        started_utc=datetime.now(UTC),
        duration_s=0.0,
        pivot=None,
        worker_stdout="",
        worker_stderr="",
        raw=raw,
    )


def cantilever(alpha: float | None = None) -> tuple[Frame, AnalysisRun]:
    """Voladizo +X empotrado en I con −P en Z en J y su salida BRUTA exacta."""
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    member = frame.member(a, b, alpha=alpha)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-P)
    raw = RawPlanarResults(
        "SOLVER_XZ_RAW",
        (
            RawNodeResult(a.id, (0.0, 0.0, 0.0), (0.0, P, P * L)),
            RawNodeResult(
                b.id, (0.0, -P * L**3 / (3 * EI), -P * L**2 / (2 * EI)), (None, None, None)
            ),
        ),
        (),
        (RawMemberForces(member.id, (0.0, P, P * L, 0.0, -P, 0.0)),),
    )
    return frame, synthetic_run(frame, raw)


def test_cantilever_normalized_to_adr001() -> None:
    frame, run = cantilever()
    res = normalize_planar_xz(run, frame.model)
    assert (res.run_id, res.model_revision, res.case_id) == (
        run.run_id,
        run.model_revision,
        run.case_id,
    )
    (support,) = res.reactions
    assert support.global_.basis is ResultBasis.GLOBAL
    assert support.global_.values() == (0.0, None, P, None, -P * L, None)
    assert support.support_basis is None
    tip = next(n for n in res.nodes if n.node_id != support.node_id)
    assert tip.displacement.values() == pytest.approx(
        (0.0, None, -P * L**3 / (3 * EI), None, P * L**2 / (2 * EI), None)
    )
    (member,) = res.members
    assert member.p_i.basis is ResultBasis.MEMBER_LOCAL
    assert member.p_i.values() == (0.0, None, P, None, -P * L, None)
    assert member.p_j.values() == (0.0, None, -P, None, 0.0, None)
    assert member.section_start.values() == (-0.0, None, -P, None, P * L, None)
    assert member.section_end.values() == member.p_j.values()
    assert member.axes == (Vec3(1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, 0.0, 1.0))
    assert "fem.hypothesis_normalized" in res.hypotheses


def test_out_of_plane_components_are_not_calculated_never_zero() -> None:
    frame, run = cantilever()
    res = normalize_planar_xz(run, frame.model)
    vectors = [n.displacement for n in res.nodes]
    vectors += [r.global_ for r in res.reactions]
    vectors += [v for m in res.members for v in (m.p_i, m.p_j, m.section_start, m.section_end)]
    for vector in vectors:
        for index in (1, 3, 5):
            assert vector.components[index].status is ComponentStatus.NOT_CALCULATED
            assert vector.components[index].value is None


def test_free_support_dof_is_not_applicable() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.support(b, roller_restraints())
    frame.nodal(b, fz=-P)
    raw = RawPlanarResults(
        "SOLVER_XZ_RAW",
        (
            RawNodeResult(a.id, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            RawNodeResult(b.id, (0.0, 0.0, 0.0), (None, P, None)),
        ),
        (),
        (RawMemberForces(member.id, (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),),
    )
    res = normalize_planar_xz(synthetic_run(frame, raw), frame.model)
    roller = next(r for r in res.reactions if r.node_id == b.id)
    statuses = [c.status for c in roller.global_.components]
    assert statuses[0] is ComponentStatus.NOT_APPLICABLE  # X libre
    assert statuses[2] is ComponentStatus.CALCULATED
    assert statuses[4] is ComponentStatus.NOT_APPLICABLE  # Ry libre
    assert roller.global_.values()[0] is None


def test_inclined_support_reports_both_bases_with_axes() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    support = frame.model.set_support(b.id, roller_restraints(), basis=inclined(30.0))
    frame.nodal(b, fz=-P)
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    r = 100.0  # Reacción del rodillo a lo largo de z′ = (−s, c).
    raw = RawPlanarResults(
        "SOLVER_XZ_RAW",
        (
            RawNodeResult(a.id, (0.0, 0.0, 0.0), (r * s, P - r * c, (P - r * c) * L)),
            RawNodeResult(b.id, (0.0, 0.0, 0.0), (None, None, None)),
        ),
        (RawSpringReaction(support.id, b.id, 2, True, (-r * s, r * c, 0.0)),),
        (
            RawMemberForces(
                member.id, (r * s, P - r * c, (P - r * c) * L, -r * s, -(P - r * c), 0.0)
            ),
        ),
    )
    res = normalize_planar_xz(synthetic_run(frame, raw), frame.model)
    roller = next(x for x in res.reactions if x.node_id == b.id)
    assert roller.axes == support.basis.rows
    assert roller.support_basis is not None
    assert roller.support_basis.basis is ResultBasis.SUPPORT
    local = roller.support_basis.values()
    assert local[0] is None  # Ux′ libre: NOT_APPLICABLE
    assert local[2] == pytest.approx(r)
    assert roller.global_.values()[0] == pytest.approx(-r * s)
    assert "fem.hypothesis_support_projection" in res.hypotheses


def test_failed_run_has_no_results() -> None:
    frame, _run = cantilever()
    with pytest.raises(InputValueError) as info:
        normalize_planar_xz(synthetic_run(frame, None), frame.model)
    assert info.value.code is DiagnosticCode.ANALYSIS_FAILED
    assert info.value.diagnostic.message_key == "fem.results_unavailable"


def test_model_changed_after_run_is_rejected() -> None:
    frame, run = cantilever()
    frame.nodal(list(frame.model.nodes.values())[1], fx=1.0)
    with pytest.raises(InputValueError) as info:
        normalize_planar_xz(run, frame.model)
    assert info.value.code is DiagnosticCode.REVISION_CONFLICT


def test_other_model_with_same_revision_is_rejected() -> None:
    _frame, run = cantilever()
    other, _ = cantilever()  # Mismo contador, otros UUID: la petición difiere.
    assert other.model.model_revision == run.model_revision
    with pytest.raises(InputValueError) as info:
        normalize_planar_xz(run, other.model)
    assert info.value.code is DiagnosticCode.REVISION_CONFLICT
    assert info.value.diagnostic.message_key == "fem.results_model_mismatch"


def test_untranslatable_model_is_a_mismatch() -> None:
    _frame, run = cantilever()
    other = Frame()
    other.node(0, 0, y=1.0)  # Fuera del plano: el traductor lo rechaza.
    for _ in range(run.model_revision - other.model.model_revision):
        other.node(0, 0)
    with pytest.raises(InputValueError) as info:
        normalize_planar_xz(run, other.model)
    assert info.value.code is DiagnosticCode.REVISION_CONFLICT


def test_equilibrium_postcondition_blocks_results() -> None:
    frame, run = cantilever()
    assert run.raw is not None
    (member,) = run.raw.members
    wrong = replace(member, local_force=(0.0, P, -P * L, 0.0, -P, 0.0))  # Signo de M_I mal
    broken = replace(run, raw=replace(run.raw, members=(wrong,)))
    with pytest.raises(ResultsNormalizationError) as info:
        normalize_planar_xz(broken, frame.model)
    assert info.value.code is DiagnosticCode.ANALYSIS_FAILED
    assert info.value.diagnostic.message_key == "fem.results_equilibrium"


@pytest.mark.parametrize(
    "local_force",
    [
        (0.0, P, P * L, 0.0, P, 0.0),  # Signo de V_J
        (P, P, P * L, 0.0, -P, 0.0),  # Axil espurio en I
        (0.0, P, P * L, 0.0, -P, P * L),  # Momento espurio en J
    ],
    ids=["v_j", "n_i", "m_j"],
)
def test_equilibrium_detects_single_end_misreads(
    local_force: tuple[float, float, float, float, float, float],
) -> None:
    """Revisión M3: un error O(1) en un solo extremo no pasa la postcondición D7."""
    frame, run = cantilever()
    assert run.raw is not None
    (member,) = run.raw.members
    broken = replace(run, raw=replace(run.raw, members=(replace(member, local_force=local_force),)))
    with pytest.raises(ResultsNormalizationError):
        normalize_planar_xz(broken, frame.model)


def test_equilibrium_detects_wrong_spring_reaction_sign() -> None:
    """Reacción auxiliar con signo invertido: el equilibrio del nudo la rechaza (M3)."""
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    support = frame.model.set_support(b.id, roller_restraints(), basis=inclined(30.0))
    frame.nodal(b, fz=-P)
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    r = 100.0
    raw = RawPlanarResults(
        "SOLVER_XZ_RAW",
        (
            RawNodeResult(a.id, (0.0, 0.0, 0.0), (r * s, P - r * c, (P - r * c) * L)),
            RawNodeResult(b.id, (0.0, 0.0, 0.0), (None, None, None)),
        ),
        (RawSpringReaction(support.id, b.id, 2, True, (r * s, -r * c, 0.0)),),  # signo mal
        (
            RawMemberForces(
                member.id, (r * s, P - r * c, (P - r * c) * L, -r * s, -(P - r * c), 0.0)
            ),
        ),
    )
    with pytest.raises(ResultsNormalizationError):
        normalize_planar_xz(synthetic_run(frame, raw), frame.model)


def test_unreadable_configuration_is_a_mismatch() -> None:
    """Revisión O4: una configuración que no se puede reconstruir es REVISION_CONFLICT."""
    frame, run = cantilever()
    broken = replace(run, configuration={**run.configuration, "unknown": 1})
    with pytest.raises(InputValueError) as info:
        normalize_planar_xz(broken, frame.model)
    assert info.value.code is DiagnosticCode.REVISION_CONFLICT


def test_axes_coherence_rejects_wrong_branch() -> None:
    frame, _run = cantilever()
    end = normalized._End(Vec3(0.0, 0.0, P), Vec3(0.0, -P * L, 0.0))
    iz_axes = (Vec3(1.0, 0.0, 0.0), Vec3(0.0, 0.0, 1.0), Vec3(0.0, -1.0, 0.0))
    with pytest.raises(ResultsNormalizationError) as info:
        normalized._member_vector(end, iz_axes, "Iy", frame.config())
    assert info.value.diagnostic.message_key == "fem.results_axes_inconsistent"


def test_alpha_pi_uses_current_axes_and_records_them() -> None:
    """D8-A: la Q usada queda registrada; con alfa = π, Fz y My locales cambian de signo."""
    frame, run = cantilever(alpha=math.pi)
    (member,) = normalize_planar_xz(run, frame.model).members
    assert member.axes[2].z == pytest.approx(-1.0)
    assert member.p_i.values()[2] == pytest.approx(-P)
    assert member.p_i.values()[4] == pytest.approx(P * L)


def test_component_invariant() -> None:
    with pytest.raises(ValueError, match="None"):
        Component(None, ComponentStatus.CALCULATED)
    with pytest.raises(ValueError, match="None"):
        Component(0.0, ComponentStatus.NOT_APPLICABLE)


def test_argument_types() -> None:
    frame, run = cantilever()
    with pytest.raises(TypeError):
        normalize_planar_xz(object(), frame.model)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        normalize_planar_xz(run, object())  # type: ignore[arg-type]
