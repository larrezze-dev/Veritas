"""Dominio del catálogo de perfiles (T-022 E1): entidades, estados, hash y documento."""

import json
import math
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from catalog_support import PROVENANCE, make_data, make_properties, prop, uid
from veritas.catalogs.errors import CatalogError
from veritas.catalogs.model import (
    CatalogInfo,
    CatalogProfile,
    ProfileDimension,
    ProfileFamily,
    Provenance,
    canonical_json,
    data_document,
    decode_profile,
    profile_document,
    profile_json,
    profile_sha256,
    same_content,
)
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.sections.properties import PropertyStatus, SectionProperty


def profile(**changes: Any) -> CatalogProfile:
    base = CatalogProfile(id=uid(1), revision=1, catalog_id=uid(100), data=make_data())
    return replace(base, **changes)


def code_of(error: pytest.ExceptionInfo[DiagnosticError]) -> DiagnosticCode:
    return error.value.code


# --- Estados y propiedades ---------------------------------------------------------------


def test_property_status_is_extended_additively() -> None:
    assert [s.value for s in PropertyStatus] == [
        "CALCULADO",
        "NO_DISPONIBLE",
        "IMPORTADO_SIN_VERIFICAR",
        "CONTRASTADO",
        "VERIFICADO",
    ]


@pytest.mark.parametrize(
    "status",
    [PropertyStatus.IMPORTADO_SIN_VERIFICAR, PropertyStatus.CONTRASTADO, PropertyStatus.VERIFICADO],
)
def test_new_statuses_require_a_finite_value(status: PropertyStatus) -> None:
    assert SectionProperty(1.0, "m²", "o", "m", status).value == 1.0
    with pytest.raises(TypeError):
        SectionProperty(None, "m²", "o", "m", status)
    with pytest.raises(ValueError, match="finito"):
        SectionProperty(math.inf, "m²", "o", "m", status)


@pytest.mark.parametrize("status", [PropertyStatus.CONTRASTADO, PropertyStatus.VERIFICADO])
def test_catalog_rejects_verified_states_in_t022(status: PropertyStatus) -> None:
    """Ningún dato importado se presenta como contrastado o verificado (SRS §5.2)."""
    claim = SectionProperty(1.0e-3, "m²", "o", "m", status)
    props = make_properties()
    with pytest.raises(CatalogError) as error:
        replace(props, area=claim)
    assert error.value.diagnostic.message_key == "catalog.invalid_status"


def test_catalog_rejects_calculated_state() -> None:
    claim = SectionProperty(1.0e-3, "m²", "o", "m", PropertyStatus.CALCULADO)
    with pytest.raises(CatalogError):
        replace(make_properties(), area=claim)


@pytest.mark.parametrize("value", [0.0, -0.0, -1.0, 1e-310])
def test_property_values_must_be_positive_and_normal(value: float) -> None:
    with pytest.raises(DiagnosticError):
        replace(make_properties(), iy=prop(value, "m⁴"))


def test_property_unit_dimension_is_checked() -> None:
    with pytest.raises(CatalogError) as error:
        replace(make_properties(), area=prop(1.0, "m⁴"))
    assert error.value.code is DiagnosticCode.UNITS_INCOMPATIBLE
    with pytest.raises(CatalogError):
        replace(make_properties(), linear_mass=prop(1.0, "m²"))


def test_unavailable_property_has_no_value_never_zero() -> None:
    props = make_properties(torsion=None)
    assert props.torsion_constant.status is PropertyStatus.NO_DISPONIBLE
    assert props.torsion_constant.value is None
    # J no se deduce de Iy + Iz.
    assert props.torsion_constant.value != props.iy.value + props.iz.value  # type: ignore[operator]


def test_properties_require_section_property_instances() -> None:
    with pytest.raises(TypeError):
        replace(make_properties(), iz=1.0)  # type: ignore[arg-type]


# --- Datos de perfil -----------------------------------------------------------------------


def test_data_normalizes_keys_and_orders_dimensions() -> None:
    data = make_data(designation="  P-100 ", series=" S ")
    assert data.key == ("S", "P-100")
    assert [d.name for d in data.dimensions] == ["b", "h"]


@pytest.mark.parametrize(
    "changes",
    [
        {"series": " "},
        {"designation": ""},
        {"family": "I"},
        {"dimensions": [ProfileDimension("h", 1.0)]},
        {"dimensions": (ProfileDimension("h", 1.0), ProfileDimension("h", 2.0))},
        {"warnings": ["catalog.aviso"]},
        {"warnings": ("catalog.aviso", "catalog.aviso")},
        {"warnings": ("Aviso",)},
        {"properties": None},
        {"provenance": None},
    ],
)
def test_data_validation(changes: dict[str, Any]) -> None:
    with pytest.raises(DiagnosticError):
        replace(make_data(), **changes)


@pytest.mark.parametrize("name", ["H", "1h", "h-1", "", "a b"])
def test_dimension_name_is_restricted(name: str) -> None:
    with pytest.raises(CatalogError):
        ProfileDimension(name, 1.0)


@pytest.mark.parametrize("value", [0.0, -1.0, math.nan, math.inf, True])
def test_dimension_value_must_be_positive_finite(value: float) -> None:
    with pytest.raises(DiagnosticError):
        ProfileDimension("h", value)


def test_provenance_is_mandatory() -> None:
    for field in ("source", "license", "edition", "method"):
        changes: dict[str, Any] = {field: "  "}
        with pytest.raises(DiagnosticError):
            replace(PROVENANCE, **changes)
    with pytest.raises(TypeError):
        Provenance("a", "b", "c")  # type: ignore[call-arg]


