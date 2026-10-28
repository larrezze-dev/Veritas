"""CLI ``veritas analyze referencias`` y módulo ``fem.benchmarks`` (T-009, D1-A; T-010, D6).

Las referencias que imprime la demo se comparan con los literales del plan, para que la demo
no pueda mostrar una referencia errónea. Los tests de referencia frente al solver están en
``test_fem_reference.py`` y no usan este módulo.
"""

import dataclasses

import pytest

from veritas import cli
from veritas.core.messages_es import MESSAGES_ES, render_text
from veritas.core.presentation import QuantityKind
from veritas.fem import benchmarks

# Literales del plan T-009 (SI) para cada magnitud que imprime la demo.
PLAN_LITERALS: dict[str, dict[str, float]] = {
    "T-FEM-001": {
        "R_Z N1": 5000.0,
        "R_Z N3": 5000.0,
        "R_X N1": 0.0,
        "My(L−) B1": -10_000.0,
        "My(0+) B2": -10_000.0,
        "Vz(L−) B1": -5000.0,
        "Vz(0+) B2": 5000.0,
        "Uz N2": -5.95238e-5,
        "Ry N1": 4.46429e-5,
        "Ry N3": -4.46429e-5,
        "Ry N2": 0.0,
    },
    "T-FEM-002": {
        "R_Z N1": 20_000.0,
        "R_My N1": -20_000.0,
        "My(0+) B1": 20_000.0,
        "Vz(0+) B1": -20_000.0,
        "My(0+) B2": 5000.0,
        "My(L−) B2": 0.0,
        "Uz N2": -3.16220e-5,
        "Uz N3": -8.92857e-5,
        "Ry N3": 5.95238e-5,
    },
    "T-FEM-003 a": {
        "R_Z N1": 5000.0,
        "R_Z N3": 5000.0,
        "Uz N3": -5.0e-3,
        "Uz N2": -2.559524e-3,
        "Ry N1": 1.294643e-3,
    },
    "T-FEM-003 b": {
        "R_z′ N3": 5773.503,
        "R_X N1": 2886.751,
        "R_Z N3": 5000.0,
        "u·ez′ N3": -5.773503e-3,
    },
    "T-FEM-004": {
        "R_z′ N3": 5773.503,
        "R global N3 · ex′": 0.0,
        "R_X N1": 2886.751,
        "R_Z N1": 5000.0,
        "N(0+) B1": -2886.751,
        "Ux N3": -6.87322e-7,
        "Uz N3": -3.96825e-7,
        "Uz N2": -5.97222e-5,
    },
    # Plan T-010, D6: E = 210 GPa, R 200×400 (EI = 2,24e8 N·m², EA = 1,68e10 N), L = 4 m,
    # w = 10 kN/m, P = 10 kN, k = 3·EI/L = 1,68e8 N·m/rad; cercha con sen a = 0,6.
    "T-CON-REF-01 a": {
        "R_Z N1": 20_000.0,
        "R_Z N2": 20_000.0,
        "R_My N1": 0.0,
        "|M| extremo I B1": 0.0,
        "Ry N2": -1.190476e-4,
    },
    "T-CON-REF-01 b": {
        "R_Z N1": 22_500.0,
        "R_Z N2": 17_500.0,
        "R_My N1": -10_000.0,
        "|M| extremo I B1": 10_000.0,
        "Ry N2": -8.928571e-5,
    },
    "T-CON-REF-02": {
        "R_Z N1": 10_000.0,
        "R_My N1": -40_000.0,
        "Uz N2": -1.904762e-3,
        "Ry N2": 5.952381e-4,
    },
    "T-CON-REF-03": {
        "R_Z N1": 10_000.0,
        "R_Z N3": 0.0,
        "R_My N1": -40_000.0,
        "M extremo J B1 (rótula)": 0.0,
        "M extremo I B2": 0.0,
        "Uz N2": -9.523810e-4,
        "Ry N3": -2.380952e-4,
    },
    "T-CON-REF-04": {
        "R_Z N1": 5000.0,
        "R_Z N2": 5000.0,
        "R_X N1": 0.0,
        "N B1": 6666.667,
        "N B2": -8333.333,
        "N B3": -8333.333,
        "Uz N3": -3.125e-6,
        **{f"M extremo {end} {member}": 0.0 for member in ("B1", "B2", "B3") for end in "IJ"},
    },
}


def all_outcomes() -> list[benchmarks.ReferenceOutcome]:
    return [outcome for case in benchmarks.CASES for outcome in benchmarks.run_reference(case)]


@pytest.mark.solver
def test_demo_references_match_plan_literals() -> None:
    outcomes = all_outcomes()
    assert [o.case_id for o in outcomes] == list(PLAN_LITERALS)
    for outcome in outcomes:
        shown = {check.quantity: check.reference for check in outcome.checks}
        expected = PLAN_LITERALS[outcome.case_id]
        assert shown.keys() == expected.keys(), outcome.case_id
        for name, literal in expected.items():
            assert shown[name] == pytest.approx(literal, rel=1e-5, abs=1e-12), (
                outcome.case_id,
                name,
            )
        assert outcome.matches, outcome.case_id


