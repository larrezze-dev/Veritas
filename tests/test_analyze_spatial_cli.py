"""CLI ``veritas analyze espacial`` (plan T-018, D7-A, pieza 7).

Casos ``referencias``, ``contraste-plano``, ``mecanismo-torsion`` y ``uniones`` (E2.6) con sus
códigos de salida,
y la comparación de la salida aceptada de ``analyze demo|uniones|referencias|asientos`` con
``tests/golden`` (normalizada en ``cli_golden``).
"""

import dataclasses

import pytest

import benchmark_cache
import cli_golden
from veritas import cli
from veritas.core.connections.end import Dof, DofState, EndConnection
from veritas.core.messages_es import MESSAGES_ES
from veritas.core.units import Quantity
from veritas.fem import RunStatus, benchmarks

pytestmark = pytest.mark.solver


@pytest.fixture(autouse=True)
def cached_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las corridas de referencia y de contraste, una vez por sesión (``benchmark_cache``)."""
    monkeypatch.setattr(benchmarks, "run_spatial_reference", benchmark_cache.run_spatial_reference)
    monkeypatch.setattr(benchmarks, "run_cross_planar", benchmark_cache.run_cross_planar)
    monkeypatch.setattr(
        benchmarks, "run_settlement_battery", benchmark_cache.run_settlement_battery
    )
    monkeypatch.setattr(
        benchmarks, "run_peso_propio_battery", benchmark_cache.run_peso_propio_battery
    )


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = cli.main(["analyze", "espacial", *args])
    return code, capsys.readouterr().out


def test_references_all_match(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--caso", "referencias", "--profile", "kN-m")
    assert code == cli.EXIT_OK
    assert "Referencias analíticas espaciales (plan T-018, D8):" in out
    for case in ("T-FEM3D-01 a", "T-FEM3D-02 b", "T-FEM3D-03 b", "T-FEM3D-04 a", "T-FEM3D-05 a"):
        assert f"== {case} — " in out
    assert "Las 10 variantes coinciden" in out
    assert cli.REFERENCE_MISMATCH not in out
    assert "J (= 0.00073178 m⁴" in out  # hipótesis con la J de la sección
    assert " kN·m " in out
    assert cli.EXPERIMENTAL_WARNING in out


def test_default_case_is_references(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys)
    assert code == cli.EXIT_OK
    assert "Referencias analíticas espaciales" in out


def test_cross_planar_matches(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--caso", "contraste-plano")
    assert code == cli.EXIT_OK
    assert "Contraste espacial-plano de T-FEM-001…004 y T-CON-REF-01…04" in out
    assert out.count("Corrida plana del contraste: SUCCEEDED") == 10
    assert "== T-CON-REF-04 — " in out
    assert "fórmulas del plano: coincide" in out
    assert (
        "Las 10 variantes coinciden con las fórmulas del caso plano y con el adaptador plano" in out
    )
    assert " 0 (fuera del plano) " in out


def test_torsion_mechanism_exits_3_with_mode(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--caso", "mecanismo-torsion")
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert "Estado: FAILED" in out
    assert "ERROR ANALYSIS_SINGULAR" in out
    assert "Modo de mecanismo (nulidad 1" in out
    for node in ("N1", "N2", "N3"):
        assert f"nudo {node}, Rx" in out
    assert cli.SPATIAL_TORSION_EXPECTED in out
    assert cli.SPATIAL_TORSION_MISSED not in out
    assert cli.NO_RESULTS_FAILED in out


def test_mismatch_prints_no_coincide_and_exit_3(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = benchmarks.run_spatial_reference

    def wrong(case_id: str, timeout_s: float | None = None) -> tuple[object, ...]:
        outcomes = real(case_id, timeout_s)
        if case_id != "T-FEM3D-02":
            return outcomes
        first = outcomes[0]
        check = dataclasses.replace(first.checks[0], reference=first.checks[0].reference * 2)
        return (dataclasses.replace(first, checks=(check, *first.checks[1:])), *outcomes[1:])

    monkeypatch.setattr(benchmarks, "run_spatial_reference", wrong)
    code, out = run(capsys)
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert f"{cli.REFERENCE_MISMATCH}: T-FEM3D-02 a." in out
    assert "Las 10 variantes coinciden" not in out


def test_failed_run_is_not_shown_as_match(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = benchmarks.run_cross_planar

    def failed(case_id: str, timeout_s: float | None = None) -> tuple[object, ...]:
        outcomes = real(case_id, timeout_s)
        if case_id != "T-FEM-001":
            return outcomes
        run_ = dataclasses.replace(outcomes[0].run, status=RunStatus.FAILED, raw=None)
        empty = dataclasses.replace(outcomes[0], run=run_, results=None, checks=())
        return (empty, *outcomes[1:])

    monkeypatch.setattr(benchmarks, "run_cross_planar", failed)
    code, out = run(capsys, "--caso", "contraste-plano")
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert f"T-FEM-001: {cli.REFERENCE_NO_RESULTS}" in out
    assert f"{cli.REFERENCE_NO_RESULTS}: T-FEM-001." in out


def test_failed_planar_contrast_run_has_no_results(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revisión de E1: una corrida plana FAILED del contraste no dice «NO COINCIDE»."""
    real = benchmarks.run_cross_planar

    def failed(case_id: str, timeout_s: float | None = None) -> tuple[object, ...]:
        outcomes = real(case_id, timeout_s)
        if case_id != "T-FEM-002":
            return outcomes
        planar = outcomes[0].planar
        assert planar is not None
        run_ = dataclasses.replace(planar.run, status=RunStatus.FAILED, raw=None)
        empty = dataclasses.replace(planar, run=run_, results=None, checks=())
        return (dataclasses.replace(outcomes[0], planar=empty), *outcomes[1:])

    monkeypatch.setattr(benchmarks, "run_cross_planar", failed)
    code, out = run(capsys, "--caso", "contraste-plano")
    assert code == cli.EXIT_ANALYSIS_FAILED
    line = next(x for x in out.splitlines() if "Corrida plana del contraste: FAILED" in x)
    assert line.endswith(f"fórmulas del plano: {cli.REFERENCE_NO_RESULTS}")
    assert cli.REFERENCE_MISMATCH not in line


