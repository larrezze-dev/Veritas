"""``veritas project demo|open|verify`` (plan T-012, D11-A; E1)."""

from pathlib import Path

import pytest

from veritas.cli import main
from veritas.persistence.project import open_project


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
    assert "esquema v1" in out
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
