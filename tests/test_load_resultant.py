"""Resultantes ΣF/ΣM de acciones prescritas y proyección local -> global (plan T-006, D8-D9).

Valores de referencia derivados a mano (cuerpo libre), no de la salida del código.
Tolerancia absoluta 1e-9 N / N·m: residuos de redondeo de Qᵀ·q_local (orden 1e-12).
"""

import math

import pytest

from veritas.core.geometry.entities import Member
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.load_case import LoadCategory
from veritas.core.loads.loads import LoadBasis, point_contribution, uniform_contribution
from veritas.core.materials.material import MaterialFamily
from veritas.core.units import Quantity

TOL = 1e-9


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def model_with_member(
    end: tuple[float, float, float], alpha: Quantity | None = None
) -> tuple[StructuralModel, Member]:
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
    a = model.add_node(m(0), m(0), m(0))
    b = model.add_node(m(end[0]), m(end[1]), m(end[2]))
    member = model.add_member(a.id, b.id, material=material.ref, section=section.ref, alpha=alpha)
    return model, member


def si(values: tuple[Quantity, Quantity, Quantity]) -> tuple[float, float, float]:
    return (values[0].si_value, values[1].si_value, values[2].si_value)


def assert_vec(actual: tuple[float, float, float], expected: tuple[float, float, float]) -> None:
    for a, e in zip(actual, expected, strict=True):
        assert a == pytest.approx(e, abs=TOL)


@pytest.mark.parametrize(
    ("end", "alpha", "q_local", "q_global"),
    [
        # Barra en +Y: ex = Y, ey = -X, ez = Z (ADR-001 §2). q_y local = 1000 -> (-1000, 0, 0).
        ((0, 1, 0), None, {"qy": 1000.0}, (-1000.0, 0.0, 0.0)),
        # Barra en +X con alfa = π/2: ey = Z, ez = -Y. q_z local = -1000 -> (0, +1000, 0).
        ((1, 0, 0), Quantity.of(math.pi / 2, "rad"), {"qz": -1000.0}, (0.0, 1000.0, 0.0)),
        # Barra en +X sin giro: local = global.
        ((1, 0, 0), None, {"qz": -1000.0}, (0.0, 0.0, -1000.0)),
    ],
)
def test_local_basis_projection(
    end: tuple[float, float, float],
    alpha: Quantity | None,
    q_local: dict[str, float],
    q_global: tuple[float, float, float],
) -> None:
    model, member = model_with_member(end, alpha)
    case = model.add_load_case("Q1", LoadCategory.VARIABLE)
    components = {k: Quantity.of(v, "N/m") for k, v in q_local.items()}
    model.add_member_uniform_load(case.id, member.id, basis=LoadBasis.LOCAL, **components)
    # L = 1 m: ΣF = q_global.
    assert_vec(si(model.case_resultant(case.id).force), q_global)


def test_local_load_follows_member_reorientation() -> None:
    model, member = model_with_member((1, 0, 0))
    case = model.add_load_case("Q1", LoadCategory.VARIABLE)
    model.add_member_uniform_load(
        case.id, member.id, basis=LoadBasis.LOCAL, qz=Quantity.of(-1000, "N/m")
    )
    before = model.model_revision
    model.set_member_orientation(member.id, alpha=Quantity.of(math.pi / 2, "rad"))
    assert model.model_revision == before + 1
    assert_vec(si(model.case_resultant(case.id).force), (0.0, 1000.0, 0.0))


def test_demo_case_q1_by_hand() -> None:
    """Q1 del plan (tabla D9): F = (8000, 0, -24000) N; M = (0, 105000, 0) N·m."""
    model, b1 = model_with_member((4, 0, 0))
    n2 = b1.node_j
    n3 = model.add_node(m(7), m(0), m(4))
    b2 = model.add_member(n2, n3.id, material=b1.material, section=b1.section)
    q1 = model.add_load_case("Q1", LoadCategory.VARIABLE)
    model.add_nodal_load(q1.id, n2, fz=Quantity.of(-10, "kN"))
    for member in (b1, b2):
        model.add_member_uniform_load(
            q1.id, member.id, basis=LoadBasis.LOCAL, qz=Quantity.of(-2, "kN/m")
        )
    resultant = model.case_resultant(q1.id)
    assert_vec(si(resultant.force), (8000.0, 0.0, -24_000.0))
    assert_vec(si(resultant.moment), (0.0, 105_000.0, 0.0))
    assert resultant.load_count == 3


def test_nodal_moment_is_added_to_r_cross_f() -> None:
    model, member = model_with_member((4, 0, 0))
    case = model.add_load_case("Q1", LoadCategory.VARIABLE)
    model.add_nodal_load(case.id, member.node_j, fy=Quantity.of(1, "kN"), mz=Quantity.of(3, "kN·m"))
    resultant = model.case_resultant(case.id)
    # r = (4,0,0), F = (0,1000,0): r × F = (0, 0, 4000); + M (0, 0, 3000).
    assert_vec(si(resultant.force), (0.0, 1000.0, 0.0))
    assert_vec(si(resultant.moment), (0.0, 0.0, 7000.0))


def test_empty_case_has_zero_count() -> None:
    model, _ = model_with_member((1, 0, 0))
    case = model.add_load_case("Vacío", LoadCategory.VARIABLE)
    resultant = model.case_resultant(case.id)
    assert resultant.load_count == 0
    assert si(resultant.force) == (0.0, 0.0, 0.0)


def test_contribution_helpers() -> None:
    force, moment = point_contribution(Vec3(1, 2, 3), Vec3(0, 0, -1), Vec3(0, 0, 0))
    assert force == Vec3(0, 0, -1)
    assert moment == Vec3(-2.0, 1.0, 0.0)  # (1,2,3) × (0,0,-1)
    force, moment = uniform_contribution(Vec3(0, 0, 0), Vec3(3, 0, 4), Vec3(0, 0, -1))
    assert force.components() == pytest.approx((0.0, 0.0, -5.0))
    assert moment.components() == pytest.approx((0.0, 7.5, 0.0))  # (1,5;0;2) × (0;0;-5)