def test_unknown_case_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["analyze", "espacial", "--caso", "desconocido"])
    assert error.value.code == cli.EXIT_USAGE
    assert "invalid choice" in capsys.readouterr().err


def test_invalid_timeout_is_diagnostic(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "espacial", "--timeout", "-1"]) == cli.EXIT_DIAGNOSTIC
    assert capsys.readouterr().err


def test_help_documents_oblique_components(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["analyze", "espacial", "--help"])
    out = capsys.readouterr().out
    for token in ("t1/t2", "r1/r2", "mecanismo-torsion", "contraste-plano", "3 corrida FAILED"):
        assert token in out


def test_mechanism_text_for_oblique_components() -> None:
    from uuid import uuid4  # noqa: PLC0415

    from veritas.fem import MechanismDof  # noqa: PLC0415

    node = uuid4()
    item = MechanismDof(node_id=node, component="t1", participation=0.5)
    text = cli._mechanism_dof_text(item, {node: "N7"})
    assert text == "nudo N7, traslación oblicua t1 (0.5)"
    item = MechanismDof(node_id=node, component="r2", participation=0.25)
    assert cli._mechanism_dof_text(item, {node: "N7"}) == "nudo N7, giro oblicuo r2 (0.25)"


@pytest.mark.parametrize("name", sorted(cli_golden.COMMANDS))
def test_accepted_analyze_output_is_unchanged(name: str) -> None:
    """D7-A: la salida aceptada de ``analyze demo|uniones|referencias`` no cambia."""
    golden = (cli_golden.GOLDEN_DIR / f"{name}.txt").read_text(encoding="utf-8")
    assert cli_golden.capture(cli_golden.COMMANDS[name]) == golden


# --- uniones (E2-e, E2.6) -------------------------------------------------------------------


@pytest.fixture(scope="module")
def unions_output() -> tuple[str, int]:
    return cli.run_analyze_spatial("uniones", "60")


