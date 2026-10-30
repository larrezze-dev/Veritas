"""Entidad SupportSettlement: identidad, vectores y validaciones (plan T-016, D1)."""

import sys
from uuid import UUID, uuid1, uuid4

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.loads import SupportSettlement

D = DiagnosticCode
ZERO = Vec3(0.0, 0.0, 0.0)
DOWN = Vec3(0.0, 0.0, -0.005)
ONE_X = Vec3(1.0, 0.0, 0.0)
SUBNORMAL = sys.float_info.min / 4


def settlement(translation: Vec3 = DOWN, rotation: Vec3 = ZERO) -> SupportSettlement:
    return SupportSettlement(uuid4(), 1, uuid4(), uuid4(), translation, rotation)


def test_fields_are_stored() -> None:
    case_id, support_id = uuid4(), uuid4()
    load = SupportSettlement(
        uuid4(), 1, case_id, support_id, Vec3(0.0, 0.0, -0.005), Vec3(0.0, 0.002, 0.0)
    )
    assert (load.case_id, load.support_id) == (case_id, support_id)
    assert load.translation == Vec3(0.0, 0.0, -0.005)
    assert load.rotation == Vec3(0.0, 0.002, 0.0)


def test_only_rotation_is_accepted() -> None:
    assert settlement(ZERO, Vec3(0.0, 1e-3, 0.0)).rotation.y == 1e-3


def test_all_zero_is_rejected() -> None:
    with pytest.raises(InputValueError) as info:
        settlement(ZERO, ZERO)
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    assert info.value.diagnostic.message_key == "load.all_zero"


def test_subnormal_translation_is_rejected() -> None:
    with pytest.raises(InputValueError) as info:
        settlement(Vec3(0.0, SUBNORMAL, 0.0), ONE_X)
    assert info.value.code is D.UNITS_UNREPRESENTABLE


def test_subnormal_rotation_is_rejected() -> None:
    with pytest.raises(InputValueError) as info:
        settlement(ONE_X, Vec3(0.0, SUBNORMAL, 0.0))
    assert info.value.code is D.UNITS_UNREPRESENTABLE


@pytest.mark.parametrize("revision", [0, True, "1"])
def test_revision_must_be_valid(revision: object) -> None:
    with pytest.raises((InputTypeError, InputValueError)):
        SupportSettlement(uuid4(), revision, uuid4(), uuid4(), ONE_X, ZERO)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_id", [uuid1(), "x", None])
def test_id_must_be_uuid_v4(bad_id: object) -> None:
    with pytest.raises(InputTypeError):
        SupportSettlement(bad_id, 1, uuid4(), uuid4(), ONE_X, ZERO)  # type: ignore[arg-type]


def test_case_reference_must_be_uuid() -> None:
    with pytest.raises(InputTypeError):
        SupportSettlement(uuid4(), 1, "no-uuid", uuid4(), ONE_X, ZERO)  # type: ignore[arg-type]


def test_support_reference_must_be_uuid() -> None:
    with pytest.raises(InputTypeError):
        SupportSettlement(uuid4(), 1, uuid4(), "no-uuid", ONE_X, ZERO)  # type: ignore[arg-type]


def test_translation_must_be_vec3() -> None:
    with pytest.raises(InputTypeError):
        SupportSettlement(uuid4(), 1, uuid4(), uuid4(), (0.0, 0.0, 1.0), ZERO)  # type: ignore[arg-type]


def test_rotation_must_be_vec3() -> None:
    with pytest.raises(InputTypeError):
        SupportSettlement(uuid4(), 1, uuid4(), uuid4(), ONE_X, (0.0, 0.0, 1.0))  # type: ignore[arg-type]


def test_is_frozen() -> None:
    load = settlement()
    with pytest.raises(AttributeError):
        load.revision = 2  # type: ignore[misc]
    assert isinstance(load.id, UUID)
