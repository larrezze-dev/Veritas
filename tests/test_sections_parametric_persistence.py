"""Esquema v5: rama PARAMETRICA, clave opcional ``shear`` y migración 4 → 5 (plan T-025 E1, D9-B).

La prueba de salida es la del plan: un proyecto v4 AUTÉNTICO (``tests/data/proyecto_v4_ref.json``,
escrito con el código de T-024 antes de tocar el esquema: rectángulo, perfil de catálogo y una
corrida espacial) se abre bajo v5 con el ``model_hash`` idéntico y su corrida sigue CURRENT.
"""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from test_persistence_integrity import edit, mdl
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily
from veritas.core.sections.parametric import ParametricShape
from veritas.core.sections.section import SectionFamily, build_rectangular_section
from veritas.persistence.canonical import model_hash
from veritas.persistence.errors import IntegrityError
from veritas.persistence.migrations import CURRENT_SCHEMA_VERSION, MIGRATIONS, migrate
from veritas.persistence.project import open_project, save_project
from veritas.persistence.runs import Validity, validity

REFERENCE_V4 = Path(__file__).parent / "data" / "proyecto_v4_ref.json"
REFERENCE_V4_HASH = "9d68bdcd12b5da58a5c353bb0610a77ca02c7a556bdd3b6f872ea9e3fc3d7551"

I, RHS, SHS, CHS = (  # noqa: E741 (etiquetas de forma)
    ParametricShape.I,
    ParametricShape.RHS,
    ParametricShape.SHS,
    ParametricShape.CHS,
)


def mm(**dims: float) -> dict[str, u.Quantity]:
    return {name: u.Quantity.of(value, "mm") for name, value in dims.items()}


def parametric_model() -> StructuralModel:
    model = StructuralModel()
    material = model.add_material(
        name="Acero",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=u.Quantity.of(210_000, "N/mm²"),
        shear_modulus=u.Quantity.of(81_000, "N/mm²"),
        density=u.Quantity.of(7850, "kg/m³"),
    )
    sections = [
        model.add_parametric_section(
            I, mm(h=300, b=150, tw=7.1, tf=10.7, r=15), name="I 300 con r"
        ),
        model.add_parametric_section(I, mm(h=300, b=150, tw=7.1, tf=10.7), name="I 300 viva"),
        model.add_parametric_section(RHS, mm(h=300, b=200, t=10, ro=15), name="RHS"),
        model.add_parametric_section(SHS, mm(b=100, t=5), name="SHS"),
        model.add_parametric_section(CHS, mm(d=100, t=5), name="CHS"),
        model.register_section(
            build_rectangular_section(
                sections_id(),
                1,
                b=u.Quantity.of(0.2, "m"),
                h=u.Quantity.of(0.4, "m"),
                with_shear=True,
            )
        ),
    ]
    sections.append(model.revise_parametric_section(sections[0].id, dimensions=mm(tf=12)))
    origin = model.add_node(u.Quantity.of(0, "m"), u.Quantity.of(0, "m"), u.Quantity.of(0, "m"))
    for index, section in enumerate(sections):
        end = model.add_node(
            u.Quantity.of(1 + index, "m"), u.Quantity.of(0, "m"), u.Quantity.of(0, "m")
        )
        model.add_member(origin.id, end.id, material=material.ref, section=section.ref)
    return model


def sections_id() -> UUID:
    return UUID(int=99 | (4 << 76) | (0b10 << 62))


def open_error(path: Path) -> DiagnosticError:
    with pytest.raises(DiagnosticError) as info:
        open_project(path)
    return info.value


# --- Ida y vuelta ---------------------------------------------------------------------------


def test_round_trip_is_exact_for_every_shape(tmp_path: Path) -> None:
    model = parametric_model()
    path = tmp_path / "p.json"
    saved = save_project(path, model)
    opened = open_project(path)
    assert opened.schema_version == CURRENT_SCHEMA_VERSION == 7
    assert repr(opened.model.snapshot()) == repr(model.snapshot())
    assert opened.model_hash == saved.model_hash == model_hash(model.snapshot())
    families = {s.family for s in opened.model.snapshot().sections}
    assert families == {SectionFamily.PARAMETRICA, SectionFamily.RECTANGULAR}
    unavailable = [
        s for s in opened.model.snapshot().sections if s.properties.torsion_constant.value is None
    ]
    assert unavailable  # I y RHS: J NO_DISPONIBLE sigue siendo None tras la ida y vuelta
    assert all(s.shear is not None for s in opened.model.snapshot().sections)


