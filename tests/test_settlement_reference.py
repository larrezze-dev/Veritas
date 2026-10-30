"""Batería de asientos T-ASI-01…10 de ``fem.benchmarks`` (plan T-016, E2.3).

Las referencias que imprime ``veritas analyze asientos`` se comparan con las mismas fórmulas
calculadas aquí en fracciones, sin el solver ni el módulo ``benchmarks``. Son las de
``test_fem_settlements.py`` (T-ASI-01…06) y ``test_fem3d_settlements.py`` (T-ASI-07 y 08),
revisadas en E2.1 y E2.2. Así la batería no puede mostrar una referencia distinta de la
revisada. T-ASI-09 suma T-ASI-01 y la biempotrada con q hacia −Z: reacciones verticales
+wL/2, momentos de empotramiento ∓wL²/12 y flecha central −wL⁴/(384EI). El signo lo fija
ADR-001 §2 (My > 0 es tracción superior: voladizo My(s) = P(L−s)): la carga hacia −Z da
My(0+) B1 > 0 y, con q(0+) = −pI = −R, R_My N1 = −wL²/12; ΣMy respecto a N1 = 0 (My = −x·Fz).
"""

import dataclasses
from fractions import Fraction

import pytest

import benchmark_cache
from fem_support import AREA, E_PA, IY, IZ
from veritas.core.loads.loads import SupportSettlement
from veritas.core.messages_es import MESSAGES_ES, render_text
from veritas.core.presentation import QuantityKind
from veritas.core.sections.rectangular import rectangular_properties
from veritas.fem import RunStatus, benchmarks

pytestmark = pytest.mark.solver

L = Fraction(4)
EI = Fraction(E_PA) * Fraction(IY)
EA = Fraction(E_PA) * Fraction(AREA)
EIZ = Fraction(E_PA) * Fraction(IZ)
_J = rectangular_properties(0.2, 0.4).torsion_constant.value
assert _J is not None
GJ = Fraction(81e9) * Fraction(_J)
DELTA = Fraction(-1, 100)  # m
THETA = Fraction(2, 1000)  # rad
AXIAL = Fraction(1, 1000)  # m
W = Fraction(10_000)  # N/m

F1, M1 = 12 * EI * DELTA / L**3, 6 * EI * DELTA / L**2
F2, M2 = 3 * EI * DELTA / L**3, 3 * EI * DELTA / L**2
F5 = 6 * EI * DELTA / L**3
N6 = EA * AXIAL / L
T7 = GJ * THETA / L
F8, M8 = 3 * EIZ * DELTA / L**3, 3 * EIZ * DELTA / L**2
F8B = 24 * EIZ * DELTA / (5 * L**3)

REFERENCES: dict[str, dict[str, Fraction]] = {
    "T-ASI-01": {
        "R_Z N3": F1,
        "R_Z N1": -F1,
        "R_X N1": Fraction(0),
        "R_My N1": M1,
        "R_My N3": M1,
        "My(0+) B1": -M1,
        "Uz N3": DELTA,
        "Uz N2": DELTA / 2,
        "Ry N2": -3 * DELTA / (2 * L),
    },
    "T-ASI-02": {
        "R_Z N3": F2,
        "R_Z N1": -F2,
        "R_My N1": M2,
        "My(0+) B1": -M2,
        "Uz N3": DELTA,
        "Ry N3": -3 * DELTA / (2 * L),
    },
    "T-ASI-03": {
        "R_Z N1": Fraction(0),
        "R_Z N3": Fraction(0),
        "R_X N1": Fraction(0),
        "My(L−) B1": Fraction(0),
        "Vz(0+) B1": Fraction(0),
        "Uz N2": DELTA / 2,
        "Uz N3": DELTA,
        "Ry N1": -DELTA / L,
        "Ry N2": -DELTA / L,
        "Ry N3": -DELTA / L,
    },
    "T-ASI-04": {
        "R_My N1": 4 * EI * THETA / L,
        "R_My N3": 2 * EI * THETA / L,
        "R_Z N1": -6 * EI * THETA / L**2,
        "R_Z N3": 6 * EI * THETA / L**2,
        "My(0+) B1": -4 * EI * THETA / L,
        "Ry N1": THETA,
    },
    "T-ASI-05": {
        "R_Z N3": F5,
        "R_Z N1": -F5 / 2,
        "R_Z N5": -F5 / 2,
        "My(L−) B2": 3 * EI * DELTA / L**2,
        "Uz N3": DELTA,
    },
    "T-ASI-06": {
        "R_X N3": N6,
        "R_X N1": -N6,
        "N(0+) B1": N6,
        "N(0+) B2": N6,
        "Ux N2": AXIAL / 2,
        "Ux N3": AXIAL,
    },
    "T-ASI-07": {
        "R_MX N3": T7,
        "R_MX N1": -T7,
        "T(0+) B1": T7,
        "T(0+) B2": T7,
        "Rx N3": THETA,
        "Rx N2": THETA / 2,
    },
    "T-ASI-08 a": {
        "R_Y N3": F8,
        "R_Y N1": -F8,
        "R_MZ N1": -M8,
        "R_MZ N3": Fraction(0),
        "Uy N3": DELTA,
    },
    "T-ASI-08 b": {
        "R_Y N3": F8B,
        "R_Y N1": -F8B,
        "R_MZ N1": -18 * EIZ * DELTA / (5 * L**2),
        "R_MZ N3": -6 * EIZ * DELTA / (5 * L**2),
        "Uy N3": DELTA,
    },
}
SUPERPOSITION: dict[str, Fraction] = {
    "R_Z N3": W * L / 2 + F1,
    "R_Z N1": W * L / 2 - F1,
    "R_My N1": -W * L**2 / 12 + M1,
    "R_My N3": W * L**2 / 12 + M1,
    "Uz N2": DELTA / 2 - W * L**4 / (384 * EI),
    "Ry N2": -3 * DELTA / (2 * L),
}
EXPECTED_IDS = [
    *REFERENCES,
    "T-ASI-09 plano",
    "T-ASI-09 espacial",
    *(f"T-ASI-10 · T-ASI-0{index}" for index in range(1, 7)),
]