def test_family_includes_other() -> None:
    assert {f.value for f in ProfileFamily} == {
        "I", "U", "L", "T", "SHS", "RHS", "CHS", "RECT", "ROUND", "OTRO",
    }  # fmt: skip


def test_profile_identity_is_validated() -> None:
    with pytest.raises(TypeError):
        profile(id="no-uuid")
    with pytest.raises(ValueError, match=">= 1"):
        profile(revision=0)
    with pytest.raises(TypeError):
        profile(revision=True)
    with pytest.raises(TypeError):
        profile(data="no")
    with pytest.raises(TypeError):
        profile(catalog_id=uid(100).hex)


def test_catalog_info_validation() -> None:
    info = CatalogInfo(uid(5), " Mi catálogo ", "", "fuente", "licencia", "2026", False)
    assert info.name == "Mi catálogo"
    for field in ("name", "source", "license", "edition"):
        changes: dict[str, Any] = {field: ""}
        with pytest.raises(DiagnosticError):
            replace(info, **changes)
    with pytest.raises(TypeError):
        replace(info, protected=1)  # type: ignore[arg-type]


# --- Contenido, hash y documento ---------------------------------------------------------------


def test_same_content_ignores_identity_but_not_values() -> None:
    assert same_content(make_data(), make_data())
    assert not same_content(make_data(), make_data(area=1.0000001e-3))
    assert not same_content(make_data(), make_data(torsion=1e-8))
    assert not same_content(make_data(), make_data(warnings=("catalog.aviso",)))
    assert not same_content(make_data(), make_data(family=ProfileFamily.U))


def test_hash_is_deterministic_and_sensitive() -> None:
    first = profile_sha256(profile())
    assert first == profile_sha256(profile())
    assert len(first) == 64
    assert first != profile_sha256(profile(revision=2))
    assert first != profile_sha256(profile(id=uid(2)))
    assert first != profile_sha256(profile(catalog_id=uid(101)))
    assert first != profile_sha256(profile(data=make_data(area=2e-3)))


def test_document_is_canonical_json() -> None:
    text = profile_json(profile())
    assert text == canonical_json(json.loads(text))
    assert "É" in text  # UTF-8 sin escapes
    document = profile_document(profile())
    assert document["format"] == "veritas-catalog-profile/1"
    assert document["data"] == data_document(profile().data)


def test_roundtrip_is_exact_for_awkward_floats() -> None:
    values = [0.1 + 0.2, 1.0 / 3.0, 1e-9, 123456789.123456789, 5e-324 * 1e300, 2.5e-5]
    for value in values:
        original = profile(data=make_data(area=value))
        decoded = decode_profile(profile_json(original))
        assert repr(decoded.properties.area.value) == repr(value)
        assert profile_json(decoded) == profile_json(original)
        assert decoded == original


def test_roundtrip_keeps_all_fields_and_unavailable() -> None:
    original = profile(data=make_data(warnings=("catalog.aviso_masa",), torsion=None))
    decoded = decode_profile(profile_json(original))
    assert decoded == original
    assert decoded.properties.torsion_constant.status is PropertyStatus.NO_DISPONIBLE
    assert decoded.properties.torsion_constant.value is None
    assert decoded.data.warnings == ("catalog.aviso_masa",)


def _document() -> dict[str, Any]:
    return json.loads(profile_json(profile()))  # type: ignore[no-any-return]


def _mutations() -> list[tuple[str, str]]:
    cases: list[tuple[str, str]] = []

    def add(label: str, mutate: Callable[[dict[str, Any]], object]) -> None:
        document = _document()
        mutate(document)
        cases.append((label, json.dumps(document)))

    add("formato", lambda d: d.update(format="otro/1"))
    add("clave extra", lambda d: d.update(extra=1))
    add("clave que falta", lambda d: d.pop("catalog_id"))
    add("revision bool", lambda d: d.update(revision=True))
    add("revision texto", lambda d: d.update(revision="1"))
    add("id no uuid", lambda d: d.update(id="x"))
    add("id v1", lambda d: d.update(id="00000000-0000-1000-8000-000000000001"))
    add("estado verificado", lambda d: d["data"]["properties"]["area"].update(status="VERIFICADO"))
    add("valor entero", lambda d: d["data"]["properties"]["area"].update(value=1))
    add("valor cero", lambda d: d["data"]["properties"]["area"].update(value=0.0))
    add("familia desconocida", lambda d: d["data"].update(family="Z"))
    add("propiedad extra", lambda d: d["data"]["properties"].update(wel=1.0))
    add("dimension entera", lambda d: d["data"].update(dimensions=[{"name": "h", "value": 1}]))
    add("avisos no lista", lambda d: d["data"].update(warnings="x"))
    add("procedencia vacia", lambda d: d["data"]["provenance"].update(source=""))
    cases.append(("no es json", "{"))
    cases.append(("lista", "[]"))
    cases.append(("NaN", json.dumps(_document()).replace('"revision": 1', '"revision": NaN')))
    return cases


@pytest.mark.parametrize(("label", "text"), _mutations(), ids=[c[0] for c in _mutations()])
def test_decode_rejects_invalid_documents(label: str, text: str) -> None:
    with pytest.raises(CatalogError) as error:
        decode_profile(text)
    assert error.value.diagnostic.message_key == "catalog.invalid_document", label
    assert error.value.diagnostic.cause is not None
