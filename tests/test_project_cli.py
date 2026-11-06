"""``veritas project demo|open|verify`` (plan T-012, D11-A; E1)."""

from pathlib import Path

import pytest

from veritas.cli import _analyze_demo_model, main
from veritas.core.loads.load_case import LoadCategory
from veritas.persistence.project import open_project, save_project


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(["project", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("case", ["viga", "portico", "uniones"])
def test_demo_then_open_and_verify(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, case: str
) -> None:
    path = str(tmp_path / f"{case}.json")
    code, out, err = run(capsys, "demo", "--out", path, "--caso", case)
    assert (code, err) == (0, "")
    project = open_project(path)
    assert project.model_hash in out
    assert project.integrity in out

    code, out, err = run(capsys, "open", path)
    assert (code, err) == (0, "")
    assert project.model_hash in out
    assert "CORRECTA" in out
    assert "esquema v8" in out
    assert "veritas-canon/1" in out
    assert "experimental" in out

    code, out, err = run(capsys, "verify", path)
    assert (code, err) == (0, "")
    assert "íntegro y válido" in out


def test_default_case_is_viga(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    path = tmp_path / "d.json"
    assert run(capsys, "demo", "--out", str(path))[0] == 0
    assert len(open_project(path).model.members) == 1


def test_demo_overwrite_keeps_bak(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    path = tmp_path / "d.json"
    run(capsys, "demo", "--out", str(path))
    first = path.read_bytes()
    run(capsys, "demo", "--out", str(path), "--caso", "portico")
    assert path.with_name("d.json.bak").read_bytes() == first


def test_altered_file_exits_3_with_message_on_stderr(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "a.json"
    run(capsys, "demo", "--out", str(path))
    path.write_text(path.read_text(encoding="utf-8").replace('"N1"', '"X1"'), encoding="utf-8")
    for action in ("verify", "open"):
        code, out, err = run(capsys, action, str(path))
        assert code == 3
        assert out == ""
        assert err.startswith("INPUT_OUT_OF_RANGE: El archivo está alterado")


@pytest.mark.parametrize("content", ["{}", "no es json", "[1]"])
def test_invalid_file_exits_2(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, content: str
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    for action in ("verify", "open"):
        code, out, err = run(capsys, action, str(path))
        assert code == 2
        assert out == ""
        assert err.startswith("INPUT_INVALID_TYPE:")


def test_missing_file_exits_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out, err = run(capsys, "verify", str(tmp_path / "no.json"))
    assert (code, out) == (2, "")
    assert "No se puede leer" in err


def test_unknown_case_is_a_usage_error(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as info:
        main(["project", "demo", "--out", str(tmp_path / "x.json"), "--caso", "otro"])
    assert info.value.code == 2
    assert not (tmp_path / "x.json").exists()
    capsys.readouterr()


def test_project_requires_an_action(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["project"])
    assert info.value.code == 2
    capsys.readouterr()


# --- E2: analyze, runs, move-node ---------------------------------------------------------


@pytest.mark.solver
def test_analyze_save_runs_move_and_return(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = str(tmp_path / "v.json")
    run(capsys, "demo", "--out", path)
    code, out, err = run(capsys, "runs", path)
    assert (code, err) == (0, "")
    assert "sin ejecuciones guardadas" in out

    code, out, err = run(capsys, "analyze", path)  # sin --guardar: no se archiva
    assert (code, err) == (0, "")
    assert "SUCCEEDED" in out
    assert "sin ejecuciones" in run(capsys, "runs", path)[1]

    code, out, err = run(capsys, "analyze", path, "--guardar")
    assert (code, err) == (0, "")
    assert "Ejecución guardada" in out
    code, out, _ = run(capsys, "analyze", path, "--tipo", "plano", "--guardar")
    assert code == 0
    runs = run(capsys, "runs", path)[1]
    assert "2 ejecución(es)" in runs
    assert [r.endswith("CURRENT") for r in runs.splitlines() if r.startswith("  ")] == [True, True]
    assert "spatial" in runs
    assert "planar" in runs

    code, out, _ = run(capsys, "move-node", path, "--nodo", "N2", "--a", "2.5", "0", "0")
    assert code == 0
    assert "0 CURRENT, 2 STALE" in out
    rows = [r for r in run(capsys, "runs", path)[1].splitlines() if r.startswith("  ")]
    assert [r.endswith("STALE") for r in rows] == [True, True]

    code, out, _ = run(capsys, "move-node", path, "--nodo", "N2", "--a", "2", "0", "0")
    assert "2 CURRENT, 0 STALE" in out
    assert "ejecuciones guardadas: 2" in run(capsys, "open", path)[1]


@pytest.mark.solver
def test_failed_analysis_exits_3_and_is_saved_as_failed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    model, *_ = _analyze_demo_model("mecanismo")
    path = tmp_path / "m.json"
    save_project(path, model)
    code, out, _ = run(capsys, "analyze", str(path), "--guardar")
    assert code == 3
    assert "FAILED" in out
    assert "ANALYSIS_SINGULAR" in out
    assert "sin resultados válidos" in out
    table = run(capsys, "runs", str(path))[1]
    assert "FAILED" in table
    assert "SIN RESULTADOS VÁLIDOS" in table


def test_analyze_needs_a_case_when_there_are_several(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "c.json"
    run(capsys, "demo", "--out", str(path))
    project = open_project(path)
    project.model.add_load_case("G", LoadCategory.PERMANENT)
    save_project(path, project.model)
    code, out, err = run(capsys, "analyze", str(path))
    assert (code, out) == (2, "")
    assert "--carga" in err
    assert "Q1" in err


def test_move_node_unknown_label_exits_2(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "n.json"
    run(capsys, "demo", "--out", str(path))
    code, out, err = run(capsys, "move-node", str(path), "--nodo", "N9", "--a", "1", "1", "1")
    assert (code, out) == (2, "")
    assert err.startswith("REFERENCE_NOT_FOUND")
