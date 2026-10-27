"""Apoyo de nudo por GDL: estados, rigidez, presets y base inclinada (plan T-005, R1-R4)."""

import dataclasses
import math
from typing import Any
from uuid import uuid1, uuid4

import pytest

from veritas.core.connections.end import Dof
from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.geometry.vector import Vec3
from veritas.core.supports.support import (
    FIXED,
    FREE,
    BasisKind,
    DofRestraint,
    RestraintKind,
    Support,
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
    roller_restraints,
)
from veritas.core.units import Quantity

D = DiagnosticCode


def spring(value: float) -> DofRestraint:
    return DofRestraint(RestraintKind.SPRING, value)


def make_support(**overrides: Any) -> Support:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "revision": 1,
        "label": "A",
        "node_id": uuid4(),
        "basis": SupportBasis.GLOBAL,
        "restraints": pinned_restraints(),
    }
    fields.update(overrides)
    return Support(**fields)


# --- DofRestraint -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "stiffness", "code"),
    [
        (RestraintKind.FREE, 1.0, D.INPUT_INVALID_TYPE),
        (RestraintKind.FIXED, 1.0, D.INPUT_INVALID_TYPE),
        (RestraintKind.SPRING, None, D.INPUT_INVALID_TYPE),
        (RestraintKind.SPRING, True, D.INPUT_INVALID_TYPE),
        ("SPRING", 1.0, D.INPUT_INVALID_TYPE),
        (RestraintKind.SPRING, 0.0, D.INPUT_OUT_OF_RANGE),
        (RestraintKind.SPRING, -1.0, D.INPUT_OUT_OF_RANGE),
        (RestraintKind.SPRING, 1e-320, D.INPUT_OUT_OF_RANGE),  # Subnormal.
        (RestraintKind.SPRING, math.inf, D.INPUT_NONFINITE),
        (RestraintKind.SPRING, -math.inf, D.INPUT_NONFINITE),
        (RestraintKind.SPRING, math.nan, D.INPUT_NONFINITE),
    ],
)
def test_dof_restraint_rejects_invalid(kind: Any, stiffness: Any, code: DiagnosticCode) -> None:
    with pytest.raises((InputTypeError, InputValueError)) as info:
        DofRestraint(kind, stiffness)
    assert info.value.code is code


def test_dof_restraint_valid_states() -> None:
    assert FREE.stiffness is None
    assert FIXED.stiffness is None
    assert spring(5e6).stiffness == 5e6
    assert DofRestraint(RestraintKind.SPRING, 1) == spring(1.0)


def test_dof_restraint_is_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        FIXED.kind = RestraintKind.FREE  # type: ignore[misc]


def test_spring_from_quantity_converts_exactly() -> None:
    # 5000 kN/m = 5000 * 1000 N/m = 5e6 N/m (factor decimal exacto).
    restraint = DofRestraint.spring(SupportDof.UZ, Quantity.of(5000, "kN/m"))
    assert restraint.kind is RestraintKind.SPRING
    assert restraint.stiffness == 5e6
    rotational = DofRestraint.spring(SupportDof.RY, Quantity.of(2, "kN·m/rad"))
    assert rotational.stiffness == 2000.0


def test_spring_quantity_roundtrip() -> None:
    quantity = spring(5e6).quantity(SupportDof.UX)
    assert quantity is not None
    assert quantity.si_value == 5e6
    assert quantity.to("kN/m") == 5000.0
    assert FIXED.quantity(SupportDof.UX) is None
    assert FREE.quantity(SupportDof.RX) is None


@pytest.mark.parametrize(
    ("dof", "stiffness"),
    [
        (SupportDof.RY, Quantity.of(1, "kN/m")),  # Traslacional en giro.
        (SupportDof.UZ, Quantity.of(1, "kN·m/rad")),  # Rotacional en traslación.
        (SupportDof.UX, Quantity.of(1, "m")),
    ],
)
def test_spring_cross_units_incompatible(dof: SupportDof, stiffness: Quantity) -> None:
    with pytest.raises(InputValueError) as info:
        DofRestraint.spring(dof, stiffness)
    assert info.value.code is D.UNITS_INCOMPATIBLE


