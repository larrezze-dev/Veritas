"""Dominio de configuraciones de normativas: validación, protegida y cobertura (T-014 E1)."""

import math
from dataclasses import replace
from uuid import UUID

import pytest

from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.materials.material import MaterialFamily
from veritas.core.messages_es import MESSAGES_ES, render_message
from veritas.core.references import RevisionRef
from veritas.design_codes.builtin import (
    CTE_PROTECTED,
    CTE_PROTECTED_ID,
    Coverage,
    coverage,
    protected_configurations,
)
from veritas.design_codes.norms import (
    NormConfiguration,
    NormKind,
    NormParameter,
    NormSystem,
)

ID = UUID(int=7 | (4 << 76) | (0b10 << 62))
OTHER_ID = UUID(int=8 | (4 << 76) | (0b10 << 62))


def parameter(**changes: object) -> NormParameter:
    base: dict[str, object] = {
        "key": "gamma_m0",
        "value": 1.05,
        "dimension": u.DIMENSIONLESS,
        "unit": "1",
        "source": "Documento de prueba, cláusula 1",
    }
    base.update(changes)
    return NormParameter(**base)  # type: ignore[arg-type]


def configuration(**changes: object) -> NormConfiguration:
    base: dict[str, object] = {
        "id": ID,
        "revision": 1,
        "name": "Mi normativa",
        "kind": NormKind.USER,
        "system": NormSystem.OTHER,
        "jurisdiction": "Prueba",
        "edition": "2020",
        "documents": ("Doc A",),
        "description": "",
        "derived_from": None,
        "parameters": (),
    }
    base.update(changes)
    return NormConfiguration(**base)  # type: ignore[arg-type]


def error_of(action: object) -> DiagnosticError:
    assert callable(action)
    with pytest.raises(DiagnosticError) as info:
        action()
    return info.value


# --- NormParameter -----------------------------------------------------------------------


def test_parameter_valid_keeps_si_value_and_negative_zero() -> None:
    p = parameter(value=-0.0)
    assert math.copysign(1.0, p.value) == -1.0
    q = parameter(key="f_y.k", value=275, dimension=u.STRESS, unit="MPa", note="n")
    assert q.value == 275.0
    assert isinstance(q.value, float)
    assert q.note == "n"


@pytest.mark.parametrize("key", ["", "Gamma", "gamma m", "γ", "a-b", "a/b", " a", "a\n"])
def test_parameter_rejects_bad_keys(key: str) -> None:
    assert error_of(lambda: parameter(key=key)).code in {
        DiagnosticCode.INPUT_INVALID_TYPE,
        DiagnosticCode.INPUT_OUT_OF_RANGE,
    }


def test_parameter_key_accepts_documented_alphabet() -> None:
    assert parameter(key="a0_b.c1").key == "a0_b.c1"


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_parameter_rejects_nonfinite(value: float) -> None:
    assert error_of(lambda: parameter(value=value)).code is DiagnosticCode.INPUT_NONFINITE


@pytest.mark.parametrize("value", [True, "1", None, 1 + 0j])
def test_parameter_rejects_wrong_value_type(value: object) -> None:
    assert error_of(lambda: parameter(value=value)).code is DiagnosticCode.INPUT_INVALID_TYPE


def test_parameter_rejects_unit_not_matching_dimension() -> None:
    err = error_of(lambda: parameter(dimension=u.STRESS, unit="kN"))
    assert err.code is DiagnosticCode.UNITS_INCOMPATIBLE


def test_parameter_rejects_unknown_unit() -> None:
    assert error_of(lambda: parameter(unit="furlong")).code is DiagnosticCode.UNITS_UNKNOWN


@pytest.mark.parametrize("source", ["", "   ", None, 3])
def test_parameter_source_is_mandatory(source: object) -> None:
    assert error_of(lambda: parameter(source=source)).code is DiagnosticCode.INPUT_INVALID_TYPE


def test_parameter_rejects_bad_dimension_type_and_note_type() -> None:
    assert error_of(lambda: parameter(dimension="STRESS")).code is DiagnosticCode.INPUT_INVALID_TYPE
    assert error_of(lambda: parameter(note=3)).code is DiagnosticCode.INPUT_INVALID_TYPE


