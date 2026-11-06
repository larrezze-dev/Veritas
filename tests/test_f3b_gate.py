"""T-029 E2: CLI del croquis libre y puerta de F3B («sección con hueco y error por
autointersección»), de extremo a extremo y sin GUI. Datos sintéticos: ningún perfil real."""

from pathlib import Path

import pytest

from catalog_cli_golden import DATA_DIR, MAPPING, SYNTHETIC_ARGS
from veritas.cli import main
from veritas.core.sections.properties import PropertyStatus
from veritas.core.sections.section import SectionFamily
from veritas.persistence.project import open_project

DB = "c.sqlite"
REF = "Sint/SINTETICO-I/SINT-100x50-4-6"
HOLE_OUTER = "0,0;0.4,0;0.4,0.6;0,0.6"
HOLE = "0.1,0.2;0.25,0.2;0.25,0.5;0.1,0.5"
BOWTIE = "0,0;1,1;1,0;0,1"
# La I del catálogo sintético (h=100, b=50, tw=4, tf=6 mm) dibujada como polígono, en mm.
I_OUTLINE = "0,0;50,0;50,6;27,6;27,94;50,94;50,100;0,100;0,94;23,94;23,6;0,6"
I_FAR = "0,0;50,0;50,7;27,7;27,93;50,93;50,100;0,100;0,93;23,93;23,7;0,7"


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lote.csv").write_bytes((DATA_DIR / "catalog_synthetic_100.csv").read_bytes())
    (tmp_path / MAPPING).write_bytes((DATA_DIR / MAPPING).read_bytes())
    create = ["catalog", "create", "--db", DB, "--nombre", "Sint", "--fuente", "SINTÉTICO"]
    assert main([*create, "--licencia", "Sin valor comercial", "--edicion", "2026"]) == 0
    imported = ["catalog", "import", "--db", DB, "--catalogo", "Sint", "lote.csv", *SYNTHETIC_ARGS]
    assert main(imported) == 0
    assert main(["project", "demo", "--out", "p.json", "--caso", "viga"]) == 0
    return tmp_path


# --- section sketch / u / l / t ----------------------------------------------------------


def test_sketch_reports_centroid_product_of_inertia_axes_and_the_missing_j(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["section", "sketch", "--exterior", HOLE_OUTER, "--hueco", HOLE]) == 0
    out = capsys.readouterr().out
    assert "polygon-green/1" in out
    assert "yc" in out
    assert "Iyz" in out
    assert "Ejes principales" in out
    assert "indeterminados" not in out
    assert "sin-solucion-exacta-validada/1" in out
    assert "origen en el centroide" not in out  # el croquis no está centrado
    assert "0.195" in out  # área neta 0,24 − 0,045 m²