def test_spring_unrepresentable_quantity() -> None:
    with pytest.raises(InputValueError) as info:
        Quantity.of(1e-320, "N/m")
    assert info.value.code is D.UNITS_UNREPRESENTABLE


def test_spring_factory_rejects_wrong_argument_types() -> None:
    with pytest.raises(InputTypeError) as info:
        DofRestraint.spring(Dof.UX, Quantity.of(1, "kN/m"))  # type: ignore[arg-type]
    assert info.value.code is D.INPUT_INVALID_TYPE
    with pytest.raises(InputTypeError) as info:
        DofRestraint.spring(SupportDof.UX, 5.0)  # type: ignore[arg-type]
    assert info.value.code is D.INPUT_INVALID_TYPE


# --- SupportDof ---------------------------------------------------------------------------


def test_support_dof_order_labels_and_kind() -> None:
    assert [d.label for d in SupportDof] == ["Ux", "Uy", "Uz", "Rx", "Ry", "Rz"]
    assert [d.is_translation for d in SupportDof] == [True] * 3 + [False] * 3
    assert [d.value for d in SupportDof] == [0, 1, 2, 3, 4, 5]


def test_support_dof_is_not_local_dof() -> None:
    for support_dof, local_dof in zip(SupportDof, Dof, strict=True):
        assert support_dof != local_dof  # type: ignore[comparison-overlap]
        assert local_dof != support_dof  # type: ignore[comparison-overlap]
        assert support_dof.label == local_dof.value  # Mismo nombre visible, distinto tipo.
    assert SupportDof.UX != "Ux"  # type: ignore[comparison-overlap]


# --- Support ------------------------------------------------------------------------------


def test_support_valid_and_restraint_lookup() -> None:
    support = make_support()
    assert support.restraint(SupportDof.UX) is FIXED
    assert support.restraint(SupportDof.RZ) is FREE
    assert support.basis is SupportBasis.GLOBAL


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"id": uuid1()}, D.INPUT_INVALID_TYPE),
        ({"id": "no-uuid"}, D.INPUT_INVALID_TYPE),
        ({"revision": 0}, D.INPUT_OUT_OF_RANGE),
        ({"revision": -2}, D.INPUT_OUT_OF_RANGE),
        ({"revision": True}, D.INPUT_INVALID_TYPE),
        ({"revision": 1.0}, D.INPUT_INVALID_TYPE),
        ({"label": 3}, D.INPUT_INVALID_TYPE),
        ({"node_id": "n1"}, D.INPUT_INVALID_TYPE),
        ({"basis": "GLOBAL"}, D.INPUT_INVALID_TYPE),
        ({"basis": BasisKind.GLOBAL}, D.INPUT_INVALID_TYPE),
        ({"restraints": [FIXED] * 6}, D.INPUT_INVALID_TYPE),
        ({"restraints": (FIXED,) * 5}, D.INPUT_INVALID_TYPE),
        ({"restraints": (FIXED,) * 7}, D.INPUT_INVALID_TYPE),
        ({"restraints": (FIXED, FIXED, FIXED, True, FREE, FREE)}, D.INPUT_INVALID_TYPE),
        ({"restraints": ("FIXED",) * 6}, D.INPUT_INVALID_TYPE),
        ({"restraints": tuple(Dof)}, D.INPUT_INVALID_TYPE),
        ({"restraints": (FREE,) * 6}, D.INPUT_OUT_OF_RANGE),
    ],
)
def test_support_rejects_invalid(overrides: dict[str, Any], code: DiagnosticCode) -> None:
    with pytest.raises((InputTypeError, InputValueError)) as info:
        make_support(**overrides)
    assert info.value.code is code


def test_support_invalid_states_message_key() -> None:
    with pytest.raises(InputTypeError) as info:
        make_support(restraints=(FIXED,) * 5)
    assert info.value.diagnostic.message_key == "support.invalid_states"
    with pytest.raises(InputValueError) as info2:
        make_support(restraints=(FREE,) * 6)
    assert info2.value.diagnostic.message_key == "support.no_restraint"


