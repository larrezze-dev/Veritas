"""Consumidores del peso propio sobre la fuente única (LD-003; plan T-019, E2).

D4-A: ``StructuralModel.self_weight_loads()`` es la única fuente de barras y q; los traductores
y las normalizaciones plana y espacial solo iteran en su propio orden. Aquí se comprueba que
los cinco sitios (el modelo, los dos traductores y las dos normalizaciones) listan las mismas
barras con la misma q, que ΣF de la entrada es igual a las reacciones, la regla E2-d (D8), el
contraste plano/espacial y la identidad de la petición con la de HEAD (D6).
"""

import itertools
import math
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from fem_support import Frame
from veritas import fem
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import InputValueError
from veritas.core.geometry import entities
from veritas.core.geometry.model import StructuralModel
from veritas.core.loads.load_case import LoadCase, LoadCategory
from veritas.core.loads.loads import SelfWeightMode
from veritas.core.materials.material import MaterialFamily, MaterialRevision
from veritas.core.supports.support import fixed_restraints, pinned_restraints
from veritas.core.units import Quantity
from veritas.fem.adapters.planar_xz import translate_planar_xz
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.results import normalized
from veritas.fem.results.run import RunStatus
from veritas.fem.spatial import analyze_spatial
from veritas.fem.spatial import results as spatial_results
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.translator import translate_spatial

HEAD_REQUESTS = Path(__file__).parent / "data" / "self_weight_head_requests.txt"
EXCLUSIONS = pytest.mark.parametrize(
    "excluded", [(), (1,), (0, 2)], ids=["sin excluir", "B2", "B1 y B3"]
)


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def _material(model: StructuralModel, name: str, gamma_kn_m3: float | None) -> MaterialRevision:
    return model.add_material(
        name=name,
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(210, "GPa"),
        shear_modulus=Quantity.of(81, "GPa"),
        density=Quantity.of(7850, "kg/m³"),
        specific_weight=None if gamma_kn_m3 is None else Quantity.of(gamma_kn_m3, "kN/m³"),
    )


def triangle() -> tuple[StructuralModel, list[UUID], LoadCase]:
    """Triángulo en XZ: B1 horizontal, B2 vertical y B3 oblicua, con material y sección
    distintos por barra (q distinta). Empotrado en N1 y articulado en N3."""
    model = StructuralModel()
    steel_a = _material(model, "A", 25.0)
    steel_b = _material(model, "B", 30.0)
    sec_a = model.add_rectangular_section(b=m(0.2), h=m(0.4))
    sec_b = model.add_rectangular_section(b=m(0.2), h=m(0.25))
    n1 = model.add_node(m(0), m(0), m(0))
    n2 = model.add_node(m(3), m(0), m(0))
    n3 = model.add_node(m(3), m(0), m(2))
    refs = ((n1, n2, steel_a, sec_a), (n2, n3, steel_b, sec_b), (n1, n3, steel_a, sec_b))
    members = [
        model.add_member(a.id, b.id, material=mat.ref, section=sec.ref).id
        for a, b, mat, sec in refs
    ]
    model.set_support(n1.id, fixed_restraints())
    model.set_support(n3.id, pinned_restraints())
    case = model.add_load_case("G1", LoadCategory.PERMANENT)
    return model, members, case


def _length(model: StructuralModel, member_id: UUID) -> float:
    member = model.member(member_id)
    return (model.node(member.node_j).position - model.node(member.node_i).position).norm()


def _expected(members: list[UUID], excluded: tuple[int, ...]) -> dict[UUID, float]:
    """q (N/m, hacia −Z) de cada barra incluida, a mano: γ del material por A = b·h."""
    areas = {members[0]: 0.2 * 0.4, members[1]: 0.2 * 0.25, members[2]: 0.2 * 0.25}
    gammas = {members[0]: 25e3, members[1]: 30e3, members[2]: 25e3}
    return {mid: gammas[mid] * areas[mid] for i, mid in enumerate(members) if i not in excluded}


def _value(component: Any) -> float:
    value = component.value
    assert value is not None
    return float(value)


def _setup(excluded: tuple[int, ...]) -> tuple[StructuralModel, LoadCase, dict[UUID, float]]:
    model, members, case = triangle()
    model.set_self_weight(
        case.id,
        mode=SelfWeightMode.SPECIFIC_WEIGHT,
        excluded_members=[members[i] for i in excluded],
    )
    return model, case, _expected(members, excluded)


def _solved(model: StructuralModel, case: LoadCase) -> tuple[Any, Any]:
    planar = fem.analyze_planar_xz(model, AnalysisConfiguration(case.id))
    spatial = analyze_spatial(model, SpatialAnalysisConfiguration(case.id))
    assert planar.status is RunStatus.SUCCEEDED, planar.diagnostics
    assert spatial.status is RunStatus.SUCCEEDED, spatial.diagnostics
    return planar, spatial


