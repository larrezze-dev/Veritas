"""CLI ``veritas connection dofs`` (plan T-010, D7, entrega E1)."""

import pytest

from veritas import cli

LABELS = ["Ux", "Uy", "Uz", "Rx", "Ry", "Rz"]


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(["connection", "dofs", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def _table(out: str) -> dict[str, tuple[str, str]]:
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("GDL"))
    rows: dict[str, tuple[str, str]] = {}
    for line in lines[start + 2 : start + 8]:
        parts = line.split(None, 2)
        rows[parts[0]] = (parts[1], parts[2].strip())
    return rows


def _ok(argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, tuple[str, str]]:
    code, out, err = _run(argv, capsys)
    assert code == cli.EXIT_OK
    assert err == ""
    lines = out.splitlines()
    assert lines[0] == cli.CONNECTION_BASIS
    assert lines[-1] == cli.CONNECTION_ANALYSIS_PENDING
    table = _table(out)
    assert list(table) == LABELS
    return table


@pytest.mark.parametrize(
    ("argv", "states"),
    [
        ([], ["RIGID"] * 6),
        (["--preset", "rigido"], ["RIGID"] * 6),
        (["--preset", "articulado"], ["RIGID"] * 4 + ["RELEASED"] * 2),
        (["--release", "ry, Rz"], ["RIGID"] * 4 + ["RELEASED"] * 2),
    ],
)
def test_states(argv: list[str], states: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    table = _ok(argv, capsys)
    assert [state for state, _ in table.values()] == states
    assert all(stiffness == "-" for _, stiffness in table.values())


def test_semi_rigid_shows_converted_stiffness(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["--preset", "semirrigido", "--spring", "Ry=5000 kN·m/rad", "--profile", "kN-m"]
    table = _ok(argv, capsys)
    assert table["Ry"][0] == "SPRING"
    assert table["Ry"][1].startswith("5000")
    assert "kN" in table["Ry"][1]


def test_custom_release_and_springs_si(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["--release", "Rz", "--spring", "Ry=5 kN*m/rad", "--spring", "Ux=2 kN/m"]
    table = _ok(argv, capsys)
    assert table["Rz"] == ("RELEASED", "-")
    assert table["Ry"][0] == "SPRING"
    assert table["Ry"][1].startswith("5000")
    assert table["Ux"][0] == "SPRING"
    assert table["Ux"][1].startswith("2000")


@pytest.mark.parametrize(
    ("argv", "key_text"),
    [
        (["--spring", "Ry=0 kN*m/rad"], "INPUT_OUT_OF_RANGE"),
        (["--spring", "Ry=-3 kN*m/rad"], "INPUT_OUT_OF_RANGE"),
        (["--spring", "Ry=inf kN*m/rad"], ""),
        (["--spring", "Ry=5 kN/m"], "UNITS_INCOMPATIBLE"),
        (["--spring", "Ry"], "INPUT_INVALID_TYPE"),
        (["--release", "Ry", "--spring", "Ry=5 kN*m/rad"], "INPUT_OUT_OF_RANGE"),
        (["--release", "Ry,Ry"], "INPUT_OUT_OF_RANGE"),
        (["--release", "Rw"], "INPUT_OUT_OF_RANGE"),
        (["--preset", "semirrigido", "--spring", "Ux=5 kN/m"], "INPUT_OUT_OF_RANGE"),
    ],
)
def test_invalid_input_exit_2(
    argv: list[str], key_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(argv, capsys)
    assert code == cli.EXIT_DIAGNOSTIC
    assert out == ""
    assert key_text in err


@pytest.mark.parametrize(
    "argv",
    [
        ["--preset", "rigido", "--release", "Ry"],
        ["--preset", "articulado", "--spring", "Ry=5 kN*m/rad"],
        ["--preset", "semirrigido"],
        ["--preset", "semirrigido", "--spring", "Ry=5 kN*m/rad", "--release", "Rz"],
        ["--preset", "otro"],
    ],
)
def test_usage_errors(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        _run(argv, capsys)
    assert info.value.code == 2
