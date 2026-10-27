"""Entidades de carga: LoadCase, NodalLoad, MemberUniformLoad y SelfWeight (plan T-006)."""

import sys
from uuid import UUID, uuid1, uuid4

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.load_case import LoadCase, LoadCategory
from veritas.core.loads.loads import (
    STANDARD_GRAVITY,
    LoadBasis,
    MemberUniformLoad,
    NodalLoad,
    SelfWeight,
    self_weight_intensity,
)
from veritas.core.units import ACCELERATION, LINEAR_MASS, Quantity

D = DiagnosticCode
ZERO = Vec3(0.0, 0.0, 0.0)
SUBNORMAL = sys.float_info.min / 4


DOWN = Vec3(0.0, 0.0, -1.0)


def nodal(force: Vec3 = DOWN, moment: Vec3 = ZERO) -> NodalLoad:
    return NodalLoad(uuid4(), 1, uuid4(), uuid4(), force, moment)


# --- LoadCase ---------------------------------------------------------------------------


def test_load_case_fields_and_default_description() -> None:
    case = LoadCase(uuid4(), 1, "G1", LoadCategory.PERMANENT)
    assert case.description == ""
    assert [c.value for c in LoadCategory] == ["PERMANENT", "VARIABLE", "ACCIDENTAL"]


@pytest.mark.parametrize("name", ["", "   ", None, 3])
def test_load_case_name_must_be_text(name: object) -> None:
    with pytest.raises(InputTypeError) as info:
        LoadCase(uuid4(), 1, name, LoadCategory.VARIABLE)  # type: ignore[arg-type]
    assert info.value.code is D.INPUT_INVALID_TYPE


@pytest.mark.parametrize("category", ["PERMANENT", None, 1])
def test_load_case_category_must_be_enum(category: object) -> None:
    with pytest.raises(InputTypeError):
        LoadCase(uuid4(), 1, "G1", category)  # type: ignore[arg-type]


def test_load_case_description_must_be_text() -> None:
    with pytest.raises(InputTypeError):
        LoadCase(uuid4(), 1, "G1", LoadCategory.PERMANENT, None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("entity_id", "revision", "error"),
    [
        (uuid1(), 1, InputTypeError),
        ("x", 1, InputTypeError),
        (uuid4(), True, InputTypeError),
        (uuid4(), 1.0, InputTypeError),
        (uuid4(), 0, InputValueError),
    ],
)
def test_load_case_identity(entity_id: object, revision: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        LoadCase(entity_id, revision, "G1", LoadCategory.PERMANENT)  # type: ignore[arg-type]


# --- NodalLoad / MemberUniformLoad ------------------------------------------------------


def test_nodal_load_all_zero_is_rejected_including_negative_zero() -> None:
    with pytest.raises(InputValueError) as info:
        nodal(Vec3(0.0, -0.0, 0.0), Vec3(-0.0, 0.0, 0.0))
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    assert info.value.diagnostic.message_key == "load.all_zero"


def test_nodal_load_with_only_a_moment_is_valid() -> None:
    load = nodal(ZERO, Vec3(0.0, 5.0, 0.0))
    assert load.moment.y == 5.0


def test_negative_zero_is_kept_as_given() -> None:
    load = nodal(Vec3(-0.0, 0.0, -1.0))
    assert str(load.force.x) == "-0.0"


@pytest.mark.parametrize("field", ["force", "moment"])
def test_subnormal_component_is_unrepresentable(field: str) -> None:
    vectors = {"force": Vec3(0.0, 0.0, -1.0), "moment": ZERO}
    vectors[field] = Vec3(SUBNORMAL, 0.0, 0.0)
    with pytest.raises(InputValueError) as info:
        nodal(vectors["force"], vectors["moment"])
    assert info.value.code is D.UNITS_UNREPRESENTABLE


def test_nodal_load_types() -> None:
    with pytest.raises(InputTypeError):
        NodalLoad(uuid4(), 1, uuid4(), uuid4(), (0, 0, 1), ZERO)  # type: ignore[arg-type]
    with pytest.raises(InputTypeError):
        NodalLoad(uuid4(), 1, "case", uuid4(), Vec3(0, 0, 1), ZERO)  # type: ignore[arg-type]


def test_member_uniform_load_checks() -> None:
    load = MemberUniformLoad(uuid4(), 1, uuid4(), uuid4(), LoadBasis.LOCAL, Vec3(0, 0, -2))
    assert load.basis is LoadBasis.LOCAL
    with pytest.raises(InputValueError) as info:
        MemberUniformLoad(uuid4(), 1, uuid4(), uuid4(), LoadBasis.GLOBAL, ZERO)
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    with pytest.raises(InputTypeError):
        MemberUniformLoad(uuid4(), 1, uuid4(), uuid4(), "LOCAL", Vec3(0, 0, 1))  # type: ignore[arg-type]


def test_loads_are_immutable() -> None:
    load = nodal()
    with pytest.raises(AttributeError):
        load.force = ZERO  # type: ignore[misc]


# --- SelfWeight y gravedad --------------------------------------------------------------


def test_standard_gravity_is_the_conventional_value() -> None:
    assert STANDARD_GRAVITY.si_value == 9.80665
    assert STANDARD_GRAVITY.dimension == ACCELERATION


@pytest.mark.parametrize("gravity", [0.0, -9.81])
def test_self_weight_gravity_must_be_positive(gravity: float) -> None:
    with pytest.raises(InputValueError) as info:
        SelfWeight(uuid4(), 1, uuid4(), gravity)
    assert info.value.code is D.INPUT_OUT_OF_RANGE


def test_self_weight_gravity_type() -> None:
    with pytest.raises(InputTypeError):
        SelfWeight(uuid4(), 1, uuid4(), True)


def test_self_weight_intensity_is_rho_a_g_magnitude() -> None:
    # ρ·A = 1000 kg/m³ · 0,08 m² = 80 kg/m; g = 10 m/s² -> 800 N/m.
    q = self_weight_intensity(Quantity(80.0, LINEAR_MASS), Quantity.of(10, "m/s²"))
    assert q.to("N/m") == pytest.approx(800.0, rel=1e-12)


def test_self_weight_intensity_rejects_wrong_dimension_and_nonpositive_g() -> None:
    with pytest.raises(InputValueError) as info:
        self_weight_intensity(Quantity(80.0, LINEAR_MASS), Quantity.of(10, "m"))
    assert info.value.code is D.UNITS_INCOMPATIBLE
    with pytest.raises(InputValueError) as info:
        self_weight_intensity(Quantity(80.0, LINEAR_MASS), Quantity(-1.0, ACCELERATION))
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    with pytest.raises(InputTypeError):
        self_weight_intensity(80.0, STANDARD_GRAVITY)  # type: ignore[arg-type]


def test_self_weight_intensity_overflow_is_unrepresentable() -> None:
    with pytest.raises(InputValueError) as info:
        self_weight_intensity(Quantity(1e300, LINEAR_MASS), Quantity(1e300, ACCELERATION))
    assert info.value.code is D.UNITS_UNREPRESENTABLE


def test_entity_ids_are_uuid4() -> None:
    assert isinstance(nodal().id, UUID)
