"""Puerta de F2 (T-024 E2, D9-A): importar 100 perfiles, buscar con filtros, asignar a una barra,
guardar, borrar el SQLite, reabrir y comprobar propiedades, hash y análisis.

Datos sintéticos (``tests/data``); los datos reales de catálogo siguen fuera (TK-019, NFR-012).
"""

from pathlib import Path

import pytest

from catalog_cli_golden import DATA_DIR, MAPPING, SYNTHETIC_ARGS
from veritas.catalogs.store import CatalogStore
from veritas.cli import main
from veritas.core.sections.properties import PropertyStatus
from veritas.core.sections.section import SectionFamily
from veritas.persistence.project import open_project

DB = "c.sqlite"
REF = "Sint/SINTETICO-I/SINT-100x50-4-6"


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lote.csv").write_bytes((DATA_DIR / "catalog_synthetic_100.csv").read_bytes())
    (tmp_path / MAPPING).write_bytes((DATA_DIR / MAPPING).read_bytes())
    create = ["catalog", "create", "--db", DB, "--nombre", "Sint", "--fuente", "SINTÉTICO"]
    assert main([*create, "--licencia", "Sin valor comercial", "--edicion", "2026"]) == 0
    imported = ["catalog", "import", "--db", DB, "--catalogo", "Sint", "lote.csv", *SYNTHETIC_ARGS]
    assert main(imported) == 0
    return tmp_path


def test_import_search_assign_save_delete_catalog_reopen(
    gate: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    # 1. Buscar con filtros (T-023): el perfil de 9,52 cm² está entre los de 9 a 10 cm².
    assert main(["catalog", "search", "--db", DB, "--area", "9..10 cm2"]) == 0
    found = capsys.readouterr().out
    assert "SINT-100x50-4-6" in found
    assert "SINT-100x55-5-6" not in found  # 11,00 cm² queda fuera de 9..10

    with CatalogStore.open(gate / DB) as store:
        info = store.catalog_by_name("Sint")
        source = store.latest(info.id, "SINTETICO-I", "SINT-100x50-4-6")
        assert len(store.list_profiles(info.id)) == 100

    # 2. Asignar a una barra de un proyecto y guardar.
    assert main(["project", "demo", "--out", "p.json", "--caso", "viga"]) == 0
    capsys.readouterr()
    assert main(["project", "assign-profile", "p.json", "B1", REF, "--db", DB]) == 0
    assigned = capsys.readouterr().out
    assert "barra «B1»: APLICADA" in assigned
    saved_hash = open_project(gate / "p.json").model_hash

    # 3. Borrar el SQLite: el proyecto se reabre y verifica sin catálogo.
    (gate / DB).unlink()
    assert main(["project", "verify", "p.json"]) == 0
    assert saved_hash in capsys.readouterr().out
    reopened = open_project(gate / "p.json")
    assert reopened.model_hash == saved_hash
    member = next(iter(reopened.model.members.values()))
    section = reopened.model.section(member.section)
    assert section.family is SectionFamily.CATALOGO

    # 4. Las propiedades son las del catálogo, con su procedencia y estado.
    for name in ("area", "iy", "iz"):
        prop = getattr(section.properties, name)
        assert prop.value == getattr(source.properties, name).value
        assert prop.status is PropertyStatus.IMPORTADO_SIN_VERIFICAR
    assert section.properties.torsion_constant.value is None  # J no aportada: ausente, nunca 0
    assert section.properties.iyz.status is PropertyStatus.CALCULADO  # simetría de la forma I
    assert section.parameters.provenance.source == source.provenance.source  # type: ignore[union-attr]
    assert section.parameters.source_sha256  # type: ignore[union-attr]

    # 5. El análisis plano usa el snapshot (sin catálogo); el espacial exige J y no se inventa.
    capsys.readouterr()
    assert main(["project", "analyze", "p.json", "--tipo", "plano"]) == 0
    assert "SUCCEEDED" in capsys.readouterr().out
    assert main(["project", "analyze", "p.json", "--tipo", "espacial"]) != 0
    assert "J" in capsys.readouterr().err