Outcomes = dict[str, benchmarks.ReferenceOutcome | benchmarks.SpatialReferenceOutcome]


@pytest.fixture(scope="module")
def outcomes() -> Outcomes:
    return {o.case_id: o for o in benchmark_cache.run_settlement_battery()}


def formula_checks(
    outcome: benchmarks.ReferenceOutcome | benchmarks.SpatialReferenceOutcome,
) -> dict[str, benchmarks.ReferenceCheck]:
    """Filas de fórmula (ni «A + Q» ni 3D frente a plano), por magnitud."""
    skipped = {benchmarks.SUPERPOSED, benchmarks.PLANAR, benchmarks.OUT_OF_PLANE_ZERO}
    return {c.quantity: c for c in outcome.checks if c.formula not in skipped}


def assert_references(
    checks: dict[str, benchmarks.ReferenceCheck], expected: dict[str, Fraction]
) -> None:
    assert checks.keys() == expected.keys()
    for name, value in expected.items():
        assert checks[name].reference == pytest.approx(float(value), rel=1e-12, abs=1e-15), name


def test_battery_order_and_all_match(outcomes: Outcomes) -> None:
    assert list(outcomes) == EXPECTED_IDS
    for case_id, outcome in outcomes.items():
        assert outcome.matches, case_id
        assert outcome.failure is None, case_id


@pytest.mark.parametrize("case_id", list(REFERENCES))
def test_references_match_reviewed_fractions(outcomes: Outcomes, case_id: str) -> None:
    assert_references(formula_checks(outcomes[case_id]), REFERENCES[case_id])


def test_adapters_by_case(outcomes: Outcomes) -> None:
    for case_id, outcome in outcomes.items():
        spatial = isinstance(outcome, benchmarks.SpatialReferenceOutcome)
        expected = case_id[:8] in ("T-ASI-07", "T-ASI-08", "T-ASI-10") or "espacial" in case_id
        assert spatial is expected, case_id


@pytest.mark.parametrize("case_id", ["T-ASI-09 plano", "T-ASI-09 espacial"])
def test_superposition_has_formulas_and_linearity(outcomes: Outcomes, case_id: str) -> None:
    outcome = outcomes[case_id]
    assert_references(formula_checks(outcome), SUPERPOSITION)
    summed = [c for c in outcome.checks if c.formula == benchmarks.SUPERPOSED]
    names = {c.quantity for c in summed}
    # Desplazamientos, reacciones y pI/pJ de las dos barras, en el plano y en 3D.
    for name in ("Uz N2", "R_Z N3", "R_MY N1", "pI_My B1", "pJ_Vz B2"):
        assert name in names, name
    if "espacial" in case_id:
        assert "Uy N2" in names  # fuera del plano: el 3D también las publica
    assert all(c.matches and c.tolerance == benchmarks.TOL_EXACT for c in summed)


def test_superposition_rows_are_sum_of_separate_runs() -> None:
    parts = []
    for part in benchmarks.SUPERPOSITION_PARTS:
        case = benchmarks._Case()
        benchmarks._build_asi09(part)(case)
        run, results, _failure = benchmarks._planar_run(case, None)
        assert results is not None, run.diagnostics
        case.results = results
        parts.append(case)
    rows = benchmarks._superposition_rows(*parts)
    alone, loaded, joint = (benchmarks._vectors(part) for part in parts)
    (row,) = (r for r in rows if r[0] == "R_Z N3")
    a, q, j = (vectors[("R", "N3")][0].components[2].value for vectors in (alone, loaded, joint))
    assert a is not None
    assert q is not None
    assert row[3:] == (a + q, j)


