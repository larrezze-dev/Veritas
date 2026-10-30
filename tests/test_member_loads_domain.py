"""Cargas de barra ampliadas en el dominio (plan T-015, E1: D1-D5 y D10).

Puntual (fuerza y par en 0 < ξ < 1), fuerza distribuida y momento distribuido lineales entre
ξa y ξb, base GLOBAL o LOCAL. Las resultantes de referencia se derivan a mano (cuerpo libre)
o se integran en fracciones exactas con la regla de Simpson, exacta para el integrando
r(t) × q(t), que es cuadrático en t; no se toman de la salida del código.
"""

import math
from collections.abc import Callable
from fractions import Fraction
from uuid import UUID, uuid4

import pytest

from fem_support import Frame
from veritas import cli
from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.geometry.entities import Member
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.load_case import LoadCategory
from veritas.core.loads.loads import (
    MIN_SEGMENT,
    LoadBasis,
    MemberDistributedLoad,
    MemberDistributedMoment,
    MemberPointLoad,
    linear_contribution,
    linear_moment_contribution,
)
from veritas.core.materials.material import MaterialFamily
from veritas.core.presentation import PROFILE_KN_M
from veritas.core.supports.support import fixed_restraints
from veritas.core.units import Quantity
from veritas.fem.adapters.planar_xz import translate_planar_xz
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.translator import translate_spatial

D = DiagnosticCode
TOL = 1e-9
ZERO = Vec3(0.0, 0.0, 0.0)


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def kn(value: float) -> Quantity:
    return Quantity.of(value, "kN")


def knm(value: float) -> Quantity:
    return Quantity.of(value, "kN·m")


def knpm(value: float) -> Quantity:
    return Quantity.of(value, "kN/m")


def model_with_member(
    end: tuple[float, float, float], start: tuple[float, float, float] = (0.0, 0.0, 0.0)
) -> tuple[StructuralModel, Member, UUID]:
    model = StructuralModel()
    material = model.add_material(
        name="Sintético",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(210, "GPa"),
        shear_modulus=Quantity.of(81, "GPa"),
        density=Quantity.of(1000, "kg/m³"),
    )
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4))
    a = model.add_node(m(start[0]), m(start[1]), m(start[2]))
    b = model.add_node(m(end[0]), m(end[1]), m(end[2]))
    member = model.add_member(a.id, b.id, material=material.ref, section=section.ref)
    case = model.add_load_case("Q", LoadCategory.VARIABLE)
    return model, member, case.id


def si(values: tuple[Quantity, Quantity, Quantity]) -> tuple[float, float, float]:
    return (values[0].si_value, values[1].si_value, values[2].si_value)


def assert_vec(actual: tuple[float, ...], expected: tuple[float | Fraction, ...]) -> None:
    for a, e in zip(actual, expected, strict=True):
        assert a == pytest.approx(float(e), abs=TOL)


# --- Entidades: validación (D2, D3) ---------------------------------------------------


DOWN = Vec3(0.0, 0.0, -1.0)


def point(position: object, force: Vec3 = DOWN) -> MemberPointLoad:
    return MemberPointLoad(
        uuid4(),
        1,
        uuid4(),
        uuid4(),
        LoadBasis.GLOBAL,
        position,  # type: ignore[arg-type]
        force,
        ZERO,
    )


def distributed(start: object, end: object, q_a: Vec3, q_b: Vec3) -> MemberDistributedLoad:
    return MemberDistributedLoad(
        uuid4(),
        1,
        uuid4(),
        uuid4(),
        LoadBasis.GLOBAL,
        start,  # type: ignore[arg-type]
        end,  # type: ignore[arg-type]
        q_a,
        q_b,
    )


@pytest.mark.parametrize("position", [0.0, 1.0, -0.25, 1.5])
def test_point_position_outside_open_interval_is_rejected(position: float) -> None:
    with pytest.raises(InputValueError) as info:
        point(position)
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    assert info.value.diagnostic.message_key == "load.position_out_of_range"


@pytest.mark.parametrize("position", [math.nan, math.inf])
def test_point_position_must_be_finite(position: float) -> None:
    with pytest.raises(InputValueError):
        point(position)


@pytest.mark.parametrize("position", [True, "0.5", None])
def test_point_position_must_be_real(position: object) -> None:
    with pytest.raises(InputTypeError):
        point(position)


def test_point_position_fraction_is_invalid_type() -> None:
    with pytest.raises(InputTypeError):
        point(Fraction(1, 2))  # Fraction no es int ni float


def test_point_all_zero_is_rejected() -> None:
    with pytest.raises(InputValueError) as info:
        point(0.5, ZERO)
    assert info.value.diagnostic.message_key == "load.all_zero"


