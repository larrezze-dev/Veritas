"""Revisiones de material (plan T-004, D5; MAT-001, MAT-002)."""

import dataclasses
import math
from typing import Any
from uuid import uuid1, uuid4

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.materials.material import MaterialFamily, MaterialRevision, build_material
from veritas.core.units import Quantity


def steel(**overrides: Any) -> MaterialRevision:
    fields: dict[str, Any] = {
        "name": "Acero",
        "family": MaterialFamily.STEEL,
        "source": "CTE DB-SE-A §4.2",
        "elastic_modulus": Quantity.of(210_000, "N/mm²"),
        "shear_modulus": Quantity.of(81_000, "N/mm²"),
        "density": Quantity.of(7850, "kg/m³"),
        "poisson_ratio": 0.3,
    }
    fields.update(overrides)
    return build_material(uuid4(), 1, **fields)


def test_steel_values_of_the_standard_are_accepted_without_coherence_check() -> None:
    material = steel()
    assert material.elastic_modulus == 210e9
    assert material.shear_modulus == 81e9
    assert material.density == 7850.0
    assert material.poisson_ratio == 0.3
    # G ≠ E/2(1+ν) en un 0,29 %: no se rechaza (D5).
    assert not math.isclose(material.shear_modulus, 210e9 / 2.6, rel_tol=1e-3)
    assert material.ref.entity_id == material.id
    assert material.ref.revision == 1


def test_timber_without_poisson_and_dimensionless_quantity() -> None:
    timber = steel(
        family=MaterialFamily.TIMBER,
        elastic_modulus=Quantity.of(11, "GPa"),
        shear_modulus=Quantity.of(690, "MPa"),
        density=Quantity.of(420, "kg/m³"),
        poisson_ratio=None,
    )
    assert timber.poisson_ratio is None
    one = Quantity.of(1, "1")
    assert steel(poisson_ratio=one * 0.25).poisson_ratio == 0.25


def test_families_have_no_composite() -> None:  # T-MAT-001 (no hay multimaterial)
    assert {family.value for family in MaterialFamily} == {"STEEL", "ALUMINIUM", "TIMBER"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"elastic_modulus": Quantity.of(0, "Pa")},
        {"shear_modulus": Quantity.of(-1, "MPa")},
        {"density": Quantity.of(0, "kg/m³")},
        {"poisson_ratio": 0.5},
        {"poisson_ratio": -1.0},
        {"poisson_ratio": 0.7},
    ],
)
def test_out_of_range_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(InputValueError) as info:
        steel(**overrides)
    assert info.value.code is DiagnosticCode.INPUT_OUT_OF_RANGE
    assert info.value.diagnostic.location is not None
    assert info.value.diagnostic.location.field is not None


@pytest.mark.parametrize(
    ("overrides", "error", "code"),
    [
        ({"elastic_modulus": Quantity.of(7850, "kg/m³")}, InputValueError, "UNITS_INCOMPATIBLE"),
        ({"density": Quantity.of(1, "MPa")}, InputValueError, "UNITS_INCOMPATIBLE"),
        ({"poisson_ratio": Quantity.of(1, "m")}, InputValueError, "UNITS_INCOMPATIBLE"),
        ({"elastic_modulus": 210e9}, InputTypeError, "INPUT_INVALID_TYPE"),
        ({"poisson_ratio": True}, InputTypeError, "INPUT_INVALID_TYPE"),
        ({"poisson_ratio": math.nan}, InputValueError, "INPUT_NONFINITE"),
        ({"name": "  "}, InputTypeError, "INPUT_INVALID_TYPE"),
        ({"source": ""}, InputTypeError, "INPUT_INVALID_TYPE"),
        ({"standard_edition": ""}, InputTypeError, "INPUT_INVALID_TYPE"),
        ({"family": "STEEL"}, InputTypeError, "INPUT_INVALID_TYPE"),
    ],
)
def test_invalid_inputs(overrides: dict[str, Any], error: type[Exception], code: str) -> None:
    with pytest.raises(error) as info:
        steel(**overrides)
    assert info.value.code == DiagnosticCode(code)  # type: ignore[attr-defined]


def test_revision_is_immutable_and_identity_checked() -> None:
    material = steel()
    with pytest.raises(dataclasses.FrozenInstanceError):
        material.density = 1.0  # type: ignore[misc]
    with pytest.raises(TypeError, match="UUID v4"):
        dataclasses.replace(material, id=uuid1())
    with pytest.raises(ValueError, match=">= 1"):
        dataclasses.replace(material, revision=0)
    with pytest.raises(TypeError, match="entero"):
        dataclasses.replace(material, revision=True)


def test_stored_values_are_validated_too() -> None:
    material = steel()
    with pytest.raises(InputValueError) as info:
        dataclasses.replace(material, density=-1.0)
    assert info.value.code is DiagnosticCode.INPUT_OUT_OF_RANGE