def test_support_single_spring_with_rest_free_is_valid() -> None:
    restraints = (FREE, FREE, spring(1e6), FREE, FREE, FREE)
    support = make_support(restraints=restraints)
    assert support.restraint(SupportDof.UZ).stiffness == 1e6


def test_support_restraint_rejects_local_dof() -> None:
    support = make_support()
    with pytest.raises(InputTypeError) as info:
        support.restraint(Dof.UX)  # type: ignore[arg-type]
    assert info.value.code is D.INPUT_INVALID_TYPE


def test_support_is_immutable() -> None:
    support = make_support()
    with pytest.raises(dataclasses.FrozenInstanceError):
        support.label = "otro"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        support.restraints = fixed_restraints()  # type: ignore[misc]


# --- Presets y build_restraints -----------------------------------------------------------


def test_preset_fixed_and_pinned_exact() -> None:
    assert fixed_restraints() == (FIXED,) * 6
    assert pinned_restraints() == (FIXED, FIXED, FIXED, FREE, FREE, FREE)


@pytest.mark.parametrize(
    ("axis", "fixed_index"),
    [(None, 2), (SupportDof.UX, 0), (SupportDof.UY, 1), (SupportDof.UZ, 2)],
)
def test_roller_presets(axis: SupportDof | None, fixed_index: int) -> None:
    restraints = roller_restraints() if axis is None else roller_restraints(axis)
    expected = tuple(FIXED if i == fixed_index else FREE for i in range(6))
    assert restraints == expected
    assert sum(item.kind is RestraintKind.FIXED for item in restraints) == 1


@pytest.mark.parametrize("axis", [SupportDof.RX, SupportDof.RY, SupportDof.RZ])
def test_roller_rejects_rotation_axis(axis: SupportDof) -> None:
    with pytest.raises(InputValueError) as info:
        roller_restraints(axis)
    assert info.value.code is D.INPUT_OUT_OF_RANGE


def test_roller_rejects_non_dof_axis() -> None:
    with pytest.raises(InputTypeError) as info:
        roller_restraints(Dof.UZ)  # type: ignore[arg-type]
    assert info.value.code is D.INPUT_INVALID_TYPE


def test_presets_equal_explicit_build() -> None:
    every = list(SupportDof)
    assert fixed_restraints() == build_restraints(every)
    assert pinned_restraints() == build_restraints(every[:3])
    assert roller_restraints() == build_restraints([SupportDof.UZ])
    assert roller_restraints(SupportDof.UX) == build_restraints([SupportDof.UX])


def test_preset_support_equals_explicit_up_to_identity() -> None:
    node = uuid4()
    ident = uuid4()
    a = Support(ident, 1, "", node, SupportBasis.GLOBAL, pinned_restraints())
    b = Support(ident, 1, "", node, SupportBasis.GLOBAL, build_restraints(list(SupportDof)[:3]))
    assert a == b  # No se guarda el nombre del preset.


def test_build_restraints_with_springs() -> None:
    restraints = build_restraints(
        [SupportDof.UX],
        {SupportDof.UZ: Quantity.of(5000, "kN/m"), SupportDof.RY: Quantity.of(1, "N·m/rad")},
    )
    assert restraints == (FIXED, FREE, spring(5e6), FREE, spring(1.0), FREE)


@pytest.mark.parametrize(
    ("fixed", "springs"),
    [
        ([SupportDof.UX, SupportDof.UX], None),
        ([SupportDof.UZ], {SupportDof.UZ: Quantity.of(1, "kN/m")}),
    ],
)
def test_build_restraints_rejects_repeated_dof(
    fixed: list[SupportDof], springs: dict[SupportDof, Quantity] | None
) -> None:
    with pytest.raises(InputValueError) as info:
        build_restraints(fixed, springs)
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    assert info.value.diagnostic.message_key == "support.duplicate_dof"