@pytest.mark.solver
def test_cli_references_all_match(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "referencias", "--profile", "kN-m"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    for case_id in PLAN_LITERALS:
        assert f"== {case_id} " in out
    assert cli.REFERENCE_MISMATCH not in out
    assert cli.REFERENCE_NO_RESULTS not in out
    assert "Las 10 variantes coinciden" in out
    assert cli.REFERENCES_NOTE in out
    assert cli.EXPERIMENTAL_WARNING in out
    assert "-10 kN·m" in out  # My central de T-FEM-001
    assert "-0.0595238 mm" in out  # Uz central de T-FEM-001
    assert "5.7735 kN" in out  # R_z′ de T-FEM-003 b y -004
    assert "no aplicable (GDL libre)" in out
    for word in ("APROBADO", "aprobado", "VÁLIDO", "válido"):
        assert word not in out


@pytest.mark.solver
def test_cli_single_case(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "referencias", "--caso", "T-FEM-003"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "== T-FEM-003 a " in out
    assert "== T-FEM-003 b " in out
    assert "== T-FEM-001 " not in out
    assert "Las 2 variantes coinciden" in out


@pytest.mark.solver
def test_cli_mismatch_prints_no_coincide_and_exit_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = benchmarks.run_reference

    def altered(case_id: str, timeout_s: float | None = None) -> tuple[object, ...]:
        outcomes = real(case_id, timeout_s)
        return tuple(
            dataclasses.replace(
                outcome,
                checks=tuple(
                    dataclasses.replace(check, reference=check.reference * 1.01 + check.scale)
                    for check in outcome.checks
                ),
            )
            for outcome in outcomes
        )

    monkeypatch.setattr(benchmarks, "run_reference", altered)
    code = cli.main(["analyze", "referencias", "--caso", "T-FEM-001"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert cli.REFERENCE_MISMATCH in out
    assert "T-FEM-001: 0/11 magnitudes, NO COINCIDE." in out


@pytest.mark.solver
def test_cli_failed_run_is_not_shown_as_match(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["analyze", "referencias", "--caso", "T-FEM-002", "--timeout", "0.001"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert "Estado: FAILED" in out
    assert cli.NO_RESULTS_FAILED in out
    assert "T-FEM-002: SIN RESULTADOS" in out
    assert cli.REFERENCE_MISMATCH not in out
    assert "Magnitud" not in out


def test_unknown_case_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["analyze", "referencias", "--caso", "T-FEM-999"])
    assert info.value.code == cli.EXIT_USAGE
    assert capsys.readouterr().out == ""


def test_run_reference_rejects_unknown_case() -> None:
    with pytest.raises(ValueError, match="desconocido"):
        benchmarks.run_reference("T-FEM-999")


def test_evaluate_scales_by_dimension() -> None:
    force, moment = QuantityKind.FORCE, QuantityKind.MOMENT
    checks = benchmarks.evaluate(
        [
            ("F grande", "", force, 1000.0, 1000.0 + 5e-7),
            ("F nula", "", force, 0.0, 5e-7),  # Escala 1000 N: 5e-10 ≤ 1e-9.
            ("M", "", moment, 10.0, 10.0 + 2e-8),  # Escala propia 10 N·m: 2e-9 > 1e-9.
            ("Sin valor", "", force, 1.0, None),
        ],
        1e-9,
    )
    assert [c.matches for c in checks] == [True, True, False, False]
    assert checks[1].scale == 1000.0
    assert checks[2].scale == 10.0
    assert checks[3].relative_error is None


def test_evaluate_rejects_dimension_without_scale() -> None:
    with pytest.raises(ValueError, match="escala"):
        benchmarks.evaluate([("cero", "", QuantityKind.FORCE, 0.0, 0.0)], 1e-9)


def test_benchmark_texts_render_from_catalog() -> None:
    # P5: títulos, descripciones, hipótesis y notas vienen de messages_es, con parámetros.
    texts = [*benchmarks.HYPOTHESES]
    for specs in benchmarks.SPECS.values():
        for spec in specs:
            texts += [spec.title, spec.description]
    texts += [
        benchmarks.Text.of("benchmark.note_in_plane_not_applicable"),
        benchmarks.Text.of("benchmark.note_in_plane_status", status="CALCULATED"),
    ]
    for text in texts:
        assert text.key in MESSAGES_ES, text.key
        rendered = render_text(text.key, dict(text.params))
        assert rendered != MESSAGES_ES[text.key] or "{" not in MESSAGES_ES[text.key], text.key
        assert "{" not in rendered, text.key