@EXCLUSIONS
def test_model_and_translators_list_the_same_members_and_q(excluded: tuple[int, ...]) -> None:
    model, case, expected = _setup(excluded)
    assert len(expected) == 3 - len(excluded)

    # Sitio 1: el modelo (fuente única) y su resultante.
    source = {mid: -q[2].si_value for mid, q in model.self_weight_loads().items()}
    assert source == pytest.approx(expected, rel=1e-12)
    total = sum(q * _length(model, mid) for mid, q in expected.items())
    assert model.case_resultant(case.id).force[2].si_value == pytest.approx(-total, rel=1e-12)

    # Sitio 2: traductor plano (|q| = √(wy² + wx²), hacia −Z).
    planar_request = translate_planar_xz(model, AnalysisConfiguration(case.id)).request
    ids = {b.tag: UUID(b.entity_id) for b in planar_request.beams}
    planar = {ids[w.beam]: math.hypot(w.wy, w.wx) for w in planar_request.beam_loads}
    assert planar == pytest.approx(expected, rel=1e-12)

    # Sitio 3: traductor espacial.
    spatial_request = translate_spatial(model, SpatialAnalysisConfiguration(case.id)).request
    ids = {b.tag: UUID(b.entity_id) for b in spatial_request.beams}
    spatial = {
        ids[w.beam]: math.sqrt(w.wy**2 + w.wz**2 + w.wx**2) for w in spatial_request.beam_loads
    }
    assert spatial == pytest.approx(expected, rel=1e-12)


@pytest.mark.solver
@EXCLUSIONS
def test_normalizations_and_reactions_follow_the_same_selection(excluded: tuple[int, ...]) -> None:
    model, case, expected = _setup(excluded)
    planar_run, spatial_run = _solved(model, case)

    # Sitios 4 y 5: las cargas del dominio de las dos normalizaciones.
    plane = normalized._Normalizer(
        planar_run, model, normalized._checked_request(planar_run, model)
    )
    space = spatial_results._Normalizer(
        spatial_run, model, spatial_results._checked_request(spatial_run, model)
    )
    for normalizer in (plane, space):
        distributed = normalizer._domain_loads()[1]
        assert {k: -v.z for k, v in distributed.items()} == pytest.approx(expected, rel=1e-12)
        assert all(v.x == 0.0 and v.y == 0.0 for v in distributed.values())

    # ΣF de la entrada = reacciones, en los dos adaptadores (equilibrio global, ADR-001).
    total = sum(q * _length(model, mid) for mid, q in expected.items())
    for results in (
        normalized.normalize_planar_xz(planar_run, model),
        spatial_results.normalize_spatial(spatial_run, model),
    ):
        fx = sum(_value(r.global_.components[0]) for r in results.reactions)
        fz = sum(_value(r.global_.components[2]) for r in results.reactions)
        assert fz == pytest.approx(total, rel=1e-9)
        assert abs(fx) <= 1e-9 * total


@pytest.mark.solver
def test_planar_and_spatial_agree_with_gamma_and_exclusion() -> None:
    model, case, _ = _setup((1,))
    planar_run, spatial_run = _solved(model, case)
    plane = normalized.normalize_planar_xz(planar_run, model)
    space = spatial_results.normalize_spatial(spatial_run, model)
    scale = max(abs(_value(r.global_.components[2])) for r in plane.reactions)
    by_node = {r.node_id: r for r in space.reactions}
    for reaction in plane.reactions:
        other = by_node[reaction.node_id]
        for index in (0, 2, 4):  # Fx, Fz y My (los del plano)
            a, b = reaction.global_.components[index], other.global_.components[index]
            if a.value is None or b.value is None:  # GDL sin restricción: sin reacción
                assert a.value is None
                assert b.value is None
                continue
            assert abs(a.value - b.value) <= 1e-9 * scale
    displacements = {n.node_id: n for n in space.nodes}
    for node in plane.nodes:
        for index in (0, 2):
            ua = _value(node.displacement.components[index])
            ub = _value(displacements[node.node_id].displacement.components[index])
            assert abs(ua - ub) <= 1e-9 * max(abs(ua), 1e-6)