# --- NormConfiguration -------------------------------------------------------------------


def test_configuration_valid_and_ref() -> None:
    c = configuration(parameters=(parameter(), parameter(key="gamma_m1")))
    assert c.ref == RevisionRef(ID, 1)
    assert [p.key for p in c.parameters] == ["gamma_m0", "gamma_m1"]


@pytest.mark.parametrize("name", ["", "  ", None, 4])
def test_configuration_rejects_bad_name(name: object) -> None:
    assert error_of(lambda: configuration(name=name)).code is DiagnosticCode.INPUT_INVALID_TYPE


@pytest.mark.parametrize("field", ["jurisdiction", "edition"])
@pytest.mark.parametrize("value", ["", "   ", None])
def test_configuration_rejects_empty_text_fields(field: str, value: object) -> None:
    assert (
        error_of(lambda: configuration(**{field: value})).code is DiagnosticCode.INPUT_INVALID_TYPE
    )


@pytest.mark.parametrize("field", ["kind", "system"])
@pytest.mark.parametrize("value", ["USER", "CTE", None, 1])
def test_configuration_rejects_non_enum_kind_and_system(field: str, value: object) -> None:
    assert (
        error_of(lambda: configuration(**{field: value})).code is DiagnosticCode.INPUT_INVALID_TYPE
    )


@pytest.mark.parametrize("revision", [0, -1])
def test_configuration_rejects_revision_below_one(revision: int) -> None:
    with pytest.raises(ValueError, match="revision"):
        configuration(revision=revision)


@pytest.mark.parametrize("revision", [True, 1.0, "1", None])
def test_configuration_rejects_non_integer_revision(revision: object) -> None:
    with pytest.raises(TypeError, match="revision"):
        configuration(revision=revision)


def test_configuration_rejects_bad_id() -> None:
    with pytest.raises(TypeError, match="id"):
        configuration(id="no-uuid")
    with pytest.raises(TypeError, match="id"):
        configuration(id=UUID(int=1))  # no es v4


def test_configuration_documents_must_be_tuple_of_unique_texts() -> None:
    assert configuration(documents=()).documents == ()
    assert error_of(lambda: configuration(documents=["A"])).code is (
        DiagnosticCode.INPUT_INVALID_TYPE
    )
    assert error_of(lambda: configuration(documents=("A", ""))).code is (
        DiagnosticCode.INPUT_INVALID_TYPE
    )
    dup = error_of(lambda: configuration(documents=("A", "A")))
    assert dup.code is DiagnosticCode.INPUT_OUT_OF_RANGE
    assert dup.diagnostic.message_key == "norm.duplicate_document"


def test_configuration_description_is_text() -> None:
    assert configuration(description="algo").description == "algo"
    assert error_of(lambda: configuration(description=None)).code is (
        DiagnosticCode.INPUT_INVALID_TYPE
    )


def test_configuration_rejects_duplicate_parameter_keys() -> None:
    err = error_of(lambda: configuration(parameters=(parameter(), parameter(value=2.0))))
    assert err.code is DiagnosticCode.INPUT_OUT_OF_RANGE
    assert err.diagnostic.message_key == "norm.duplicate_parameter"
    assert err.diagnostic.params_dict()["key"] == "gamma_m0"


def test_configuration_parameters_must_be_tuple_of_parameters() -> None:
    assert error_of(lambda: configuration(parameters=[parameter()])).code is (
        DiagnosticCode.INPUT_INVALID_TYPE
    )
    assert error_of(lambda: configuration(parameters=("gamma",))).code is (
        DiagnosticCode.INPUT_INVALID_TYPE
    )


def test_configuration_derived_from_type_and_self_reference() -> None:
    other = RevisionRef(OTHER_ID, 3)
    assert configuration(derived_from=other).derived_from == other
    assert error_of(lambda: configuration(derived_from=OTHER_ID)).code is (
        DiagnosticCode.INPUT_INVALID_TYPE
    )
    err = error_of(lambda: configuration(derived_from=RevisionRef(ID, 1)))
    assert err.diagnostic.message_key == "norm.self_derived"