def test_build_restraints_rejects_empty_and_bad_types() -> None:
    with pytest.raises(InputValueError) as info:
        build_restraints([])
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    with pytest.raises(InputTypeError) as info2:
        build_restraints([Dof.UX])  # type: ignore[list-item]
    assert info2.value.code is D.INPUT_INVALID_TYPE


# --- SupportBasis -------------------------------------------------------------------------

SQRT_HALF = 1.0 / math.sqrt(2.0)


def assert_close(actual: tuple[float, ...], expected: tuple[float, ...], tol: float) -> None:
    for a, e in zip(actual, expected, strict=True):
        assert abs(a - e) <= tol, (actual, expected)


def test_global_basis_is_identity() -> None:
    assert SupportBasis.GLOBAL.kind is BasisKind.GLOBAL
    assert SupportBasis.GLOBAL.rows is None
    assert SupportBasis.GLOBAL.matrix() == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def test_inclined_hand_case_45_degrees() -> None:
    # x_dir=(1,0,1), z_ref=(0,0,1): ex'=(1,0,1)/sqrt2; z_ref - (z_ref.ex')ex' = (0,0,1)-(1/2)(1,0,1)
    # = (-1/2,0,1/2) -> ez'=(-1,0,1)/sqrt2; ey'=ez' x ex' = (0,1,0).
    ex, ey, ez = SupportBasis.inclined((1, 0, 1), (0, 0, 1)).matrix()
    assert_close(ex, (SQRT_HALF, 0.0, SQRT_HALF), 1e-15)
    assert_close(ey, (0.0, 1.0, 0.0), 1e-15)
    assert_close(ez, (-SQRT_HALF, 0.0, SQRT_HALF), 1e-15)


def test_inclined_hand_case_30_degrees() -> None:
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    ex, ey, ez = SupportBasis.inclined((c, 0.0, s), (0.0, 0.0, 1.0)).matrix()
    assert_close(ex, (c, 0.0, s), 1e-15)
    assert_close(ey, (0.0, 1.0, 0.0), 1e-15)
    assert_close(ez, (-s, 0.0, c), 1e-15)


@pytest.mark.parametrize(
    ("x_dir", "z_ref"),
    [
        ((1, 0, 0), (0, 0, 1)),
        ((1, 2, 3), (0, 0, 1)),
        ((0, 1, 0), (1, 1, 1)),
        ((-3, 0.5, 2), (1, -1, 4)),
        ((1e-3, 0, 0), (0, 5e4, 1e4)),
        ((1, 1, 0), (1, 1, 1)),
    ],
)
def test_inclined_orthonormal_and_right_handed(
    x_dir: tuple[float, ...], z_ref: tuple[float, ...]
) -> None:
    q = SupportBasis.inclined(x_dir, z_ref).matrix()
    for i in range(3):
        for j in range(3):
            dot = sum(q[i][k] * q[j][k] for k in range(3))
            assert abs(dot - (1.0 if i == j else 0.0)) <= 1e-12
    det = (
        q[0][0] * (q[1][1] * q[2][2] - q[1][2] * q[2][1])
        - q[0][1] * (q[1][0] * q[2][2] - q[1][2] * q[2][0])
        + q[0][2] * (q[1][0] * q[2][1] - q[1][1] * q[2][0])
    )
    assert det > 0.0
    assert abs(det - 1.0) <= 1e-12
    norm = math.sqrt(sum(c * c for c in x_dir))  # ex' tiene la dirección de x_dir.
    assert_close(q[0], tuple(c / norm for c in x_dir), 1e-15)


@pytest.mark.parametrize(("t", "accepted"), [(0.5e-8, False), (2e-8, True)])
def test_inclined_reference_parallel_threshold(t: float, accepted: bool) -> None:
    # z_ref = ex'*1 + n*t con n ortogonal a ex': la proyección normal es ~t (|z_ref| ~ 1).
    z_ref = (1.0, 0.0, t)
    if accepted:
        basis = SupportBasis.inclined((1.0, 0.0, 0.0), z_ref)
        assert_close(basis.matrix()[2], (0.0, 0.0, 1.0), 1e-12)
    else:
        with pytest.raises(InputValueError) as info:
            SupportBasis.inclined((1.0, 0.0, 0.0), z_ref)
        assert info.value.code is D.GEOMETRY_INVALID_ORIENTATION