def test_point_couple_alone_is_valid() -> None:
    load = MemberPointLoad(
        uuid4(), 1, uuid4(), uuid4(), LoadBasis.GLOBAL, 0.5, ZERO, Vec3(0.0, 1.0, 0.0)
    )
    assert load.moment == Vec3(0.0, 1.0, 0.0)


def test_basis_must_be_load_basis() -> None:
    with pytest.raises(InputTypeError):
        MemberPointLoad(uuid4(), 1, uuid4(), uuid4(), "GLOBAL", 0.5, Vec3(1, 0, 0), ZERO)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("start", "end", "key"),
    [
        (-0.1, 0.5, "load.position_out_of_range"),
        (0.2, 1.1, "load.position_out_of_range"),
        (0.6, 0.4, "load.segment_too_short"),
        (0.5, 0.5, "load.segment_too_short"),
        (0.5, 0.5 + MIN_SEGMENT / 2, "load.segment_too_short"),
    ],
)
def test_segment_validation(start: float, end: float, key: str) -> None:
    with pytest.raises(InputValueError) as info:
        distributed(start, end, Vec3(0, 0, -1.0), Vec3(0, 0, -1.0))
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    assert info.value.diagnostic.message_key == key


def test_full_length_and_triangular_segments_are_valid() -> None:
    full = distributed(0, 1, Vec3(0, 0, -1.0), Vec3(0, 0, -1.0))
    assert (full.start, full.end) == (0.0, 1.0)
    triangle = distributed(0.0, 0.5, ZERO, Vec3(0, 0, -1.0))
    assert triangle.intensity_start == ZERO


def test_distributed_all_zero_is_rejected() -> None:
    with pytest.raises(InputValueError) as info:
        distributed(0.0, 1.0, ZERO, ZERO)
    assert info.value.diagnostic.message_key == "load.all_zero"


def test_distributed_moment_validates_like_force() -> None:
    with pytest.raises(InputValueError):
        MemberDistributedMoment(
            uuid4(), 1, uuid4(), uuid4(), LoadBasis.GLOBAL, 0.7, 0.3, Vec3(1, 0, 0), ZERO
        )


# --- Modelo: API, unidades y model_revision (D1, D5; tabla D6 de T-006) --------------------


def test_add_methods_bump_model_revision_and_keep_order() -> None:
    model, member, case_id = model_with_member((4.0, 0.0, 0.0))
    before = model.model_revision
    p = model.add_member_point_load(case_id, member.id, position=0.25, fz=kn(-10))
    q = model.add_member_distributed_load(
        case_id, member.id, start=0.25, end=0.75, qz_start=knpm(-1), qz_end=knpm(-3)
    )
    mm = model.add_member_distributed_moment(
        case_id, member.id, start=0.0, end=1.0, mx_start=Quantity.of(0.6, "kN·m/m")
    )
    assert model.model_revision == before + 3
    assert model.loads_in_case(case_id) == (p, q, mm)
    assert p.force == Vec3(0.0, 0.0, -10000.0)
    assert q.intensity_start == Vec3(0.0, 0.0, -1000.0)
    assert q.intensity_end == Vec3(0.0, 0.0, -3000.0)
    assert mm.intensity_start == Vec3(600.0, 0.0, 0.0)
    assert mm.intensity_end == ZERO
    assert model.remove_load(q.id) is q
    assert model.model_revision == before + 4


def test_rejected_add_does_not_change_model() -> None:
    model, member, case_id = model_with_member((4.0, 0.0, 0.0))
    before = model.model_revision
    with pytest.raises(InputValueError):
        model.add_member_point_load(case_id, member.id, position=1.0, fz=kn(-1))
    with pytest.raises(InputValueError):
        model.add_member_distributed_load(case_id, member.id, start=0.3, end=0.3, qz_start=knpm(1))
    assert model.model_revision == before
    assert model.loads_in_case(case_id) == ()


def test_unknown_case_or_member_is_reference_not_found() -> None:
    model, member, case_id = model_with_member((4.0, 0.0, 0.0))
    calls: list[Callable[[], object]] = [
        lambda: model.add_member_point_load(uuid4(), member.id, position=0.5, fz=kn(1)),
        lambda: model.add_member_point_load(case_id, uuid4(), position=0.5, fz=kn(1)),
        lambda: model.add_member_distributed_load(
            case_id, uuid4(), start=0.0, end=1.0, qz_start=knpm(1)
        ),
        lambda: model.add_member_distributed_moment(
            case_id, uuid4(), start=0.0, end=1.0, my_start=kn(1)
        ),
    ]
    for call in calls:
        with pytest.raises(InputValueError) as info:
            call()
        assert info.value.code is D.REFERENCE_NOT_FOUND