def test_configuration_is_immutable_and_comparable() -> None:
    c = configuration()
    with pytest.raises(AttributeError):
        c.name = "otro"  # type: ignore[misc]
    assert replace(c) == c
    assert replace(c, name="otro") != c


# --- Protegida ---------------------------------------------------------------------------


def test_protected_cte_is_identification_only() -> None:
    c = CTE_PROTECTED
    assert c.kind is NormKind.PROTECTED
    assert c.system is NormSystem.CTE
    assert c.id == CTE_PROTECTED_ID
    assert c.revision == 1
    assert c.name == "CTE España (DB-SE, DB-SE-AE, DB-SE-A, DB-SE-M)"
    assert c.edition == "NO FIJADA"
    assert c.documents == ("DB-SE", "DB-SE-AE", "DB-SE-A", "DB-SE-M")
    assert c.derived_from is None


def test_protected_cte_has_no_numeric_parameters() -> None:
    assert CTE_PROTECTED.parameters == ()


def test_protected_listing_is_exactly_the_cte_template() -> None:
    assert protected_configurations() == (CTE_PROTECTED,)


# --- Cobertura ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", list(MaterialFamily))
def test_coverage_is_not_checked_for_every_family(family: MaterialFamily) -> None:
    assert coverage(CTE_PROTECTED, family) is Coverage.NO_COMPROBADO
    assert coverage(configuration(), family) is Coverage.NO_COMPROBADO


@pytest.mark.parametrize("family", list(MaterialFamily))
def test_coverage_ignores_data_claiming_approval(family: MaterialFamily) -> None:
    tricky = configuration(
        name="APROBADO",
        description="comprobado y aprobado",
        edition="APROBADO",
        documents=("COMPROBADO",),
        parameters=(
            parameter(key="aprobado", value=1.0, source="APROBADO"),
            parameter(key="coverage", value=1.0),
            parameter(key="comprobado", value=1.0),
        ),
    )
    assert coverage(tricky, family) is Coverage.NO_COMPROBADO


def test_coverage_validates_argument_types() -> None:
    assert error_of(lambda: coverage("cte", MaterialFamily.STEEL)).code is (  # type: ignore[arg-type]
        DiagnosticCode.INPUT_INVALID_TYPE
    )
    assert error_of(lambda: coverage(CTE_PROTECTED, "STEEL")).code is (  # type: ignore[arg-type]
        DiagnosticCode.INPUT_INVALID_TYPE
    )


def test_coverage_is_not_a_configuration_field() -> None:
    fields = set(NormConfiguration.__dataclass_fields__)
    assert not {f for f in fields if "coverage" in f or "cobertura" in f}


# --- Mensajes ----------------------------------------------------------------------------


def test_norm_messages_exist_in_spanish_catalog() -> None:
    keys = {k for k in MESSAGES_ES if k.startswith("norm.")}
    assert {
        "norm.protected",
        "norm.in_use",
        "norm.duplicate_parameter",
        "norm.duplicate_document",
        "norm.invalid_key",
        "norm.self_derived",
    } <= keys
    err = error_of(lambda: configuration(parameters=(parameter(), parameter())))
    assert render_message(err.diagnostic) != "norm.duplicate_parameter"
    assert "gamma_m0" in render_message(err.diagnostic)


# --- Aislamiento de capas ----------------------------------------------------------------


def test_design_codes_is_pure_domain() -> None:
    from test_layer_isolation import PACKAGE_ROOT, imported_modules  # noqa: PLC0415

    forbidden = {
        "PySide6",
        "shiboken6",
        "pyvista",
        "pyvistaqt",
        "vtk",
        "vtkmodules",
        "openseespy",
        "veritas.gui",
        "veritas.fem",
        "veritas.persistence",
        "veritas.reports",
        "veritas.cli",
    }
    files = sorted((PACKAGE_ROOT / "design_codes").rglob("*.py"))
    assert {f.name for f in files} >= {"norms.py", "builtin.py", "registry.py"}
    for path in files:
        for name in imported_modules(path, PACKAGE_ROOT):
            assert not any(name == bad or name.startswith(f"{bad}.") for bad in forbidden), (
                f"{path.name} importa {name}"
            )
