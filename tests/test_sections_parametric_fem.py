"""Uso en el solver de los perfiles paramétricos, sin tocar los adaptadores (plan T-025 E1).

Voladizo con carga en el extremo: δ = P·L³/(3·E·I) con la inercia de la referencia independiente
(``section_reference``), no de la fórmula cerrada. El adaptador plano solo exige A, Iy e Iyz
(Euler–Bernoulli plano); el espacial exige además J: una I o un RHS del editor, con J
``NO_DISPONIBLE`` (D7), se rechazan con ``fem.section_property_unavailable`` (nunca un análisis
«válido» con un dato ausente); la CHS (J exacta) sí se analiza en 3D.
"""

from typing import Any

import pytest

import section_reference as ref
from fem_support import E_PA, Frame
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.sections.parametric import ParametricShape
from veritas.core.supports.support import fixed_restraints
from veritas.fem import RunStatus, analyze_planar_xz
from veritas.fem.spatial import analyze_spatial
from veritas.fem.spatial.config import SpatialAnalysisConfiguration

pytestmark = pytest.mark.solver

L, P = 2.0, 1000.0


def mm(**dims: float) -> dict[str, u.Quantity]:
    return {name: u.Quantity.of(value, "mm") for name, value in dims.items()}


def cantilever(shape: ParametricShape, **dims: float) -> tuple[Frame, Any]:
    frame = Frame()
    frame.section = frame.model.add_parametric_section(shape, mm(**dims))
    a, b = frame.node(0.0, 0.0), frame.node(L, 0.0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-P)
    return frame, b


CASES = [
    (
        ParametricShape.I,
        {"h": 300, "b": 150, "tw": 7.1, "tf": 10.7},
        ref.i_section(0.3, 0.15, 0.0071, 0.0107),
    ),
    (
        ParametricShape.I,
        {"h": 300, "b": 150, "tw": 7.1, "tf": 10.7, "r": 15},
        ref.i_section(0.3, 0.15, 0.0071, 0.0107, 0.015),
    ),
    (
        ParametricShape.RHS,
        {"h": 300, "b": 200, "t": 10, "ro": 15},
        ref.rect_tube(0.3, 0.2, 0.01, 0.015),
    ),
    (ParametricShape.CHS, {"d": 100, "t": 5}, ref.circular_tube(0.1, 0.005)),
]


@pytest.mark.parametrize(("shape", "dims", "geometry"), CASES)
def test_planar_cantilever_matches_pl3_over_3ei_with_the_independent_inertia(
    shape: ParametricShape, dims: dict[str, float], geometry: ref.Geometry
) -> None:
    frame, tip = cantilever(shape, **dims)
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.SUCCEEDED, run.diagnostics
    assert run.raw is not None
    result = next(n for n in run.raw.nodes if n.node_id == tip.id)
    assert result.displacement[1] == pytest.approx(-P * L**3 / (3 * E_PA * geometry.iy()), rel=1e-9)


def test_spatial_cantilever_with_a_chs_uses_its_exact_torsion_constant() -> None:
    frame, tip = cantilever(ParametricShape.CHS, d=100, t=5)
    run = analyze_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id))
    assert run.status is RunStatus.SUCCEEDED, run.diagnostics
    assert run.raw is not None
    result = next(n for n in run.raw.nodes if n.node_id == tip.id)
    expected = -P * L**3 / (3 * E_PA * ref.circular_tube(0.1, 0.005).iy())
    assert result.displacement[2] == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize(
    ("shape", "dims"),
    [
        (ParametricShape.I, {"h": 300, "b": 150, "tw": 7.1, "tf": 10.7}),
        (ParametricShape.RHS, {"h": 300, "b": 200, "t": 10}),
    ],
)
def test_spatial_solver_rejects_a_profile_without_torsion_never_filling_it_in(
    shape: ParametricShape, dims: dict[str, float]
) -> None:
    frame, _ = cantilever(shape, **dims)
    with pytest.raises(DiagnosticError) as info:
        analyze_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id))
    assert info.value.diagnostic.message_key == "fem.section_property_unavailable"
    assert dict(info.value.diagnostic.params_dict())["property"] == "J"