@pytest.mark.parametrize(
    "call",
    [
        lambda mdl, mid, cid: mdl.add_member_point_load(cid, mid, position=0.5, fz=knpm(1)),
        lambda mdl, mid, cid: mdl.add_member_point_load(cid, mid, position=0.5, my=kn(1)),
        lambda mdl, mid, cid: mdl.add_member_distributed_load(
            cid, mid, start=0.0, end=1.0, qz_start=kn(1)
        ),
        lambda mdl, mid, cid: mdl.add_member_distributed_moment(
            cid, mid, start=0.0, end=1.0, my_start=knm(1)
        ),
    ],
)
def test_wrong_dimension_is_units_incompatible(call: object) -> None:
    model, member, case_id = model_with_member((4.0, 0.0, 0.0))
    with pytest.raises(InputValueError) as info:
        call(model, member.id, case_id)  # type: ignore[operator]
    assert info.value.code is D.UNITS_INCOMPATIBLE


def test_distributed_moment_units() -> None:
    """D5-A: N·m/m tiene la dimensión de una fuerza; «kN» también se acepta."""
    model, member, case_id = model_with_member((4.0, 0.0, 0.0))
    a = model.add_member_distributed_moment(
        case_id, member.id, start=0.0, end=1.0, my_start=Quantity.of(2, "kN·m/m")
    )
    b = model.add_member_distributed_moment(
        case_id, member.id, start=0.0, end=1.0, my_start=Quantity.of(2000, "N·mm/mm")
    )
    c = model.add_member_distributed_moment(case_id, member.id, start=0.0, end=1.0, my_start=kn(2))
    assert a.intensity_start == b.intensity_start == c.intensity_start == Vec3(0.0, 2000.0, 0.0)


def test_non_quantity_component_is_invalid_type() -> None:
    model, member, case_id = model_with_member((4.0, 0.0, 0.0))
    with pytest.raises(InputTypeError):
        model.add_member_point_load(case_id, member.id, position=0.5, fz=-10.0)  # type: ignore[arg-type]


# --- Resultante del caso (D10): valores a mano ---------------------------------------------


def test_point_load_resultant_by_hand() -> None:
    """Barra (0,0,0)-(4,0,0); Fz = −10 kN y My = 2 kN·m en ξ = 1/4, r = (1, 0, 0).

    r × F = (0, −1·(−10000), 0) = (0, 10000, 0); ΣM = (0, 10000 + 2000, 0).
    """
    model, member, case_id = model_with_member((4.0, 0.0, 0.0))
    model.add_member_point_load(case_id, member.id, position=0.25, fz=kn(-10), my=knm(2))
    resultant = model.case_resultant(case_id)
    assert_vec(si(resultant.force), (0, 0, -10000))
    assert_vec(si(resultant.moment), (0, 12000, 0))
    assert resultant.load_count == 1


def test_trapezoid_resultant_by_hand() -> None:
    """Barra (0,0,0)-(4,0,0); qz de −1 a −3 kN/m entre x = 1 y x = 3 m.

    Fz = 2·(−1000 − 3000)/2 = −4000 N. Centroide del trapecio desde x = 1:
    s·(wa + 2wb)/(3(wa + wb)) = 2·7/12 = 7/6 → x̄ = 13/6 m. ΣMy = −x̄·Fz = 26000/3 N·m.
    """
    model, member, case_id = model_with_member((4.0, 0.0, 0.0))
    model.add_member_distributed_load(
        case_id, member.id, start=0.25, end=0.75, qz_start=knpm(-1), qz_end=knpm(-3)
    )
    resultant = model.case_resultant(case_id)
    assert_vec(si(resultant.force), (0, 0, -4000))
    assert_vec(si(resultant.moment), (0, Fraction(26000, 3), 0))


def test_distributed_moment_resultant_by_hand() -> None:
    """mx de 0 a 600 N·m/m en toda la barra de 4 m: ΣF = 0, ΣMx = 4·300 = 1200 N·m."""
    model, member, case_id = model_with_member((4.0, 0.0, 0.0))
    model.add_member_distributed_moment(
        case_id, member.id, start=0.0, end=1.0, mx_end=Quantity.of(600, "N·m/m")
    )
    resultant = model.case_resultant(case_id)
    assert_vec(si(resultant.force), (0, 0, 0))
    assert_vec(si(resultant.moment), (1200, 0, 0))


