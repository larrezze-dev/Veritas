"""CLI ``--cifras`` de ``analyze demo`` y ``analyze uniones`` (plan T-020, E1: D8-A).

``--cifras MAGNITUD=N[,…]`` construye en memoria un perfil derivado con N cifras
significativas por magnitud. Sin la opción la salida no cambia (golden de ``demo-*`` y
``uniones``).
"""

import pytest

from veritas import cli

pytestmark = pytest.mark.solver


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(["analyze", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_demo_with_significant_figures(capsys: pytest.CaptureFixture[str]) -> None:
    code, base, _ = run(capsys, "demo")
    assert code == cli.EXIT_OK
    code, out, err = run(capsys, "demo", "--cifras", "DISPLACEMENT=2,ANGLE=2")
    assert code == cli.EXIT_OK
    assert err == ""
    assert out != base
    assert "-1.2e-04 m" in out  # flecha de la viga con 2 cifras significativas
    assert "8.9e-05 rad" in out
    assert "-1.190e-04 m" not in out


def test_demo_without_the_option_uses_three_decimals(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run(capsys, "demo")
    assert "-1.190e-04 m" in out  # 3 decimales en científica
    assert "8.929e-05 rad" in out


def test_cifras_is_case_insensitive_and_accepts_one_kind(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run(capsys, "demo", "--cifras", "displacement=3")
    assert code == cli.EXIT_OK
    assert "-1.19e-04 m" in out  # 3 cifras significativas


def test_tres_materiales_and_uniones_accept_cifras(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = run(capsys, "demo", "--caso", "tres-materiales", "--cifras", "DISPLACEMENT=4")
    assert code == cli.EXIT_OK
    code, _, _ = run(capsys, "uniones", "--cifras", "DISPLACEMENT=4,MOMENT=5")
    assert code == cli.EXIT_OK


@pytest.mark.parametrize(
    "value",
    [
        "FOO=3",
        "DISPLACEMENT",
        "DISPLACEMENT=0",
        "DISPLACEMENT=16",
        "DISPLACEMENT=x",
        "DISPLACEMENT=2.5",
        "DISPLACEMENT=-1",
        "DISPLACEMENT=3,",
        "=3",
        "DISPLACEMENT=3,ANGLE=0",
    ],
)
@pytest.mark.parametrize("command", [["demo"], ["demo", "--caso", "tres-materiales"], ["uniones"]])
def test_invalid_cifras_exits_2_without_running(
    capsys: pytest.CaptureFixture[str], command: list[str], value: str
) -> None:
    code, out, err = run(capsys, *command, "--cifras", value)
    assert code == cli.EXIT_DIAGNOSTIC
    assert out == ""
    assert err.startswith("UNSUPPORTED_CONFIGURATION: ")
    assert "--cifras" in err
    assert "DISPLACEMENT" in err  # lista las magnitudes válidas


def test_cifras_is_only_available_in_demo_and_uniones() -> None:
    for command in ("referencias", "espacial", "asientos", "cargas-barra", "peso-propio"):
        with pytest.raises(SystemExit) as info:
            cli.main(["analyze", command, "--cifras", "DISPLACEMENT=3"])
        assert info.value.code == cli.EXIT_USAGE