def test_unions_show_the_end_jump_of_both_families(unions_output: tuple[str, int]) -> None:
    """T-020, E2.2: salto del extremo J de B2 en las dos familias, en ejes locales."""
    out, code = unions_output
    assert code == cli.EXIT_OK
    assert "Salto de extremo J de B2 (extremo de la barra − nudo N2, Ry local; T-020):" in out
    assert "Salto de extremo J de B2 (extremo de la barra − nudo N2, Rz local; T-020):" in out
    assert out.count("Rígida: — (extremo rígido, sin salto)") == 2
    assert "referencia −P·L²/(32·EI) = -2.232e-05 rad" in out  # familia Ry
    assert "referencia +P·L²/(32·EI) = 8.929e-05 rad" in out  # familia Rz (signo del giro)
    assert out.count("Contraste del salto (tolerancia 1e-09, resorte 1e-06): coincide.") == 2


def test_unions_match_and_exit_0(unions_output: tuple[str, int]) -> None:
    """E2.6-c: un caso con el mecanismo incluido, como ``analyze uniones``; sale con 0."""
    out, code = unions_output
    assert code == cli.EXIT_OK
    assert out.count("Contraste de uniones: coincide.") == 2
    assert "Familia Ry:" in out
    assert "Familia Rz:" in out
    for row in ("u·ez N3 (centro)", "M·ey N1 (reacción)", "My extremo J de B2"):
        assert row in out
    for row in ("u·ey N3 (centro)", "M·ez N1 (reacción)", "Mz extremo J de B2"):
        assert row in out
    assert "M·ex N1 (torsión)" in out
    assert cli.REFERENCE_MISMATCH not in out
    assert f"tolerancia {benchmarks.TOL_PENALTY:g})" in out
    assert "[Mecanismo] Estado: FAILED" in out
    assert "ERROR ANALYSIS_SINGULAR" in out
    assert "Modo de mecanismo (nulidad 1" in out
    assert cli.SPATIAL_UNIONS_MISSED not in out
    assert cli.EXPERIMENTAL_WARNING in out
    # Revisión de E2, m8: también las hipótesis de «Liberada», una vez por familia.
    assert out.count(MESSAGES_ES["fem3d.hypothesis_end_releases"]) == 2


def _family_solved(family: cli._UnionsFamily) -> list[cli._Solved]:
    ei = cli.ANALYZE_DEMO_E * 1e6 * family.inertia
    k = cli.UNIONS_SPRING_RATIO * ei / cli.UNIONS_L
    spring = EndConnection.semi_rigid(family.dof, Quantity(k, family.dof.stiffness_dimension))
    released = EndConnection.rigid().with_dof(family.dof, DofState.RELEASED)
    variants = [
        cli._spatial_unions_variant("Rígida", family, None),
        cli._spatial_unions_variant("Liberada", family, released),
        cli._spatial_unions_variant("Resorte", family, spring),
    ]
    return [cli._solve_spatial(variant, 60.0) for variant in variants]


@pytest.mark.parametrize("family", cli.UNIONS_FAMILIES, ids=["Ry", "Rz"])
def test_unions_spring_ratio_is_the_hand_value(family: cli._UnionsFamily) -> None:
    """Revisión m3: con k = r·EI/L sobre la EI del plano probado, s = r/(r + 4) en las dos
    familias (r = 1: s = 1/5). Un resorte con la EI del otro eje daría otro s."""
    solved = _family_solved(family)
    _rows, _pairs, contrast = cli.spatial_unions_contrast(family, solved)
    assert contrast.matches
    ratio = cli.UNIONS_SPRING_RATIO
    assert contrast.spring.ratio == pytest.approx(ratio / (ratio + 4), abs=benchmarks.TOL_PENALTY)
    member = next(m for m in solved[2].variant.model.members.values() if m.label == "B2")
    assert member.end_j.state(family.dof) is DofState.SPRING
    other = Dof.RY if family.dof is Dof.RZ else Dof.RZ
    assert member.end_j.state(other) is DofState.RIGID
    assert "fem3d.hypothesis_end_penalty_iterated" in solved[2].run.hypotheses
    for item in solved[:2]:
        assert "fem3d.hypothesis_end_penalty_iterated" not in item.run.hypotheses