def test_local_basis_follows_member_axes() -> None:
    """Barra (0,0,0)-(0,2,0): ex = Y, ey = −X, ez = Z (ADR-001 §2).

    Puntual local fy = 1 kN en ξ = 1/2: F = (−1000, 0, 0) en r = (0, 1, 0); r × F = (0, 0, 1000).
    Distribuida local qy = 1 → 3 kN/m entre ξ = 0 y 1 (y de 0 a 2): F = (−4000, 0, 0); centroide
    desde y = 0: 2·(1 + 6)/(3·4) = 7/6, r × F = (0, 0, −(7/6)·(−4000)) = (0, 0, 14000/3).
    Momento local my = 500 N·m/m en toda la barra: ey = −X → (−1000, 0, 0).
    """
    model, member, case_id = model_with_member((0.0, 2.0, 0.0))
    model.add_member_point_load(case_id, member.id, position=0.5, basis=LoadBasis.LOCAL, fy=kn(1))
    model.add_member_distributed_load(
        case_id,
        member.id,
        start=0.0,
        end=1.0,
        basis=LoadBasis.LOCAL,
        qy_start=knpm(1),
        qy_end=knpm(3),
    )
    model.add_member_distributed_moment(
        case_id,
        member.id,
        start=0.0,
        end=1.0,
        basis=LoadBasis.LOCAL,
        my_start=Quantity.of(500, "N·m/m"),
        my_end=Quantity.of(500, "N·m/m"),
    )
    resultant = model.case_resultant(case_id)
    assert_vec(si(resultant.force), (-5000, 0, 0))
    assert_vec(si(resultant.moment), (-1000, 0, 1000 + Fraction(14000, 3)))


def test_full_length_partial_equals_uniform_load() -> None:
    """R-UNI (dominio): distribuida en [0, 1] con extremos iguales = carga uniforme de T-006."""
    model, member, case_id = model_with_member((3.0, -2.0, 1.5), start=(0.5, 1.0, -0.5))
    other = model.add_load_case("U", LoadCategory.VARIABLE).id
    model.add_member_distributed_load(
        case_id,
        member.id,
        start=0.0,
        end=1.0,
        basis=LoadBasis.LOCAL,
        qy_start=knpm(1.5),
        qz_start=knpm(-2),
        qy_end=knpm(1.5),
        qz_end=knpm(-2),
    )
    model.add_member_uniform_load(
        other, member.id, basis=LoadBasis.LOCAL, qy=knpm(1.5), qz=knpm(-2)
    )
    a, b = model.case_resultant(case_id), model.case_resultant(other)
    for x, y in zip((*si(a.force), *si(a.moment)), (*si(b.force), *si(b.moment)), strict=True):
        assert x == pytest.approx(y, rel=1e-12, abs=1e-9)


# --- Fórmulas cerradas frente a integración exacta (Simpson en fracciones) ----------------

type FVec = tuple[Fraction, Fraction, Fraction]


def _f(v: tuple[float, float, float]) -> FVec:
    return (Fraction(v[0]), Fraction(v[1]), Fraction(v[2]))


def _cross(a: FVec, b: FVec) -> FVec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _lerp(a: FVec, b: FVec, t: Fraction) -> FVec:
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2]))


def _simpson(r_a: FVec, r_b: FVec, q_a: FVec, q_b: FVec, *, moment: bool) -> tuple[FVec, FVec]:
    """∫ q ds y ∫ (r × q) ds (o ∫ m ds) con Simpson, exacto para integrandos cuadráticos en t.

    s se toma en fracción a partir de la norma en coma flotante (la única cantidad irracional);
    la comparación usa tolerancia relativa 1e-12.
    """
    s = Fraction(math.dist(r_a, r_b))
    weights = (
        (Fraction(0), Fraction(1, 6)),
        (Fraction(1, 2), Fraction(4, 6)),
        (Fraction(1), Fraction(1, 6)),
    )
    force: list[Fraction] = [Fraction(0)] * 3
    torque: list[Fraction] = [Fraction(0)] * 3
    for t, w in weights:
        q = _lerp(q_a, q_b, t)
        r = _lerp(r_a, r_b, t)
        dm = q if moment else _cross(r, q)
        for k in range(3):
            if not moment:
                force[k] += w * s * q[k]
            torque[k] += w * s * dm[k]
    return (force[0], force[1], force[2]), (torque[0], torque[1], torque[2])


OBLIQUE = (
    ((0.5, 1.0, -0.5), (3.0, -2.0, 1.5), (100.0, -250.0, 400.0), (-300.0, 50.0, 125.0)),
    ((-1.0, 2.0, 3.0), (2.0, 2.0, -1.0), (0.0, 0.0, 0.0), (0.0, -1000.0, 750.0)),
    ((0.0, 0.0, 0.0), (0.0, 0.0, 5.0), (1.0, 2.0, 3.0), (1.0, 2.0, 3.0)),
)