@pytest.mark.parametrize("z_ref", [(1, 0, 0), (-2, 0, 0), (0, 0, 0)])
def test_inclined_rejects_parallel_or_zero_reference(z_ref: tuple[float, ...]) -> None:
    with pytest.raises(InputValueError) as info:
        SupportBasis.inclined((1, 0, 0), z_ref)
    assert info.value.code is D.GEOMETRY_INVALID_ORIENTATION


def test_inclined_rejects_zero_x_dir() -> None:
    with pytest.raises(InputValueError) as info:
        SupportBasis.inclined((0, 0, 0), (0, 0, 1))
    assert info.value.code is D.GEOMETRY_INVALID_ORIENTATION


@pytest.mark.parametrize(
    ("x_dir", "z_ref"),
    [
        ((math.nan, 0, 0), (0, 0, 1)),
        ((1, 0, 0), (0, math.nan, 1)),
        ((1, math.inf, 0), (0, 0, 1)),
    ],
)
def test_inclined_rejects_nonfinite(x_dir: tuple[float, ...], z_ref: tuple[float, ...]) -> None:
    with pytest.raises(InputValueError) as info:
        SupportBasis.inclined(x_dir, z_ref)
    assert info.value.code is D.INPUT_NONFINITE


def test_inclined_rejects_wrong_shape() -> None:
    with pytest.raises(InputTypeError) as info:
        SupportBasis.inclined((1, 0), (0, 0, 1))
    assert info.value.code is D.INPUT_INVALID_TYPE


def test_registered_rows_must_be_orthonormal() -> None:
    x, y, z = Vec3(1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, 0.0, 1.0)
    bad_sets = [
        (x, x, z),  # Filas repetidas.
        (x, y, Vec3(0.0, 0.0, 2.0)),  # No unitaria.
        (x, y, -z),  # Levógira: det < 0.
        (x, Vec3(0.6, 0.8, 0.0), z),  # No ortogonales.
        (x, y, Vec3(0.0, 1e-9, 1.0)),  # Desvío ~1e-9 > 1e-12.
    ]
    for rows in bad_sets:
        with pytest.raises(InputValueError) as info:
            SupportBasis(BasisKind.INCLINED, rows)
        assert info.value.code is D.GEOMETRY_INVALID_ORIENTATION


def test_registered_rows_accept_valid_triple() -> None:
    x, y, z = Vec3(1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, 0.0, 1.0)
    assert SupportBasis(BasisKind.INCLINED, (x, y, z)).kind is BasisKind.INCLINED


def test_basis_type_errors() -> None:
    x, y, z = Vec3(1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, 0.0, 1.0)
    cases: list[tuple[Any, Any]] = [
        (BasisKind.GLOBAL, (x, y, z)),  # GLOBAL con filas.
        (BasisKind.INCLINED, None),
        (BasisKind.INCLINED, [x, y, z]),
        (BasisKind.INCLINED, (x, y)),
        (BasisKind.INCLINED, (x, y, (0.0, 0.0, 1.0))),
        ("INCLINED", (x, y, z)),
    ]
    for kind, rows in cases:
        with pytest.raises(InputTypeError) as info:
            SupportBasis(kind, rows)
        assert info.value.code is D.INPUT_INVALID_TYPE


def test_inclined_identity_differs_from_global() -> None:
    x, y, z = Vec3(1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, 0.0, 1.0)
    inclined_identity = SupportBasis(BasisKind.INCLINED, (x, y, z))
    assert inclined_identity.matrix() == SupportBasis.GLOBAL.matrix()
    assert inclined_identity != SupportBasis.GLOBAL
    assert SupportBasis.inclined((1, 0, 0), (0, 0, 1)) != SupportBasis.GLOBAL
    left = make_support(basis=inclined_identity)
    right = dataclasses.replace(left, basis=SupportBasis.GLOBAL)
    assert left != right