@pytest.mark.parametrize(
    "argv",
    [
        ["section", "sketch", "--exterior", BOWTIE],
        ["section", "sketch", "--exterior", "0,0;1,0"],
        ["section", "sketch", "--exterior", "0,0;1,x;1,1"],
        ["section", "sketch", "--exterior", "0,0;1;1,1"],
        ["section", "sketch", "--exterior", "0,0;1,nan;1,1"],
        ["section", "sketch", "--exterior", "  "],
        ["section", "sketch", "--exterior", HOLE_OUTER, "--hueco", "5,5;6,5;6,6"],  # fuera
        ["section", "sketch", "--exterior", HOLE_OUTER, "--ref", REF],  # --ref sin --db
        ["section", "l", "--dim", "h=200", "b=150", "t=150", "--unidad", "mm"],
        ["section", "u", "--dim", "h=300", "b=100", "tw=8"],
        ["section", "t", "--dim", "h=200", "b=150", "tw=10", "tf=20", "q=1", "--unidad", "mm"],
    ],
)
def test_invalid_sketch_input_exits_2_with_a_diagnostic(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    assert capsys.readouterr().err


def test_templates_print_their_properties_without_a_shear_area(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for shape, dims in (
        ("u", ["h=300", "b=100", "tw=8", "tf=12"]),
        ("l", ["h=200", "b=150", "t=20"]),
        ("t", ["h=200", "b=150", "tw=10", "tf=20"]),
    ):
        assert main(["section", shape, "--dim", *dims, "--unidad", "mm"]) == 0
        out = capsys.readouterr().out
        assert "polygon-green/1" in out
        assert "Ejes principales" in out
        assert "Área de cortante" not in out


def test_sketch_comparison_with_the_catalog_only_informs(
    gate: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (gate / DB).read_bytes()
    capsys.readouterr()
    argv = ["section", "sketch", "--exterior", I_OUTLINE, "--unidad", "mm", "--ref", REF]
    assert main([*argv, "--db", DB]) == 0
    out = capsys.readouterr().out
    assert "DENTRO" in out
    assert "NO_COMPARABLE" in out  # J no disponible en Veritas: no se rellena
    assert "la forma del perfil" not in out.lower()
    assert "Solo informe" in out
    assert (gate / DB).read_bytes() == before
    far = ["section", "sketch", "--exterior", I_FAR, "--unidad", "mm", "--ref", REF, "--db", DB]
    assert main(far) == 0
    assert "FUERA" in capsys.readouterr().out


# --- project add-sketch (puerta de F3B) --------------------------------------------------


def test_f3b_gate_end_to_end(gate: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    before = (gate / "p.json").read_bytes()

    # Error por autointersección: código 2, diagnóstico y el archivo NO cambia.
    bad = ["project", "add-sketch", "p.json", "--exterior", BOWTIE, "--nombre", "mala"]
    assert main(bad) == 2
    assert "autointersecta" in capsys.readouterr().err
    assert (gate / "p.json").read_bytes() == before

    # Hueco fuera del contorno y vértice mal escrito: también código 2 sin tocar el archivo.
    outside = [
        "project",
        "add-sketch",
        "p.json",
        "--exterior",
        HOLE_OUTER,
        "--hueco",
        "5,5;6,5;6,6",
    ]
    assert main(outside) == 2
    assert main(["project", "add-sketch", "p.json", "--exterior", "0,0;1,x;1,1"]) == 2
    capsys.readouterr()
    assert (gate / "p.json").read_bytes() == before

    # Sección con hueco: se guarda como FREEFORM con área neta correcta.
    good = ["project", "add-sketch", "p.json", "--exterior", HOLE_OUTER, "--hueco", HOLE]
    assert main([*good, "--nombre", "cajon"]) == 0
    out = capsys.readouterr().out
    assert "FREEFORM" in out
    assert "yc" in out
    assert "Iyz" in out
    reopened = open_project(gate / "p.json")
    freeform = [s for s in reopened.model.snapshot().sections if s.name == "cajon"]
    assert len(freeform) == 1
    assert freeform[0].family is SectionFamily.FREEFORM
    assert freeform[0].properties.area.value == pytest.approx(0.24 - 0.045, rel=1e-13)
    assert len(freeform[0].require_freeform().holes) == 1

    # Asignable a una barra de acero, y el listado declara que NO es apta para FEM (J).
    assert main(["project", "assign-section", "p.json", "B1", "cajon"]) == 0
    capsys.readouterr()
    assert main(["project", "sections", "p.json"]) == 0
    listing = capsys.readouterr().out
    assert "cajon" in listing
    assert "FEM:" in listing
    assert "NO apta para FEM (falta J)" in listing
    assert "no aplicable (croquis libre)" in listing
    assert "Norma:" in listing

    # Verificación de integridad y reapertura: hash intacto.
    assert main(["project", "verify", "p.json"]) == 0


def test_add_sketch_with_contrast_follows_the_same_rules_as_add_section(
    gate: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["project", "add-sketch", "p.json", "--unidad", "mm"]
    # --contrastar y --db van juntos.
    assert main([*base, "--exterior", I_OUTLINE, "--contrastar", REF]) == 2
    capsys.readouterr()

    # Fuera de tolerancia: código 2 y el archivo NO cambia.
    before = (gate / "p.json").read_bytes()
    far = [*base, "--exterior", I_FAR, "--nombre", "I7", "--contrastar", REF, "--db", DB]
    assert main(far) == 2
    assert "Nada escrito" in capsys.readouterr().out
    assert (gate / "p.json").read_bytes() == before

    # Dentro de tolerancia: CONTRASTADO en A, Iy, Iz con el valor de Veritas; J sigue sin valor.
    near = [*base, "--exterior", I_OUTLINE, "--nombre", "I100", "--contrastar", REF, "--db", DB]
    assert main(near) == 0
    assert "CONTRASTADO registrado en A, Iy, Iz" in capsys.readouterr().out
    assert "VERIFICADO" not in (gate / "p.json").read_text(encoding="utf-8")
    (gate / DB).unlink()
    assert main(["project", "verify", "p.json"]) == 0
    section = max(
        (s for s in open_project(gate / "p.json").model.snapshot().sections if s.name == "I100"),
        key=lambda s: s.revision,
    )
    assert section.properties.area.status is PropertyStatus.CONTRASTADO
    assert section.properties.torsion_constant.status is PropertyStatus.NO_DISPONIBLE
    assert section.shear is None


def test_isotropic_or_symmetric_noise_does_not_define_an_angle(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["section", "sketch", "--exterior", "0,0;1,0;1,1;0,1"]) == 0
    assert "Ejes principales indeterminados" in capsys.readouterr().out
    assert main(["section", "u", "--dim", "h=300", "b=100", "tw=8", "tf=12", "--unidad", "mm"]) == 0
    out = capsys.readouterr().out
    assert "forma 0.0000° con +y" in out or "forma 90.0000° con +y" in out
    assert "-0.0000" not in out
    assert "-89.99" not in out