@pytest.mark.parametrize(("r_a", "r_b", "q_a", "q_b"), OBLIQUE)
def test_linear_contribution_matches_exact_integration(
    r_a: tuple[float, float, float],
    r_b: tuple[float, float, float],
    q_a: tuple[float, float, float],
    q_b: tuple[float, float, float],
) -> None:
    force, moment = linear_contribution(Vec3(*r_a), Vec3(*r_b), Vec3(*q_a), Vec3(*q_b))
    exact_f, exact_m = _simpson(_f(r_a), _f(r_b), _f(q_a), _f(q_b), moment=False)
    for actual, expected in zip(
        (*force.components(), *moment.components()), (*exact_f, *exact_m), strict=True
    ):
        assert actual == pytest.approx(float(expected), rel=1e-12, abs=1e-9)


@pytest.mark.parametrize(("r_a", "r_b", "q_a", "q_b"), OBLIQUE)
def test_linear_moment_contribution_matches_exact_integration(
    r_a: tuple[float, float, float],
    r_b: tuple[float, float, float],
    q_a: tuple[float, float, float],
    q_b: tuple[float, float, float],
) -> None:
    force, moment = linear_moment_contribution(Vec3(*r_a), Vec3(*r_b), Vec3(*q_a), Vec3(*q_b))
    _, exact_m = _simpson(_f(r_a), _f(r_b), _f(q_a), _f(q_b), moment=True)
    assert force == ZERO
    for actual, expected in zip(moment.components(), exact_m, strict=True):
        assert actual == pytest.approx(float(expected), rel=1e-12, abs=1e-9)


# --- Consumidores de Load: CLI y traductores FEM (rechazo explícito en E1) ------------------


def test_cli_load_rows_show_new_loads() -> None:
    model, member, case_id = model_with_member((4.0, 0.0, 0.0))
    model.add_member_point_load(case_id, member.id, position=0.25, fz=kn(-10))
    model.add_member_distributed_load(
        case_id, member.id, start=0.25, end=0.75, qz_start=knpm(-1), qz_end=knpm(-3)
    )
    model.add_member_distributed_moment(
        case_id, member.id, start=0.0, end=1.0, mx_end=Quantity.of(600, "N·m/m")
    )
    rows = cli._load_rows(model, case_id, {member.id: "B1"}, PROFILE_KN_M)
    assert [row[:3] for row in rows] == [
        ("puntual de barra", "B1", "GLOBAL"),
        ("distribuida de barra", "B1", "GLOBAL"),
        ("momento distribuido", "B1", "GLOBAL"),
    ]
    assert rows[0][3].startswith("ξ = 0.25  F = ")
    assert "kN" in rows[0][3]
    assert rows[1][3].startswith("ξ = [0.25; 0.75]  qa = ")
    assert "kN/m" in rows[1][3]
    assert rows[2][3].startswith("ξ = [0; 1]  ma = ")
    assert "kN·m/m" in rows[2][3]


def loaded_frame(kind: str) -> Frame:
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(4.0, 0.0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    model, case_id = frame.model, frame.case_id
    if kind == "point":
        model.add_member_point_load(case_id, member.id, position=0.5, fz=kn(-1))
    elif kind == "distributed":
        model.add_member_distributed_load(
            case_id, member.id, start=0.0, end=0.5, qz_start=knpm(-1), qz_end=knpm(-1)
        )
    else:
        model.add_member_distributed_moment(case_id, member.id, start=0.0, end=1.0, my_start=kn(1))
    return frame


@pytest.mark.parametrize("kind", ["point", "distributed", "moment"])
def test_planar_translator_rejects_new_member_loads(kind: str) -> None:
    frame = loaded_frame(kind)
    with pytest.raises(InputValueError) as info:
        translate_planar_xz(frame.model, frame.config())
    assert info.value.code is D.UNSUPPORTED_CONFIGURATION
    assert info.value.diagnostic.message_key == "fem.load_type_unsupported"


@pytest.mark.parametrize("kind", ["point", "distributed", "moment"])
def test_spatial_translator_rejects_new_member_loads(kind: str) -> None:
    frame = loaded_frame(kind)
    with pytest.raises(InputValueError) as info:
        translate_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id))
    assert info.value.code is D.UNSUPPORTED_CONFIGURATION
    assert info.value.diagnostic.message_key == "fem3d.load_type_unsupported"