@pytest.mark.solver
def test_planar_normalization_with_a_subdivided_bar() -> None:
    """Una barra recta en cuatro tramos: ``self.beams`` abarca todos y las incluidas, solo dos."""
    model = StructuralModel()
    material = _material(model, "A", 25.0)
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4))
    nodes = [model.add_node(m(x), m(0), m(0)) for x in range(5)]
    members = [
        model.add_member(a.id, b.id, material=material.ref, section=section.ref).id
        for a, b in itertools.pairwise(nodes)
    ]
    model.set_support(nodes[0].id, fixed_restraints())
    case = model.add_load_case("G1", LoadCategory.PERMANENT)
    model.set_self_weight(
        case.id, mode=SelfWeightMode.SPECIFIC_WEIGHT, excluded_members=[members[1], members[2]]
    )
    planar_run = fem.analyze_planar_xz(model, AnalysisConfiguration(case.id))
    assert planar_run.status is RunStatus.SUCCEEDED, planar_run.diagnostics
    plane = normalized._Normalizer(
        planar_run, model, normalized._checked_request(planar_run, model)
    )
    assert set(plane.beams) == set(members)
    assert set(plane._domain_loads()[1]) == {members[0], members[3]}
    (reaction,) = normalized.normalize_planar_xz(planar_run, model).reactions
    assert _value(reaction.global_.components[2]) == pytest.approx(2 * 2000.0, rel=1e-9)


# --- D8: la regla E2-d sigue la selección -------------------------------------------------


def _end_axial_frame() -> tuple[Frame, list[UUID]]:
    """B1 oblicua con Ux liberado en J y B2 horizontal; apoyo empotrado en N1."""
    frame = Frame()
    frame.material = frame.model.revise_material(
        frame.material.id, specific_weight=Quantity.of(25, "kN/m³")
    )
    n1, n2, n3 = frame.node(0, 0), frame.node(2, 1), frame.node(4, 1)
    first, second = frame.member(n1, n2), frame.member(n2, n3)
    frame.support(n1, fixed_restraints())
    frame.model.set_member_end(
        first.id, MemberEnd.J, EndConnection.rigid().with_dof(Dof.UX, DofState.RELEASED)
    )
    return frame, [first.id, second.id]


@pytest.mark.parametrize("mode", list(SelfWeightMode), ids=lambda mode: mode.name)
def test_axial_rule_follows_the_self_weight_selection(mode: SelfWeightMode) -> None:
    frame, (first, second) = _end_axial_frame()
    gravity = Quantity.of(10, "m/s²") if mode is SelfWeightMode.MASS_DENSITY else None
    config = SpatialAnalysisConfiguration(frame.case_id)

    frame.model.set_self_weight(frame.case_id, gravity=gravity, mode=mode)
    with pytest.raises(InputValueError) as info:
        translate_spatial(frame.model, config)
    assert info.value.diagnostic.message_key == "fem3d.end_axial_with_axial_load"

    frame.model.set_self_weight(frame.case_id, gravity=gravity, mode=mode, excluded_members=[first])
    request = translate_spatial(frame.model, config).request
    loaded = {w.beam for w in request.beam_loads}
    assert loaded == {b.tag for b in request.beams if UUID(b.entity_id) == second}


# --- D6: identidad de la petición con la de HEAD ------------------------------------------


def _head_model(monkeypatch: pytest.MonkeyPatch) -> tuple[StructuralModel, LoadCase]:
    """Modelo con UUID deterministas, sin γ ni exclusiones (el de la referencia de HEAD)."""
    counter = itertools.count(1)
    monkeypatch.setattr(entities, "uuid4", lambda: UUID(int=next(counter), version=4))
    model = StructuralModel()
    material = model.add_material(
        name="S",
        family=MaterialFamily.STEEL,
        source="t",
        elastic_modulus=Quantity.of(210, "GPa"),
        shear_modulus=Quantity.of(81, "GPa"),
        density=Quantity.of(7850, "kg/m³"),
    )
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4))
    n1 = model.add_node(m(0), m(0), m(0))
    n2 = model.add_node(m(3), m(0), m(0))
    n3 = model.add_node(m(3), m(0), m(2))
    found = [
        model.add_member(a.id, b.id, material=material.ref, section=section.ref)
        for a, b in ((n1, n2), (n2, n3), (n1, n3))
    ]
    model.set_support(n1.id, fixed_restraints())
    model.set_support(n3.id, pinned_restraints())
    case = model.add_load_case("G1", LoadCategory.PERMANENT)
    model.add_nodal_load(case.id, n2.id, fz=Quantity.of(-1000, "N"))
    model.add_member_uniform_load(case.id, found[1].id, qz=Quantity.of(-500, "N/m"))
    model.set_self_weight(case.id, gravity=Quantity.of(9.81, "m/s²"))
    return model, case


def test_request_without_gamma_or_exclusions_is_identical_to_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D6-A: sin γ ni exclusiones, las peticiones plana y espacial coinciden con ``repr`` con
    las de HEAD 7deb2f1 (guardadas antes de tocar los traductores): sin subir versiones."""
    model, case = _head_model(monkeypatch)
    planar = translate_planar_xz(model, AnalysisConfiguration(case.id)).request
    spatial = translate_spatial(model, SpatialAnalysisConfiguration(case.id)).request
    expected = HEAD_REQUESTS.read_text(encoding="utf-8").splitlines()
    assert [repr(planar), repr(spatial)] == expected
