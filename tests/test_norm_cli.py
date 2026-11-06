"""``veritas norm`` (plan T-014, E2; D8): list, show, new, copy, set, delete, use."""

import json
import re
from pathlib import Path

import pytest

from persistence_support import deterministic_ids, rich_model
from test_persistence_integrity import edit
from veritas.cli import main
from veritas.design_codes.builtin import CTE_PROTECTED, CTE_PROTECTED_ID
from veritas.design_codes.norms import NormKind
from veritas.persistence.project import open_project, save_project


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    deterministic_ids(monkeypatch)
    monkeypatch.undo()  # los ids de las normativas son uuid4 reales
    path = tmp_path / "p.json"
    save_project(path, rich_model())
    return path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(["norm", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def new(capsys: pytest.CaptureFixture[str], project: Path, name: str = "Mía", *extra: str) -> str:
    code, out, err = run(capsys, "new", str(project), "--nombre", name, *extra)
    assert (code, err) == (0, "")
    match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", out)
    assert match
    return match.group(0)


def test_list_without_file_shows_protected_only(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "list")
    assert (code, err) == (0, "")
    assert str(CTE_PROTECTED_ID) in out
    assert "PROTECTED" in out
    assert "NO FIJADA" in out
    assert "NO_COMPROBADO" in out
    assert "experimental" in out


def test_list_with_project_includes_user_and_selection(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ident = new(capsys, project, "Mía")
    assert run(capsys, "use", str(project), ident)[0] == 0
    code, out, _ = run(capsys, "list", str(project))
    assert code == 0
    assert ident in out
    assert "USER" in out
    assert "PROTECTED" in out
    assert "seleccionada" in out


def test_new_creates_revision_one_and_saves(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ident = new(
        capsys,
        project,
        "Mía",
        "--sistema",
        "EN",
        "--jurisdiccion",
        "Prueba",
        "--edicion",
        "2020",
        "--documento",
        "Doc A",
        "--documento",
        "Doc B",
        "--descripcion",
        "algo",
    )
    norms = open_project(project).norms
    (config,) = norms.user
    assert str(config.id) == ident
    assert (config.revision, config.kind.value, config.system.value) == (1, "USER", "EN")
    assert config.documents == ("Doc A", "Doc B")
    assert config.edition == "2020"
    assert config.parameters == ()
    assert (project.with_name(project.name + ".bak")).exists()


def test_new_defaults_do_not_invent_content(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    new(capsys, project)
    (config,) = open_project(project).norms.user
    assert config.system.value == "OTHER"
    assert config.edition == "NO FIJADA"
    assert config.parameters == ()


def test_new_with_empty_name_is_a_domain_error(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    code, out, err = run(capsys, "new", str(project), "--nombre", "  ")
    assert (code, out) == (2, "")
    assert "INPUT_INVALID_TYPE" in err
    assert open_project(project).norms.user == ()


def test_show_latest_and_specific_revision(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ident = new(capsys, project)
    assert run(capsys, "set", str(project), ident, "--edicion", "2021")[0] == 0
    code, out, _ = run(capsys, "show", str(project), ident)
    assert code == 0
    assert "revisión 2" in out
    assert "2021" in out
    code, out, _ = run(capsys, "show", str(project), ident, "--revision", "1")
    assert "revisión 1" in out
    assert "NO FIJADA" in out
    code, _, err = run(capsys, "show", str(project), ident, "--revision", "9")
    assert code == 2
    assert "REFERENCE_NOT_FOUND" in err


def test_show_protected_and_unknown_id(capsys: pytest.CaptureFixture[str], project: Path) -> None:
    code, out, _ = run(capsys, "show", str(project), str(CTE_PROTECTED_ID))
    assert code == 0
    assert "PROTECTED" in out
    assert "sin parámetros" in out
    code, _, err = run(capsys, "show", str(project), "00000000-0000-4000-8000-0000000000aa")
    assert code == 2
    assert "REFERENCE_NOT_FOUND" in err


def test_id_prefix_is_accepted_when_unambiguous(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ident = new(capsys, project)
    code, out, _ = run(capsys, "show", str(project), ident[:8])
    assert code == 0
    assert ident in out
    code, _, err = run(capsys, "show", str(project), "")
    assert code == 2
    assert "REFERENCE_NOT_FOUND" in err


def test_copy_user_and_protected(capsys: pytest.CaptureFixture[str], project: Path) -> None:
    ident = new(capsys, project)
    code, _, err = run(capsys, "copy", str(project), ident, "--nombre", "Copia")
    assert (code, err) == (0, "")
    code, _, err = run(capsys, "copy", str(project), str(CTE_PROTECTED_ID), "--nombre", "Mi CTE")
    assert (code, err) == (0, "")
    configs = {c.name: c for c in open_project(project).norms.user}
    assert configs["Copia"].derived_from is not None
    assert str(configs["Copia"].derived_from.entity_id) == ident
    assert configs["Mi CTE"].derived_from == CTE_PROTECTED.ref
    assert configs["Mi CTE"].kind is NormKind.USER
    assert configs["Mi CTE"].documents == CTE_PROTECTED.documents


def test_copy_exact_revision(capsys: pytest.CaptureFixture[str], project: Path) -> None:
    ident = new(capsys, project)
    run(capsys, "set", str(project), ident, "--edicion", "2021")
    run(capsys, "copy", str(project), ident, "--nombre", "C1", "--revision", "1")
    configs = {c.name: c for c in open_project(project).norms.user if c.revision == 1}
    assert configs["C1"].edition == "NO FIJADA"
    assert configs["C1"].derived_from is not None
    assert configs["C1"].derived_from.revision == 1


def test_set_fields_params_and_remove(capsys: pytest.CaptureFixture[str], project: Path) -> None:
    ident = new(capsys, project)
    code, out, err = run(
        capsys,
        "set",
        str(project),
        ident,
        "--nombre",
        "Otra",
        "--param",
        "fy.k=275",
        "MPa",
        "--fuente",
        "Doc A, 4.2",
        "--param",
        "gamma=1.05",
        "1",
        "--fuente",
        "Doc A, 5.1",
        "--documento",
        "X",
    )
    assert (code, err) == (0, "")
    assert "revisión 2" in out
    config = open_project(project).norms.user[-1]
    assert (config.name, config.revision, config.documents) == ("Otra", 2, ("X",))
    values = {p.key: (p.value, p.unit, p.source) for p in config.parameters}
    assert values == {"fy.k": (275e6, "MPa", "Doc A, 4.2"), "gamma": (1.05, "1", "Doc A, 5.1")}
    # Reemplaza el valor de una clave existente y quita otra.
    code, _, err = run(
        capsys,
        "set",
        str(project),
        ident,
        "--param",
        "gamma=1.1",
        "1",
        "--fuente",
        "Doc A, 5.2",
        "--quitar-param",
        "fy.k",
    )
    assert (code, err) == (0, "")
    config = open_project(project).norms.user[-1]
    assert config.revision == 3
    assert [(p.key, p.value, p.source) for p in config.parameters] == [("gamma", 1.1, "Doc A, 5.2")]
    assert len(open_project(project).norms.user) == 3  # las revisiones anteriores se conservan


def test_set_without_changes_makes_no_revision(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ident = new(capsys, project)
    code, out, _ = run(capsys, "set", str(project), ident)
    assert code == 0
    assert "sin cambios" in out
    assert len(open_project(project).norms.user) == 1


def test_set_param_requires_source_and_matching_counts(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ident = new(capsys, project)
    code, _, err = run(capsys, "set", str(project), ident, "--param", "a=1", "MPa")
    assert code == 2
    assert "fuente" in err.lower()
    code, _, err = run(
        capsys, "set", str(project), ident, "--param", "a=1", "MPa", "--fuente", "  "
    )
    assert code == 2
    assert len(open_project(project).norms.user) == 1


@pytest.mark.parametrize(
    ("spec", "unit"), [("a", "MPa"), ("a=x", "MPa"), ("a=nan", "MPa"), ("a=1", "furlong")]
)
def test_set_param_rejects_bad_values_and_units(
    capsys: pytest.CaptureFixture[str], project: Path, spec: str, unit: str
) -> None:
    ident = new(capsys, project)
    code, _, err = run(capsys, "set", str(project), ident, "--param", spec, unit, "--fuente", "F")
    assert code == 2
    assert err.strip()
    assert len(open_project(project).norms.user) == 1


def test_set_remove_unknown_param_is_rejected(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ident = new(capsys, project)
    code, _, err = run(capsys, "set", str(project), ident, "--quitar-param", "nada")
    assert code == 2
    assert "nada" in err


def test_set_protected_is_rejected_with_code_2(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    before = project.read_bytes()
    code, out, err = run(capsys, "set", str(project), str(CTE_PROTECTED_ID), "--nombre", "x")
    assert (code, out) == (2, "")
    assert "UNSUPPORTED_CONFIGURATION" in err
    assert "protegida" in err
    assert project.read_bytes() == before


def test_delete_requires_confirmation(capsys: pytest.CaptureFixture[str], project: Path) -> None:
    ident = new(capsys, project)
    before = project.read_bytes()
    code, out, err = run(capsys, "delete", str(project), ident)
    assert code == 2
    assert "--confirmar" in (out + err)
    assert project.read_bytes() == before
    code, out, err = run(capsys, "delete", str(project), ident, "--confirmar")
    assert (code, err) == (0, "")
    assert "1 revisión" in out
    assert open_project(project).norms.user == ()


def test_delete_protected_and_selected_are_rejected(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    code, _, err = run(capsys, "delete", str(project), str(CTE_PROTECTED_ID), "--confirmar")
    assert code == 2
    assert "UNSUPPORTED_CONFIGURATION" in err
    ident = new(capsys, project)
    run(capsys, "use", str(project), ident)
    code, _, err = run(capsys, "delete", str(project), ident, "--confirmar")
    assert code == 2
    assert "seleccionada" in err
    assert len(open_project(project).norms.user) == 1
    run(capsys, "use", str(project), str(CTE_PROTECTED_ID))
    assert run(capsys, "delete", str(project), ident, "--confirmar")[0] == 0


def test_use_selects_exact_revision_and_protected(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ident = new(capsys, project)
    run(capsys, "set", str(project), ident, "--edicion", "2021")
    assert run(capsys, "use", str(project), ident, "--revision", "1")[0] == 0
    selected = open_project(project).norms.selected
    assert selected is not None
    assert (str(selected.entity_id), selected.revision) == (ident, 1)
    assert run(capsys, "use", str(project), ident)[0] == 0
    selected = open_project(project).norms.selected
    assert selected is not None
    assert selected.revision == 2
    code, out, _ = run(capsys, "use", str(project), str(CTE_PROTECTED_ID))
    assert code == 0
    assert "NO_COMPROBADO" in out
    assert open_project(project).norms.selected == CTE_PROTECTED.ref


def test_use_unknown_revision_is_rejected(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ident = new(capsys, project)
    code, _, err = run(capsys, "use", str(project), ident, "--revision", "5")
    assert code == 2
    assert "REFERENCE_NOT_FOUND" in err
    assert open_project(project).norms.selected is None


def test_norm_commands_never_change_the_model_hash(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    base = open_project(project).model_hash
    ident = new(capsys, project)
    run(capsys, "set", str(project), ident, "--param", "a=1", "MPa", "--fuente", "F")
    run(capsys, "use", str(project), ident)
    assert open_project(project).model_hash == base


def test_move_node_and_append_runs_keep_norms(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ident = new(capsys, project)
    run(capsys, "use", str(project), ident)
    label = next(iter(open_project(project).model.nodes.values())).label
    assert main(["project", "move-node", str(project), "--nodo", label, "--a", "0", "0", "1"]) == 0
    capsys.readouterr()
    norms = open_project(project).norms
    assert len(norms.user) == 1
    assert norms.selected is not None
    assert str(norms.selected.entity_id) == ident


def test_tampered_file_exits_with_code_3(capsys: pytest.CaptureFixture[str], project: Path) -> None:
    raw = bytearray(project.read_bytes())
    raw[raw.index(b'"N1"') + 2] ^= 0x01
    project.write_bytes(bytes(raw))
    for argv in (
        ("list", str(project)),
        ("show", str(project), str(CTE_PROTECTED_ID)),
        ("new", str(project), "--nombre", "x"),
        ("use", str(project), str(CTE_PROTECTED_ID)),
    ):
        code, out, err = run(capsys, *argv)
        assert (code, out) == (3, "")
        assert "integridad" in err.lower() or "alterado" in err.lower() or err.strip()


def test_invalid_but_signed_file_exits_with_code_2(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    ghost = "00000000-0000-4000-8000-0000000000aa"
    bad = edit(
        project,
        lambda d: d["content"]["norms"].update(selected={"entity_id": ghost, "revision": 1}),
    )
    code, out, err = run(capsys, "list", str(bad))
    assert (code, out) == (2, "")
    assert "UNSUPPORTED_CONFIGURATION" in err


def test_missing_file_is_a_domain_error(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out, err = run(capsys, "list", str(tmp_path / "no.json"))
    assert (code, out) == (2, "")
    assert err.strip()


def test_norm_group_appears_in_top_level_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "norm" in capsys.readouterr().out


def test_file_written_by_cli_is_valid_json_with_v4(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    new(capsys, project)
    data = json.loads(project.read_text(encoding="utf-8"))
    assert data["schema_version"] == 8
    assert len(data["content"]["norms"]["user"]) == 1
