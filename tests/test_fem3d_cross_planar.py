"""Contraste cruzado espacial-plano de T-FEM-001…004 (plan T-018, D1).

El modelo plano, con Uy, Rx y Rz fijos en todos los nudos, se analiza con los dos
adaptadores. Se comprueban las fórmulas del caso plano sobre el 3D (1e-9), el 3D frente al
plano componente a componente (1e-9; 1e-6 en T-FEM-004, donde el plano penaliza) y las
componentes fuera del plano frente a 0.
"""

import pytest

import benchmark_cache
from veritas.core.supports.support import RestraintKind, SupportDof
from veritas.fem import RunStatus, benchmarks
from veritas.fem.spatial.results import SpatialAnalysisRun

pytestmark = pytest.mark.solver

CONTRAST = {benchmarks.PLANAR, benchmarks.OUT_OF_PLANE_ZERO}


@pytest.fixture(scope="module")
def outcomes() -> dict[str, benchmarks.SpatialReferenceOutcome]:
    return {
        o.case_id: o
        for case in benchmarks.CROSS_CASES
        for o in benchmark_cache.run_cross_planar(case)
    }


def test_all_cross_cases_match(outcomes: dict[str, benchmarks.SpatialReferenceOutcome]) -> None:
    assert set(outcomes) == {"T-FEM-001", "T-FEM-002", "T-FEM-003 a", "T-FEM-003 b", "T-FEM-004"}
    for case_id, outcome in outcomes.items():
        failing = [c.quantity for c in outcome.checks if not c.matches]
        assert outcome.matches, (case_id, failing)
        assert outcome.planar is not None
        assert outcome.planar.matches


def test_formula_rows_are_exact_in_3d(
    outcomes: dict[str, benchmarks.SpatialReferenceOutcome],
) -> None:
    """Las filas de fórmula llevan 1e-9 también en T-FEM-004: el rodillo es exacto en 3D."""
    for outcome in outcomes.values():
        formulas = [c for c in outcome.checks if c.formula not in CONTRAST]
        assert formulas
        assert all(c.tolerance == benchmarks.TOL_EXACT for c in formulas)


def test_contrast_tolerance_follows_planar(
    outcomes: dict[str, benchmarks.SpatialReferenceOutcome],
) -> None:
    for case_id, outcome in outcomes.items():
        contrast = [c for c in outcome.checks if c.formula in CONTRAST]
        expected = benchmarks.TOL_PENALTY if case_id == "T-FEM-004" else benchmarks.TOL_EXACT
        assert contrast
        assert {c.tolerance for c in contrast} == {expected}


def test_out_of_plane_rows_are_present(
    outcomes: dict[str, benchmarks.SpatialReferenceOutcome],
) -> None:
    checks = outcomes["T-FEM-001"].checks
    names = {c.quantity for c in checks if c.formula == benchmarks.OUT_OF_PLANE_ZERO}
    # N2 no tiene apoyo en el plano: sus reacciones fuera del plano se comparan con 0.
    assert {"Uy N2", "Rx N2", "Rz N2", "R_Y N2", "R_MX N2", "R_MZ N2"} <= names
    assert {"pI_Vy B1", "pI_T B1", "pJ_Mz B2", "R_Y N1", "R_MZ N3"} <= names


def test_3d_inclined_roller_has_no_penalty(
    outcomes: dict[str, benchmarks.SpatialReferenceOutcome],
) -> None:
    for case_id in ("T-FEM-003 b", "T-FEM-004"):
        run = outcomes[case_id].run
        assert isinstance(run, SpatialAnalysisRun)
        assert run.status is RunStatus.SUCCEEDED
        assert "fem3d.hypothesis_penalty" not in run.hypotheses
    assert "fem3d.hypothesis_inclined_chain" in outcomes["T-FEM-004"].run.hypotheses


def test_fix_out_of_plane_keeps_existing_states() -> None:
    spec = benchmarks.SPECS["T-FEM-003"][1]  # resorte Uz′ en base inclinada
    case = benchmarks._Case()
    spec.build(case)
    benchmarks._fix_out_of_plane(case)
    supports = {case.model.nodes[s.node_id].label: s for s in case.model.supports.values()}
    assert set(supports) == {"N1", "N2", "N3"}
    n3 = supports["N3"]
    assert n3.restraint(SupportDof.UZ).kind is RestraintKind.SPRING
    assert n3.restraint(SupportDof.UX).kind is RestraintKind.FREE
    assert n3.restraint(SupportDof.RY).kind is RestraintKind.FREE
    for dof in (SupportDof.UY, SupportDof.RX, SupportDof.RZ):
        assert n3.restraint(dof).kind is RestraintKind.FIXED
        assert supports["N2"].restraint(dof).kind is RestraintKind.FIXED


def test_unknown_cross_case_is_rejected() -> None:
    with pytest.raises(ValueError, match="desconocido"):
        benchmarks.run_cross_planar("T-CON-REF-01")


def test_contrast_covers_every_planar_entity(
    outcomes: dict[str, benchmarks.SpatialReferenceOutcome],
) -> None:
    """Revisión M1: cada nudo, reacción y barra del plano tiene filas PLANAR en los 5 casos."""
    for case_id, outcome in outcomes.items():
        planar = outcome.planar
        assert planar is not None
        assert planar.results is not None
        names = {c.quantity for c in outcome.checks if c.formula == benchmarks.PLANAR}
        labels = planar.labels
        for node in planar.results.nodes:
            assert f"Ux {labels[node.node_id]}" in names, case_id
        for reaction in planar.results.reactions:
            assert any(
                n.endswith(f" {labels[reaction.node_id]}") and n.startswith("R_") for n in names
            ), case_id
        for member in planar.results.members:
            assert f"pI_N {labels[member.member_id]}" in names, case_id
            assert f"pJ_N {labels[member.member_id]}" in names, case_id


def test_missing_spatial_entity_does_not_match() -> None:
    """Una entidad del plano ausente en 3D da filas sin valor, que no coinciden."""
    spec = benchmarks.SPECS["T-FEM-001"][0]
    planar_case, planar = benchmarks._planar_case(spec, None)
    assert planar.results is not None
    rows = benchmarks._cross_rows(planar_case, _empty_like(planar_case))
    assert rows
    assert all(computed is None for *_rest, computed in rows)


def _empty_like(case: benchmarks._Case) -> benchmarks._Case:
    import dataclasses  # noqa: PLC0415

    other = benchmarks._Case()
    assert case.results is not None
    other.results = dataclasses.replace(case.results, nodes=(), reactions=(), members=())
    return other
