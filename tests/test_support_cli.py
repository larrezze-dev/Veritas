"""CLI ``veritas support dofs`` y ``veritas support demo`` (plan T-005, D6 y R4)."""

import pytest

from veritas import cli

GLOBAL_LABELS = ["Ux", "Uy", "Uz", "Rx", "Ry", "Rz"]
INCLINED_LABELS = ["Ux'", "Uy'", "Uz'", "Rx'", "Ry'", "Rz'"]


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def _table(out: str) -> dict[str, tuple[str, str]]:
    """Filas del primer cuadro GDL/Estado/Rigidez: etiqueta -> (estado, rigidez)."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("GDL"))
    rows: dict[str, tuple[str, str]] = {}
    for line in lines[start + 2 : start + 8]:
        parts = line.split(None, 2)
        rows[parts[0]] = (parts[1], parts[2].strip())
    return rows


def _states(out: str) -> list[str]:
    return [state for state, _ in _table(out).values()]


def _dofs(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    code, out, err = _run(["support", "dofs", *argv], capsys)
    assert code == cli.EXIT_OK
    assert err == ""
    assert cli.STABILITY_NOT_CHECKED in out.splitlines()
    return out


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--preset", "empotramiento"], ["FIXED"] * 6),
        (["--preset", "articulacion"], ["FIXED"] * 3 + ["FREE"] * 3),
        (["--preset", "rodillo"], ["FREE", "FREE", "FIXED", "FREE", "FREE", "FREE"]),
        (
            ["--preset", "rodillo", "--axis", "Ux"],
            ["FIXED", "FREE", "FREE", "FREE", "FREE", "FREE"],
        ),
        (
            ["--preset", "rodillo", "--axis", "uy"],
            ["FREE", "FIXED", "FREE", "FREE", "FREE", "FREE"],
        ),
    ],
)
def test_dofs_presets(
    argv: list[str], expected: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    out = _dofs(argv, capsys)
    assert out.splitlines()[0] == "Base: GLOBAL"
    assert list(_table(out)) == GLOBAL_LABELS
    assert _states(out) == expected
    assert all(rigidity == "-" for _, rigidity in _table(out).values())


def test_dofs_fixed_mixed_case(capsys: pytest.CaptureFixture[str]) -> None:
    out = _dofs(["--fixed", "uX,UZ, rY"], capsys)
    assert _states(out) == ["FIXED", "FREE", "FIXED", "FREE", "FIXED", "FREE"]


@pytest.mark.parametrize(
    ("fixed", "expected"),
    [
        ("Uz", ["FREE", "FREE", "FIXED", "FREE", "FREE", "FREE"]),  # Mecanismo: se acepta.
        ("Ux,Uz", ["FIXED", "FREE", "FIXED", "FREE", "FREE", "FREE"]),
    ],
)
def test_dofs_fixed_accepted_without_stability_judgement(
    fixed: str, expected: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    out = _dofs(["--fixed", fixed], capsys)
    assert _states(out) == expected


@pytest.mark.parametrize(
    "text",
    ["Uz=5000 kN/m", "Uz=5000kN/m", "uz = 5000   kN/m", "UZ=5e3 kN/m"],
)
def test_dofs_spring_with_and_without_space(text: str, capsys: pytest.CaptureFixture[str]) -> None:
    out = _dofs(["--spring", text], capsys)
    table = _table(out)
    assert table["Uz"] == ("SPRING", "5000000.000 N/m")  # 5000 kN/m = 5e6 N/m.
    assert [state for label, (state, _) in table.items() if label != "Uz"] == ["FREE"] * 5


def test_dofs_spring_rotational_ascii_alias(capsys: pytest.CaptureFixture[str]) -> None:
    out = _dofs(["--spring", "Ry=2000 kN*m/rad"], capsys)
    state, shown = _table(out)["Ry"]
    assert state == "SPRING"
    assert shown.startswith("2000000.000 N")  # 2000 kN·m/rad = 2e6 N·m/rad.
    assert shown.endswith("m/rad")


def test_dofs_fixed_and_springs_combined_repeatable(capsys: pytest.CaptureFixture[str]) -> None:
    out = _dofs(["--fixed", "Ux,Uy", "--spring", "Uz=1 kN/m", "--spring", "Ry=1 kN·m/rad"], capsys)
    assert _states(out) == ["FIXED", "FIXED", "SPRING", "FREE", "SPRING", "FREE"]
    table = _table(out)
    assert table["Uz"][1] == "1000.000 N/m"
    assert table["Ry"][1].startswith("1000.000 N")


def test_dofs_profile_kn_m_shows_stiffness_in_kn(capsys: pytest.CaptureFixture[str]) -> None:
    out = _dofs(["--spring", "Uz=5000 kN/m", "--profile", "kN-m"], capsys)
    assert _table(out)["Uz"] == ("SPRING", "5000.000 kN/m")


def test_dofs_inclined_header_rows_and_primed_labels(capsys: pytest.CaptureFixture[str]) -> None:
    out = _dofs(
        ["--preset", "rodillo", "--base", "inclinada", "--x-dir", "1,0,1", "--z-ref", "0,0,1"],
        capsys,
    )
    lines = out.splitlines()
    assert lines[0].startswith("Base: INCLINADA")
    assert lines[1].strip().startswith("ex' = (0.7071067812, 0, 0.7071067812)")
    assert lines[2].strip().startswith("ey' = (0, 1, 0)")
    assert lines[3].strip().startswith("ez' = (-0.7071067812, 0, 0.7071067812)")
    assert list(_table(out)) == INCLINED_LABELS
    assert _states(out) == ["FREE", "FREE", "FIXED", "FREE", "FREE", "FREE"]


def test_dofs_inclined_case_insensitive_base(capsys: pytest.CaptureFixture[str]) -> None:
    out = _dofs(
        ["--fixed", "Ux", "--base", "INCLINADA", "--x-dir", "0,1,0", "--z-ref", "0,0,1"], capsys
    )
    assert out.splitlines()[0].startswith("Base: INCLINADA")


def test_dofs_global_base_explicit(capsys: pytest.CaptureFixture[str]) -> None:
    out = _dofs(["--fixed", "Ux", "--base", "global"], capsys)
    assert out.splitlines()[0] == "Base: GLOBAL"


# --- Errores de dominio: salida 2, stdout vacío, código en stderr --------------------------


@pytest.mark.parametrize(
    ("argv", "code_text"),
    [
        (["--fixed", "Ux,Qz"], "INPUT_OUT_OF_RANGE"),  # GDL desconocido.
        (["--fixed", "Ux,ux"], "INPUT_OUT_OF_RANGE"),  # GDL repetido.
        (["--fixed", ""], "INPUT_OUT_OF_RANGE"),  # Ningún GDL fijo.
        (["--fixed", " , "], "INPUT_OUT_OF_RANGE"),
        (["--fixed", "Ux", "--base", "local"], "UNSUPPORTED_CONFIGURATION"),
        (["--fixed", "Ux", "--spring", "Uz=0 kN/m"], "INPUT_OUT_OF_RANGE"),
        (["--fixed", "Ux", "--spring", "Uz=-5 kN/m"], "INPUT_OUT_OF_RANGE"),
        (["--fixed", "Ux", "--spring", "Uz=nan kN/m"], "INPUT_NONFINITE"),
        (["--fixed", "Ux", "--spring", "Uz=inf kN/m"], "INPUT_NONFINITE"),
        (["--fixed", "Ux", "--spring", "Uz=5 kN*m/rad"], "UNITS_INCOMPATIBLE"),
        (["--fixed", "Ux", "--spring", "Ry=5 kN/m"], "UNITS_INCOMPATIBLE"),
        (["--fixed", "Ux", "--spring", "Uz=5 parsec"], "UNITS_UNKNOWN"),
        (["--fixed", "Ux", "--spring", "Qz=5 kN/m"], "INPUT_OUT_OF_RANGE"),
        (["--fixed", "Ux", "--spring", "basura"], "INPUT_INVALID_TYPE"),
        (["--spring", "Uz=5 kN/m", "--spring", "uz=6 kN/m"], "INPUT_OUT_OF_RANGE"),
        (["--fixed", "Uz", "--spring", "Uz=5 kN/m"], "INPUT_OUT_OF_RANGE"),
        (["--preset", "rodillo", "--axis", "Rx"], "INPUT_OUT_OF_RANGE"),
        (["--preset", "rodillo", "--axis", "Qz"], "INPUT_OUT_OF_RANGE"),
        (
            ["--fixed", "Ux", "--base", "inclinada", "--x-dir", "1,0,0", "--z-ref", "1,0,0"],
            "GEOMETRY_INVALID_ORIENTATION",
        ),
        (
            ["--fixed", "Ux", "--base", "inclinada", "--x-dir", "0,0,0", "--z-ref", "0,0,1"],
            "GEOMETRY_INVALID_ORIENTATION",
        ),
        (
            ["--fixed", "Ux", "--base", "inclinada", "--x-dir", "1,0,nan", "--z-ref", "0,0,1"],
            "INPUT_NONFINITE",
        ),
        (
            ["--fixed", "Ux", "--base", "inclinada", "--x-dir", "1,0", "--z-ref", "0,0,1"],
            "INPUT_INVALID_TYPE",
        ),
    ],
)
def test_dofs_domain_errors(
    argv: list[str], code_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(["support", "dofs", *argv], capsys)
    assert code == cli.EXIT_DIAGNOSTIC == 2
    assert out == ""
    assert err.startswith(f"{code_text}: ")


def test_dofs_spring_without_unit_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["support", "dofs", "--fixed", "Ux", "--spring", "Uz=5000"], capsys)
    assert code == cli.EXIT_DIAGNOSTIC
    assert out == ""
    assert err.startswith("INPUT_INVALID_TYPE: ")


# --- Errores de uso (argparse) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--preset", "empotramiento", "--fixed", "Ux"],
        ["--preset", "empotramiento", "--spring", "Uz=5 kN/m"],
        [],
        ["--axis", "Ux", "--fixed", "Uz"],
        ["--axis", "Ux", "--preset", "articulacion"],
        ["--fixed", "Ux", "--base", "inclinada"],
        ["--fixed", "Ux", "--base", "inclinada", "--x-dir", "1,0,1"],
        ["--fixed", "Ux", "--base", "inclinada", "--z-ref", "0,0,1"],
        ["--fixed", "Ux", "--x-dir", "1,0,1"],
        ["--fixed", "Ux", "--z-ref", "0,0,1"],
        ["--fixed", "Ux", "--base", "global", "--x-dir", "1,0,1", "--z-ref", "0,0,1"],
        ["--preset", "voladizo"],
    ],
)
def test_dofs_usage_errors(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["support", "dofs", *argv])
    assert info.value.code == cli.EXIT_USAGE == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage:" in err


# --- support demo ------------------------------------------------------------------------------


def test_support_demo_trace(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["support", "demo", "--profile", "kN-m"], capsys)
    assert code == cli.EXIT_OK
    assert err == ""
    lines = out.splitlines()
    assert cli.STABILITY_NOT_CHECKED in lines
    assert "EJEMPLO" in out
    revisions = [
        int(line.rsplit("model_revision = ", 1)[1]) for line in lines if "model_revision = " in line
    ]
    assert revisions == [3, 5, 6, 6, 7, 8]
    assert "Volver a poner el rodillo: id nuevo, revisión 1" in out
    assert "mismo id" not in out
    assert "Base: GLOBAL" in out
    assert "Base: INCLINADA" in out
    assert "Ry   SPRING  5000.000 kN·m/rad" in out
    assert "Uz'  FIXED" in out


def test_support_demo_does_not_change_model_demo(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(["model", "demo", "--profile", "kN-m"], capsys)
    assert code == cli.EXIT_OK
    assert "model_revision = 7" in out
    assert "Apoyo" not in out