def test_cross_contrast_covers_planar_cases(outcomes: Outcomes) -> None:
    for index in range(1, 7):
        outcome = outcomes[f"T-ASI-10 · T-ASI-0{index}"]
        assert isinstance(outcome, benchmarks.SpatialReferenceOutcome)
        assert outcome.planar is not None
        assert outcome.planar.matches
        assert any(c.formula == benchmarks.PLANAR for c in outcome.checks)
        assert any(c.formula == benchmarks.OUT_OF_PLANE_ZERO for c in outcome.checks)
        expected = REFERENCES[f"T-ASI-0{index}"]
        assert_references(formula_checks(outcome), expected)


def test_out_of_plane_fixes_keep_the_settlement() -> None:
    """``_fix_out_of_plane`` solo pasa GDL FREE a FIXED con la misma base: D2 lo admite."""
    case = benchmarks._Case()
    benchmarks._build_asi03(case)
    (settlement,) = (x for x in case.model.loads.values() if isinstance(x, SupportSettlement))
    revision = case.model.supports[settlement.support_id].revision
    benchmarks._fix_out_of_plane(case)
    assert case.model.supports[settlement.support_id].revision > revision
    assert settlement in case.model.loads.values()


def test_axial_case_declares_moment_scale() -> None:
    (spec,) = benchmarks.SETTLEMENT_SPECS["T-ASI-06"]
    assert spec.scales is not None
    assert dict(spec.scales) == pytest.approx({QuantityKind.MOMENT: float(EA * AXIAL)})
    (spec,) = benchmarks.SETTLEMENT_SPECS["T-ASI-03"]
    assert spec.scales is not None
    assert dict(spec.scales) == pytest.approx(
        {
            QuantityKind.FORCE: float(3 * EI * -DELTA / L**3),
            QuantityKind.MOMENT: float(3 * EI * -DELTA / L**2),
        }
    )


def alias_notes(outcome: object) -> list[benchmarks.Text]:
    notes = getattr(outcome, "notes", ())
    return [n for n in notes if n.key == "benchmark.note_settlement_alias"]


def test_spatial_runs_record_settlement_control_ratio(outcomes: Outcomes) -> None:
    """Obs. 5 de la revisión de E2.2: una nota por GDL asentado en cada corrida 3D.

    Razón ≤ cota es más estricto que el control (la razón publicada es ≥ la suya); vale en
    esta batería, donde los aux comparten las traslaciones del nudo asentado.
    """
    for case_id, outcome in outcomes.items():
        notes = alias_notes(outcome)
        if not isinstance(outcome, benchmarks.SpatialReferenceOutcome):
            assert not notes, case_id
            continue
        assert len(notes) == 1, case_id
        params = dict(notes[0].params)
        assert isinstance(params["ratio"], float)
        assert isinstance(params["bound"], float)
        assert 0.0 <= params["ratio"] <= params["bound"], case_id
        assert params["node"] in ("N1", "N3"), case_id


def test_settlement_notes_without_results_are_empty(outcomes: Outcomes) -> None:
    outcome = outcomes["T-ASI-07"]
    assert isinstance(outcome, benchmarks.SpatialReferenceOutcome)
    run = dataclasses.replace(outcome.run, raw=None, status=RunStatus.FAILED)
    assert benchmarks._settlement_notes(run, outcome.labels) == []


def test_unknown_settlement_case_is_rejected() -> None:
    with pytest.raises(ValueError, match="desconocido"):
        benchmarks.run_settlement_reference("T-ASI-09")


def test_settlement_constants() -> None:
    assert float(DELTA) == benchmarks.SETTLEMENT
    assert float(THETA) == benchmarks.SETTLEMENT_ROTATION
    assert float(AXIAL) == benchmarks.SETTLEMENT_AXIAL
    assert tuple(f"T-ASI-{i:02d}" for i in range(1, 11)) == benchmarks.SETTLEMENT_CASES


def test_settlement_texts_render_from_catalog(outcomes: Outcomes) -> None:
    texts = [*benchmarks.SETTLEMENT_HYPOTHESES]
    for specs in benchmarks.SETTLEMENT_SPECS.values():
        for spec in specs:
            texts += [spec.title, spec.description]
    for outcome in outcomes.values():
        texts += [outcome.title, outcome.description, *outcome.notes]
    for text in texts:
        assert text.key in MESSAGES_ES, text.key
        rendered = render_text(text.key, dict(text.params))
        assert "{" not in rendered, text.key