def test_unions_mechanism_is_the_hinge() -> None:
    """Revisión o2: nulidad 1; participan las traslaciones y giros de N3 y el giro libre de
    los apoyos (r1 en N1 y N2), con la traslación de N3 como principal."""
    variant = cli._spatial_unions_variant("Mecanismo", cli.UNIONS_FAMILIES[0], None, mechanism=True)
    run_ = cli._solve_spatial(variant, 60.0).run
    assert run_.status is RunStatus.FAILED
    assert run_.pivot is not None
    assert run_.pivot.nullity == 1
    labels = variant.labels
    participants = {(labels[d.node_id], d.component) for d in run_.pivot.mechanism}
    expected_n3 = {("N3", c) for c in ("Ux", "Uy", "Uz", "Rx", "Ry", "Rz")}
    assert participants == {("N1", "r1"), ("N2", "r1"), *expected_n3}
    principal = max(run_.pivot.mechanism, key=lambda d: d.participation)
    assert (labels[principal.node_id], principal.component) == ("N3", "Uz")
    # Sin penalización de extremo; el giro libre oblicuo del apoyo declara la D4, que no
    # interviene: la salvaguarda detiene la corrida antes de OpenSees.
    assert "fem3d.hypothesis_end_penalty_iterated" not in run_.hypotheses
    assert "fem3d.hypothesis_penalty" in run_.hypotheses
    # Sin coordenadas de aux: la rótula va con -releasey en J de B1.
    assert all(d.member_id is None for d in run_.pivot.mechanism)
    member = next(m for m in variant.model.members.values() if m.label == "B1")
    assert member.end_j.state(Dof.RY) is DofState.RELEASED


def test_unions_altered_reference_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    real = cli.unions_references

    def altered(load: float, length: float, ei: float) -> list[tuple[float, float]]:
        rows = real(load, length, ei)
        return [(rows[0][0] * 2, rows[0][1]), *rows[1:]]

    monkeypatch.setattr(cli, "unions_references", altered)
    out, code = cli.run_analyze_spatial("uniones", "60")
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert f"Contraste de uniones: {cli.REFERENCE_MISMATCH}." in out


def test_unions_missed_mechanism_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    real = cli._spatial_unions_variant

    def no_mechanism(
        name: str,
        family: cli._UnionsFamily,
        end_j: EndConnection | None,
        *,
        mechanism: bool = False,
    ) -> cli._Variant:
        return real(name, family, end_j, mechanism=False)

    monkeypatch.setattr(cli, "_spatial_unions_variant", no_mechanism)
    out, code = cli.run_analyze_spatial("uniones", "60")
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert cli.SPATIAL_UNIONS_MISSED in out


def test_unions_mechanism_without_mode_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decisión D-2 de la revisión de E2: ANALYSIS_SINGULAR sin modo concluyente no basta."""
    real = cli._solve_spatial

    def no_mode(variant: cli._Variant, timeout: float) -> cli._Solved:
        solved = real(variant, timeout)
        if variant.name != "Mecanismo" or solved.run.pivot is None:
            return solved
        pivot = dataclasses.replace(solved.run.pivot, nullity=None, mechanism=())
        return dataclasses.replace(solved, run=dataclasses.replace(solved.run, pivot=pivot))

    monkeypatch.setattr(cli, "_solve_spatial", no_mode)
    out, code = cli.run_analyze_spatial("uniones", "60")
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert cli.SPATIAL_UNIONS_MISSED in out


def test_unions_failed_variant_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisión de E2, M2: una variante FAILED (aquí, «Resorte» por tiempo agotado) no
    publica resultados y la salida es 3 con «no coincide», nunca 0."""
    real = cli._solve_spatial

    def failed_spring(variant: cli._Variant, timeout: float) -> cli._Solved:
        return real(variant, 1e-3 if variant.name == "Resorte" else timeout)

    monkeypatch.setattr(cli, "_solve_spatial", failed_spring)
    out, code = cli.run_analyze_spatial("uniones", "60")
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert f"[Resorte] Estado: {RunStatus.FAILED}" in out
    assert f"Contraste de uniones: {cli.REFERENCE_MISMATCH}." in out
    assert f"Contraste de uniones: {cli.REFERENCE_MATCH}." not in out


def test_spatial_help_lists_unions(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["analyze", "espacial", "--help"])
    out = capsys.readouterr().out
    assert "uniones" in out
    assert "T-CON-REF-01…04" in out
