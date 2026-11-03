"""Aislamiento de capas del catálogo y no regresión de ``PropertyStatus`` (T-022 E1).

Análisis estático con ``ast`` (helpers de ``test_layer_isolation``); no se importan los módulos.
"""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from test_layer_isolation import FORBIDDEN_EXTERNAL, PACKAGE_ROOT, imported_modules
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.sections.section import build_rectangular_section
from veritas.core.units import Quantity, get_unit

CATALOGS = PACKAGE_ROOT / "catalogs"


def tops(path: Path) -> set[str]:
    return {name.split(".")[0] for name in imported_modules(path, PACKAGE_ROOT)}


def test_catalog_modules_exist() -> None:
    assert {p.name for p in CATALOGS.glob("*.py")} >= {
        "__init__.py",
        "columns.py",
        "errors.py",
        "importer.py",
        "model.py",
        "store.py",
    }


def test_only_the_store_imports_sqlite3() -> None:
    importers = {
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*.py")
        if "sqlite3" in tops(path)
    }
    assert importers == {"catalogs/store.py"}


def test_core_does_not_import_catalogs_or_sqlite() -> None:
    for path in (PACKAGE_ROOT / "core").rglob("*.py"):
        names = imported_modules(path, PACKAGE_ROOT)
        assert not any(n == "veritas.catalogs" or n.startswith("veritas.catalogs.") for n in names)
        assert "sqlite3" not in tops(path)


@pytest.mark.parametrize("path", sorted(CATALOGS.glob("*.py")), ids=lambda p: p.name)
def test_catalogs_do_not_import_upper_layers(path: Path) -> None:
    names = imported_modules(path, PACKAGE_ROOT)
    assert not tops(path) & FORBIDDEN_EXTERNAL
    forbidden = ("fem", "gui", "reports", "persistence", "design_codes", "section_editor")
    for layer in forbidden:
        assert not any(n == f"veritas.{layer}" or n.startswith(f"veritas.{layer}.") for n in names)
    assert "PySide6" not in tops(path)


def test_domain_modules_are_free_of_sqlite() -> None:
    for name in ("model.py", "errors.py", "__init__.py", "columns.py", "importer.py"):
        assert "sqlite3" not in tops(CATALOGS / name)


def test_the_importer_is_pure_and_does_not_reach_the_store() -> None:
    """El importador solo lee y valida (T-022 E2): no importa el almacén ni escribe."""
    for name in ("columns.py", "importer.py"):
        names = imported_modules(CATALOGS / name, PACKAGE_ROOT)
        assert "veritas.catalogs.store" not in names
        assert not any(n.startswith("veritas.persistence") for n in names)


def test_only_catalogs_depend_on_the_catalog_store() -> None:
    users = {
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*.py")
        if any(n.startswith("veritas.catalogs") for n in imported_modules(path, PACKAGE_ROOT))
    }
    assert users
    assert all(user.startswith("catalogs/") or user == "cli_catalog.py" for user in users)


# --- Unidades que usará el importador (hecho 4 del plan): ya estaban registradas -------------


@pytest.mark.parametrize(
    ("symbol", "dimension", "factor"),
    [
        ("mm", u.LENGTH, 1e-3),
        ("cm", u.LENGTH, 1e-2),
        ("mm²", u.AREA, 1e-6),
        ("cm²", u.AREA, 1e-4),
        ("cm³", u.SECTION_MODULUS, 1e-6),
        ("mm³", u.SECTION_MODULUS, 1e-9),
        ("cm⁴", u.SECOND_MOMENT, 1e-8),
        ("mm⁴", u.SECOND_MOMENT, 1e-12),
        ("kg/m", u.LINEAR_MASS, 1.0),
    ],
)
def test_import_units_are_registered(symbol: str, dimension: u.Dimension, factor: float) -> None:
    unit = get_unit(symbol)
    assert unit.dimension == dimension
    assert unit.factor == factor
    assert Quantity.of(2.0, symbol).si_value == pytest.approx(2.0 * factor, rel=1e-15)


# --- PropertyStatus: cambio aditivo ----------------------------------------------------------


def test_existing_statuses_keep_their_names_and_order() -> None:
    names = [s.name for s in PropertyStatus]
    assert names[:2] == ["CALCULADO", "NO_DISPONIBLE"]
    assert str(PropertyStatus.CALCULADO) == "CALCULADO"
    assert str(PropertyStatus.NO_DISPONIBLE) == "NO_DISPONIBLE"


def test_rectangular_section_properties_are_still_calculated() -> None:
    section = build_rectangular_section(
        uuid4(), 1, b=Quantity.of(0.2, "m"), h=Quantity.of(0.3, "m"), name="R"
    )
    for name in ("area", "iy", "iz", "torsion_constant"):
        assert getattr(section.properties, name).status is PropertyStatus.CALCULADO


@pytest.mark.parametrize(
    "status",
    [PropertyStatus.IMPORTADO_SIN_VERIFICAR, PropertyStatus.CONTRASTADO, PropertyStatus.VERIFICADO],
)
def test_rectangular_section_rejects_non_positive_area_in_any_status(
    status: PropertyStatus,
) -> None:
    section = build_rectangular_section(
        uuid4(), 1, b=Quantity.of(1, "m"), h=Quantity.of(2, "m"), name="R"
    )
    bad = SectionProperty(-1.0, "m²", "o", "m", status)
    with pytest.raises(DiagnosticError):
        replace(section, properties=replace(section.properties, area=bad))
