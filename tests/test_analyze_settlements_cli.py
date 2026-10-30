"""CLI ``veritas analyze asientos`` (plan T-016, D11-A y E2.3).

Códigos de salida 0, 2 y 3 (los de ``analyze referencias``). La salida aceptada,
``tests/golden/asientos.txt``, la compara ``test_analyze_spatial_cli.py`` con el resto de
``cli_golden.COMMANDS``.
"""

import dataclasses

import pytest

import benchmark_cache
from veritas import cli
from veritas.fem import benchmarks

pytestmark = pytest.mark.solver


@pytest.fixture(autouse=True)
def cached_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    """La batería, una vez por sesión (``benchmark_cache``)."""
    monkeypatch.setattr(
        benchmarks, "run_settlement_battery", benchmark_cache.run_settlement_battery
    )


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = cli.main(["analyze", "asientos", *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_battery_matches_and_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys)
    assert code == cli.EXIT_OK
    assert err == ""
    assert out.startswith("Asientos impuestos T-ASI-01…10 (plan T-016)")
    for case_id in (
        "T-ASI-01",
        "T-ASI-06",
        "T-ASI-07",
        "T-ASI-08 a",
        "T-ASI-08 b",
        "T-ASI-09 plano",
        "T-ASI-09 espacial",
        "T-ASI-10 · T-ASI-01",
        "T-ASI-10 · T-ASI-06",
    ):
        assert f"== {case_id} — " in out, case_id
    assert "Las 17 variantes coinciden" in out
    assert out.count("Corrida plana del contraste: SUCCEEDED") == 6
    assert out.count("Control de asiento, ") == 10  # 07, 08 a/b, 09 espacial y seis de 10
    assert "δ = -10 mm, θ = 0.002 rad" in out
    assert cli.REFERENCE_MISMATCH not in out
    assert cli.REFERENCE_NO_RESULTS not in out
    assert cli.EXPERIMENTAL_WARNING in out
    for word in ("APROBADO", "aprobado", "VÁLIDO", "válido"):
        assert word not in out


def test_profile_converts_units(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "--profile", "kN-m")
    assert code == cli.EXIT_OK
    assert "-420 kN " in out  # R_Z N3 de T-ASI-01: 12EIδ/L³


@pytest.mark.parametrize("timeout", ["0", "-1", "abc"])
def test_invalid_timeout_exits_2(capsys: pytest.CaptureFixture[str], timeout: str) -> None:
    code, out, err = run(capsys, "--timeout", timeout)
    assert code == cli.EXIT_DIAGNOSTIC
    assert out == ""
    assert err


def test_unknown_profile_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["analyze", "asientos", "--profile", "X"])
    assert info.value.code == cli.EXIT_USAGE
    assert capsys.readouterr().out == ""


def test_mismatch_exits_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = benchmark_cache.run_settlement_battery

    def altered(timeout_s: float | None = None) -> tuple[object, ...]:
        outcomes = list(real(timeout_s))
        first = outcomes[0]
        check = dataclasses.replace(first.checks[0], computed=first.checks[0].reference * 2 + 1)
        outcomes[0] = dataclasses.replace(first, checks=(check, *first.checks[1:]))
        return tuple(outcomes)

    monkeypatch.setattr(benchmarks, "run_settlement_battery", altered)
    code, out, _err = run(capsys)
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert "T-ASI-01: 8/9 magnitudes, NO COINCIDE." in out
    assert f"{cli.REFERENCE_MISMATCH}: T-ASI-01." in out


def test_failed_run_is_not_shown_as_match(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "--timeout", "0.001")
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert "Estado: FAILED" in out
    assert "T-ASI-01: SIN RESULTADOS" in out
    assert "T-ASI-09 plano (A): SIN RESULTADOS" in out
    assert "coinciden" not in out.splitlines()[-3]