def test_file_encodes_family_shape_dimensions_and_optional_shear(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    save_project(path, parametric_model())
    sections = json.loads(path.read_text(encoding="utf-8"))["content"]["model"]["sections"]
    by_name = {s["name"]: s for s in sections}
    chs = by_name["CHS"]
    assert chs["family"] == "PARAMETRICA"
    assert chs["parameters"] == {
        "shape": "CHS",
        "dimensions": [{"name": "d", "value": 0.1}, {"name": "t", "value": 0.005}],
    }
    assert set(chs["shear"]) == {"shear_y", "shear_z"}
    assert chs["shear"]["shear_z"]["value"] > 0.0
    assert [d["name"] for d in by_name["I 300 viva"]["parameters"]["dimensions"]] == [
        "h",
        "b",
        "tw",
        "tf",
        "r",
    ]
    assert by_name["I 300 viva"]["parameters"]["dimensions"][4] == {"name": "r", "value": 0.0}


def test_default_rectangle_is_encoded_without_a_shear_key_so_hashes_do_not_move(
    tmp_path: Path,
) -> None:
    model = StructuralModel()
    plain = model.add_rectangular_section(b=u.Quantity.of(0.2, "m"), h=u.Quantity.of(0.4, "m"))
    path = tmp_path / "p.json"
    save_project(path, model)
    section = json.loads(path.read_text(encoding="utf-8"))["content"]["model"]["sections"][0]
    assert "shear" not in section
    assert set(section) == {"id", "revision", "name", "family", "parameters", "properties"}
    assert plain.shear is None
    assert open_project(path).model.section(plain.ref).shear is None  # ausente = None, nunca 0


# --- Proyecto v4 auténtico bajo v5: hash idéntico ----------------------------------------


def test_a_real_v4_project_opens_under_v5_with_the_identical_model_hash() -> None:
    raw = json.loads(REFERENCE_V4.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 4
    assert raw["model_hash"] == REFERENCE_V4_HASH  # guardado por el código v4, antes del cambio
    opened = open_project(REFERENCE_V4)
    assert opened.schema_version == 4  # versión del archivo; el contenido se migró al abrir
    assert opened.model_hash == REFERENCE_V4_HASH
    assert model_hash(opened.model.snapshot()) == REFERENCE_V4_HASH
    families = {s.family for s in opened.model.snapshot().sections}
    assert families == {SectionFamily.RECTANGULAR, SectionFamily.CATALOGO}
    assert all(s.shear is None for s in opened.model.snapshot().sections)
    assert len(opened.runs) == 1
    assert validity(opened.runs[0], opened.model) is Validity.CURRENT  # sin STALE por el cambio


def test_resaving_the_v4_project_as_v5_keeps_hash_and_runs(tmp_path: Path) -> None:
    opened = open_project(REFERENCE_V4)
    target = tmp_path / "v5.json"
    saved = save_project(target, opened.model, opened.user_profiles, runs=opened.runs)
    assert saved.schema_version == CURRENT_SCHEMA_VERSION
    again = open_project(target)
    assert again.schema_version == CURRENT_SCHEMA_VERSION
    assert again.model_hash == REFERENCE_V4_HASH
    assert validity(again.runs[0], again.model) is Validity.CURRENT
    assert repr(again.model.snapshot()) == repr(opened.model.snapshot())


# --- Manipulación y ramas explícitas -------------------------------------------------------


def saved_parametric(tmp_path: Path) -> Path:
    path = tmp_path / "p.json"
    save_project(path, parametric_model())
    return path


def index_of(data: dict[str, Any], name: str) -> int:
    return next(i for i, s in enumerate(mdl(data)["sections"]) if s["name"] == name)


def mutate(name: str, change: Any) -> Any:
    def apply(data: dict[str, Any]) -> None:
        change(mdl(data)["sections"][index_of(data, name)])

    return apply


def test_tampering_without_resigning_fails_the_integrity_check(tmp_path: Path) -> None:
    path = edit(
        saved_parametric(tmp_path),
        mutate("I 300 viva", lambda s: s["properties"]["area"].update(value=1.0)),
        sign=False,
    )
    with pytest.raises(IntegrityError):
        open_project(path)


@pytest.mark.parametrize(
    ("section", "change", "key"),
    [
        (
            "I 300 viva",
            lambda s: s["properties"]["area"].update(value=0.0099),
            "section.properties_mismatch",
        ),
        (
            "I 300 viva",
            lambda s: s["properties"]["torsion_constant"].update(value=1e-6, status="CALCULADO"),
            "section.properties_mismatch",
        ),
        (
            "CHS",
            lambda s: s["shear"]["shear_z"].update(value=1e-3, status="CALCULADO"),
            "section.shear_mismatch",
        ),
        (
            "I 300 viva",
            lambda s: s["shear"]["shear_z"].update(value=1e-3),
            "section.shear_mismatch",
        ),
        (
            "I 300 viva",
            lambda s: s["shear"]["shear_z"].update(status="CONTRASTADO"),
            "section.shear_invalid_status",
        ),
        (
            "I 300 viva",
            lambda s: s["parameters"]["dimensions"][3].update(value=0.16),
            "section.geometry_flange_too_thick",
        ),
        (
            "I 300 viva",
            lambda s: s["parameters"]["dimensions"][2].update(value=0.2),
            "section.geometry_web_too_thick",
        ),
        (
            "I 300 viva",
            lambda s: s["parameters"]["dimensions"][4].update(value=0.2),
            "section.geometry_radius_too_large",
        ),
        (
            "CHS",
            lambda s: s["parameters"]["dimensions"][1].update(value=0.05),
            "section.geometry_wall_too_thick_circle",
        ),
        ("SHS", lambda s: s["parameters"]["dimensions"].reverse(), "section.geometry_dimensions"),
        ("SHS", lambda s: s["parameters"]["dimensions"].pop(), "section.geometry_dimensions"),
        (
            "I 300 viva",
            lambda s: s["parameters"]["dimensions"][0].update(name="H"),
            "section.geometry_dimensions",
        ),
        ("CHS", lambda s: s["parameters"].update(shape="RECT"), "project.invalid_field"),
        ("CHS", lambda s: s["parameters"].update(shape="SHS"), "section.geometry_dimensions"),
        ("CHS", lambda s: s.update(parameters={"b": 0.1, "h": 0.1}), "project.unknown_fields"),
        ("CHS", lambda s: s.update(family="RECTANGULAR"), "project.unknown_fields"),
        ("RHS", lambda s: s.update(family="NOEXISTE"), "project.invalid_field"),
        ("CHS", lambda s: s["shear"].update(extra=1), "project.unknown_fields"),
        ("CHS", lambda s: s["shear"].pop("shear_y"), "project.missing_fields"),
        ("CHS", lambda s: s.update(unknown=1), "project.unknown_fields"),
    ],
)
def test_resigned_inconsistent_sections_are_rejected(
    tmp_path: Path, section: str, change: Any, key: str
) -> None:
    error = open_error(edit(saved_parametric(tmp_path), mutate(section, change)))
    assert error.diagnostic.message_key == key


def test_a_parametric_family_never_falls_back_to_the_rectangle(tmp_path: Path) -> None:
    """Con ``family = PARAMETRICA`` y parámetros b/h no se interpreta como rectángulo."""
    error = open_error(
        edit(
            saved_parametric(tmp_path),
            mutate("SHS", lambda s: s.update(parameters={"b": 0.1, "h": 0.1})),
        )
    )
    assert error.diagnostic.message_key == "project.unknown_fields"


def test_a_catalog_section_cannot_carry_shear(tmp_path: Path) -> None:
    raw = json.loads(REFERENCE_V4.read_text(encoding="utf-8"))
    raw["schema_version"] = 5

    def add_shear(data: dict[str, Any]) -> None:
        data.update(raw)
        catalog = next(s for s in mdl(data)["sections"] if s["family"] == "CATALOGO")
        donor = {
            "value": None,
            "unit": "m²",
            "origin": "x",
            "method": "shear-energy-straight-cut/1",
            "status": "NO_DISPONIBLE",
        }
        catalog["shear"] = {"shear_y": donor, "shear_z": dict(donor)}

    source = tmp_path / "source.json"
    source.write_text(REFERENCE_V4.read_text(encoding="utf-8"), encoding="utf-8")
    error = open_error(edit(source, add_shear))
    assert error.diagnostic.message_key == "section.shear_not_derivable"


# --- Migración 4 → 5 ---------------------------------------------------------------------


def v4_content(*sections: dict[str, Any]) -> dict[str, object]:
    return {"model": {"sections": list(sections)}, "settings": {}, "runs": [], "norms": {}}


def test_migration_4_to_5_only_verifies() -> None:
    content = v4_content({"family": "RECTANGULAR"}, {"family": "CATALOGO"})
    migrated = migrate(content, 4)
    assert migrated == content
    assert migrated is not content
    assert 4 in MIGRATIONS
    assert migrate(content, CURRENT_SCHEMA_VERSION) is content  # ya está en la vigente


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ({"family": "PARAMETRICA"}, "project.invalid_field"),
        ({"family": "OTRA"}, "project.invalid_field"),
        ({"family": "RECTANGULAR", "shear": {}}, "project.unknown_fields"),
        ({"family": "CATALOGO", "shear": {}}, "project.unknown_fields"),
    ],
)
def test_migration_4_to_5_rejects_what_is_not_v4(section: dict[str, Any], key: str) -> None:
    with pytest.raises(DiagnosticError) as info:
        migrate(v4_content(section), 4)
    assert info.value.diagnostic.message_key == key


def test_the_full_chain_from_v3_reaches_v5() -> None:
    content = v4_content({"family": "RECTANGULAR"})
    assert migrate(content, 3) == content
    with pytest.raises(DiagnosticError):  # una familia de v4 no es de v3
        migrate(v4_content({"family": "CATALOGO"}), 3)


def test_a_v4_file_with_a_v5_family_is_rejected_on_open(tmp_path: Path) -> None:
    source = tmp_path / "v4.json"
    source.write_text(REFERENCE_V4.read_text(encoding="utf-8"), encoding="utf-8")
    path = edit(
        source,
        lambda d: next(s for s in mdl(d)["sections"] if s["family"] == "RECTANGULAR").update(
            family="PARAMETRICA"
        ),
    )
    assert open_error(path).diagnostic.message_key == "project.invalid_field"
