"""Unión de extremo I/J: estados RIGID/RELEASED/SPRING y rigidez (plan T-010, D1)."""

import dataclasses
import math
from typing import Any

import pytest

from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.units import Quantity

D = DiagnosticCode
R, L, S = DofState.RIGID, DofState.RELEASED, DofState.SPRING


def knm_rad(value: float) -> Quantity:
    return Quantity.of(value, "kN·m/rad")


def test_default_rigid_has_no_stiffness() -> None:  # CON-001, T-CON-001
    end = EndConnection.rigid()
    assert end.states == (R,) * 6
    assert end.stiffness == (None,) * 6
    assert end.is_rigid
    assert all(end.stiffness_of(dof) is None for dof in Dof)


def test_dof_order_kind_and_dimension() -> None:
    assert [str(dof) for dof in Dof] == ["Ux", "Uy", "Uz", "Rx", "Ry", "Rz"]
    assert [dof.position for dof in Dof] == list(range(6))
    assert [dof.is_translation for dof in Dof] == [True] * 3 + [False] * 3
    assert Quantity.of(1, "kN/m").require(Dof.UX.stiffness_dimension)
    assert Quantity.of(1, "kN·m/rad").require(Dof.RY.stiffness_dimension)


def test_member_end_names() -> None:
    assert [str(end) for end in MemberEnd] == ["I", "J"]


def test_pinned_preset_releases_ry_rz_only() -> None:  # propuesta §6.2
    end = EndConnection.pinned()
    assert end.states == (R, R, R, R, L, L)
    assert end.stiffness == (None,) * 6
    assert not end.is_rigid


def test_semi_rigid_preset_converts_exactly() -> None:
    end = EndConnection.semi_rigid(Dof.RY, knm_rad(5000))
    assert end.states == (R, R, R, R, S, R)
    assert end.stiffness[Dof.RY.position] == 5.0e6
    shown = end.stiffness_of(Dof.RY)
    assert shown is not None
    assert shown.to("kN·m/rad") == pytest.approx(5000)


@pytest.mark.parametrize("dof", [Dof.UX, Dof.UY, Dof.UZ])
def test_semi_rigid_rejects_translation(dof: Dof) -> None:
    with pytest.raises(InputValueError) as info:
        EndConnection.semi_rigid(dof, Quantity.of(1, "kN/m"))
    assert info.value.diagnostic.message_key == "connection.semi_rigid_dof"


def test_translational_spring_in_n_per_m() -> None:
    end = EndConnection.rigid().with_dof(Dof.UX, S, Quantity.of(2e5, "kN/m"))
    assert end.stiffness[0] == 2e8
    assert end.state(Dof.UX) is S


@pytest.mark.parametrize(
    ("dof", "stiffness"),
    [(Dof.RY, Quantity.of(1, "kN/m")), (Dof.UX, knm_rad(1)), (Dof.RZ, Quantity.of(1, "kN·m"))],
)
def test_spring_dimension_must_match_dof(dof: Dof, stiffness: Quantity) -> None:  # CON-004
    with pytest.raises(InputValueError) as info:
        EndConnection.rigid().with_dof(dof, S, stiffness)
    assert info.value.code is D.UNITS_INCOMPATIBLE


@pytest.mark.parametrize("value", [0.0, -1.0, 1e-320])
def test_spring_nonpositive_or_subnormal_rejected(value: float) -> None:
    with pytest.raises(InputValueError) as info:
        EndConnection.rigid().with_dof(Dof.RY, S, Quantity(value, Dof.RY.stiffness_dimension))
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    assert info.value.diagnostic.message_key == "connection.spring_nonpositive"


@pytest.mark.parametrize("value", [math.inf, math.nan])
def test_spring_nonfinite_rejected(value: float) -> None:
    states = (R, R, R, R, S, R)
    with pytest.raises(InputValueError):
        EndConnection(states, (None, None, None, None, value, None))


@pytest.mark.parametrize(
    ("state", "stiffness"),
    [(S, None), (R, knm_rad(1)), (L, knm_rad(1)), (S, 5.0)],
)
def test_with_dof_stiffness_only_for_spring(state: DofState, stiffness: Any) -> None:
    with pytest.raises(InputTypeError):
        EndConnection.rigid().with_dof(Dof.RY, state, stiffness)


@pytest.mark.parametrize(
    "stiffness",
    [(None, None, None, None, 1.0, None), (1.0, None, None, None, None, None)],
)
def test_constructor_rejects_stiffness_without_spring(stiffness: Any) -> None:
    with pytest.raises(InputTypeError):
        EndConnection((R, R, R, R, L, R), stiffness)


def test_constructor_rejects_wrong_shapes() -> None:
    with pytest.raises(TypeError):
        EndConnection((R,) * 6, (None,) * 5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        EndConnection((R,) * 6, [None] * 6)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        EndConnection((R,) * 5)  # type: ignore[arg-type]


def test_with_dof_rejects_wrong_types() -> None:
    with pytest.raises(InputTypeError):
        EndConnection.rigid().with_dof("Ry", L)  # type: ignore[arg-type]
    with pytest.raises(InputTypeError):
        EndConnection.rigid().with_dof(Dof.RY, "RELEASED")  # type: ignore[arg-type]


def test_with_dof_returns_new_value_and_keeps_others() -> None:
    base = EndConnection.semi_rigid(Dof.RZ, knm_rad(10))
    changed = base.with_dof(Dof.RY, L)
    assert base.state(Dof.RY) is R
    assert changed.states == (R, R, R, R, L, S)
    assert changed.stiffness[Dof.RZ.position] == 1.0e4
    assert changed.with_dof(Dof.RY, R).with_dof(Dof.RZ, R) == EndConnection.rigid()


def test_end_connection_is_immutable_and_hashable() -> None:
    end = EndConnection.pinned()
    with pytest.raises(dataclasses.FrozenInstanceError):
        end.states = (R,) * 6  # type: ignore[misc]
    assert hash(end) == hash(EndConnection.pinned())
