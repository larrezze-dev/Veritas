"""Traductor plano XZ: reglas de compatibilidad, mapeo y determinismo (plan T-007, D2-D10)."""

import math

import pytest

from fem_support import AREA, E_PA, IY, IZ, Frame, inclined
from veritas.core.diagnostics import DiagnosticCode, InputValueError, Severity
from veritas.core.loads.loads import LoadBasis
from veritas.core.materials.material import MaterialFamily
from veritas.core.supports.support import (
    FIXED,
    FREE,
    DofRestraint,
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
    roller_restraints,
)
from veritas.core.units import Quantity
from veritas.fem.adapters.planar_xz import (
    HYPOTHESIS_OUT_OF_PLANE,
    HYPOTHESIS_PENALTY,
    translate_planar_xz,
)
from veritas.fem.adapters.protocol import PlanarRequest

D = DiagnosticCode


def cantilever(frame: Frame) -> Frame:
    a = frame.node(0, 0)
    b = frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-1000.0)
    return frame


def rejected(frame: Frame, key: str, code: DiagnosticCode = D.UNSUPPORTED_CONFIGURATION) -> None:
    with pytest.raises(InputValueError) as info:
        translate_planar_xz(frame.model, frame.config())
    assert info.value.code is code
    assert info.value.diagnostic.message_key == key


def test_cantilever_request_content() -> None:
    frame = cantilever(Frame())
    request = translate_planar_xz(frame.model, frame.config()).request
    assert sorted((n.x1, n.x2) for n in request.nodes) == [(0.0, 0.0), (2.0, 0.0)]
    (beam,) = request.beams
    assert (beam.area, beam.elastic_modulus, beam.inertia, beam.inertia_axis) == (
        AREA,
        E_PA,
        IY,
        "Iy",
    )
    (fix,) = request.fixes
    assert fix.fixity == (1, 1, 1)
    (load,) = request.nodal_loads
    assert (load.p1, load.p2, load.m) == (0.0, -1000.0, 0.0)
    assert request.springs == ()
    assert request.model_revision == frame.model.model_revision


def test_nodal_moment_maps_to_minus_my() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fx=3.0, fz=-5.0, my=7.0)
    (load,) = translate_planar_xz(frame.model, frame.config()).request.nodal_loads
    assert (load.p1, load.p2, load.m) == (3.0, -5.0, -7.0)


@pytest.mark.parametrize(
    ("end", "alpha", "reference", "axis", "inertia"),
    [
        ((2.0, 0.0), None, None, "Iy", IY),
        ((-2.0, 0.0), None, None, "Iy", IY),
        ((3.0, 4.0), None, None, "Iy", IY),
        ((0.0, 3.0), None, None, "Iy", IY),  # Vertical: referencia de respaldo −X.
        ((2.0, 0.0), math.pi / 2, None, "Iz", IZ),
        ((2.0, 0.0), -math.pi / 2, None, "Iz", IZ),
    ],
)
def test_inertia_selection(
    end: tuple[float, float],
    alpha: float | None,
    reference: tuple[float, float, float] | None,
    axis: str,
    inertia: float,
) -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(*end)
    frame.member(a, b, alpha=alpha, reference=reference)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-1.0)
    (beam,) = translate_planar_xz(frame.model, frame.config()).request.beams
    assert (beam.inertia_axis, beam.inertia) == (axis, inertia)


def test_oblique_member_axes_rejected() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b, alpha=math.pi / 4)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-1.0)
    rejected(frame, "fem.member_axis_not_plane")


def test_node_out_of_plane_rejected_and_limit_accepted() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0, y=1e-9)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-1.0)
    translate_planar_xz(frame.model, frame.config())  # |Y| = tol: admitido.
    frame.model.move_node(b.id, Quantity.of(2, "m"), Quantity.of(2e-9, "m"), Quantity.of(0, "m"))
    rejected(frame, "fem.node_out_of_plane")


def test_short_member_with_y_residual_is_rejected_not_corrected() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(1e-3, 0, y=1e-9)  # |ex·Y| = 1e-6 > tol_axis.
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-1.0)
    rejected(frame, "fem.member_axis_not_plane")


def test_timber_rejected_and_aluminium_accepted() -> None:
    rejected(cantilever(Frame(MaterialFamily.TIMBER)), "fem.material_family_unsupported")
    aluminium = cantilever(Frame(MaterialFamily.ALUMINIUM))
    assert translate_planar_xz(aluminium.model, aluminium.config()).request.beams


def test_isolated_node_and_empty_model_rejected() -> None:
    frame = cantilever(Frame())
    frame.node(5, 5)
    rejected(frame, "fem.isolated_node")
    empty = Frame()
    rejected(empty, "fem.no_members")


def test_empty_case_and_missing_case() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    rejected(frame, "fem.empty_case", D.INPUT_OUT_OF_RANGE)
    with pytest.raises(InputValueError) as info:
        translate_planar_xz(frame.model, Frame().config())
    assert info.value.code is D.REFERENCE_NOT_FOUND


@pytest.mark.parametrize(
    "components",
    [{"fy": 1.0}, {"mx": 1.0}, {"mz": 1.0}, {"fz": -1.0, "fy": 1e-12}],
)
def test_out_of_plane_nodal_loads_rejected(components: dict[str, float]) -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, **components)
    rejected(frame, "fem.load_out_of_plane")


def test_member_loads_global_and_local() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(3, 4)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    model = frame.model
    q = Quantity.of(-1000, "N/m")
    model.add_member_uniform_load(frame.case_id, member.id, qz=q)
    # LOCAL: q_z local; su componente Y global es residuo de redondeo → 0.
    model.add_member_uniform_load(frame.case_id, member.id, basis=LoadBasis.LOCAL, qz=q)
    loads = translate_planar_xz(model, frame.config()).request.beam_loads
    # x̂ = (0,6; 0,8), ŷ = (−0,8; 0,6). GLOBAL q = (0, −1000): (Wy, Wx) = (−600, −800).
    # LOCAL: ez = (−0,8; 0; 0,6) = ŷ, así que q_z local = −1000 es transversal pura.
    pairs = sorted((w.wy, w.wx) for w in loads)
    assert pairs[0] == pytest.approx((-1000.0, 0.0), abs=1e-9)
    assert pairs[1] == pytest.approx((-600.0, -800.0), abs=1e-9)


def test_member_load_wy_wx_horizontal() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.model.add_member_uniform_load(
        frame.case_id, member.id, qx=Quantity.of(5, "N/m"), qz=Quantity.of(-7, "N/m")
    )
    (load,) = translate_planar_xz(frame.model, frame.config()).request.beam_loads
    assert (load.wy, load.wx) == (-7.0, 5.0)


def test_member_load_out_of_plane_rejected() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.model.add_member_uniform_load(frame.case_id, member.id, qy=Quantity.of(1, "N/m"))
    rejected(frame, "fem.member_load_out_of_plane")


def test_local_member_load_along_ey_rejected() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    member = frame.member(a, b)  # ey = ±Y
    frame.support(a, fixed_restraints())
    frame.model.add_member_uniform_load(
        frame.case_id, member.id, basis=LoadBasis.LOCAL, qy=Quantity.of(1, "N/m")
    )
    rejected(frame, "fem.member_load_out_of_plane")


def test_self_weight_is_global_uniform_load() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.model.set_self_weight(frame.case_id, gravity=Quantity.of(10, "m/s²"))
    (load,) = translate_planar_xz(frame.model, frame.config()).request.beam_loads
    assert load.wx == 0.0
    assert load.wy == pytest.approx(-7850 * AREA * 10, rel=1e-12)


def test_support_spring_out_of_plane_rejected() -> None:
    frame = cantilever(Frame())
    a = next(iter(frame.model.supports.values())).node_id
    spring = DofRestraint.spring(SupportDof.UY, Quantity.of(1, "kN/m"))
    restraints = (FIXED, spring, FIXED, FIXED, FIXED, FIXED)
    frame.model.set_support(a, restraints)
    rejected(frame, "fem.support_spring_out_of_plane")


def test_inclined_basis_out_of_plane_rejected() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, pinned_restraints())
    basis = SupportBasis.inclined((1.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    frame.support(b, roller_restraints(), basis)
    frame.nodal(b, fz=-1.0)
    rejected(frame, "fem.support_basis_not_plane")


@pytest.mark.parametrize("flip", [False, True])
def test_inclined_roller_uses_penalty_with_plane_orient(flip: bool) -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(4, 0)
    frame.member(a, b)
    frame.support(a, pinned_restraints())
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    z_ref = (s, 0.0, -c) if flip else (-s, 0.0, c)
    basis = SupportBasis.inclined((c, 0.0, s), z_ref)
    frame.support(b, roller_restraints(), basis)
    frame.nodal(b, fz=-1.0)
    translation = translate_planar_xz(frame.model, frame.config())
    (spring,) = translation.request.springs
    assert spring.penalty
    assert spring.dof == 2
    assert spring.axis1 == pytest.approx((c, s), abs=1e-15)
    assert spring.axis2 == pytest.approx((s, -c) if flip else (-s, c), abs=1e-15)
    assert spring.stiffness > 1e8 * E_PA * AREA / 4 * 0.99
    assert HYPOTHESIS_PENALTY in translation.hypotheses


def test_inclined_with_both_translations_fixed_uses_exact_fix() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(4, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints(), inclined(30))
    frame.nodal(b, fz=-1.0)
    translation = translate_planar_xz(frame.model, frame.config())
    assert translation.request.springs == ()
    assert translation.request.fixes[0].fixity == (1, 1, 1)
    assert HYPOTHESIS_PENALTY not in translation.hypotheses


def test_inclined_aligned_with_axes_uses_exact_fix() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(4, 0)
    frame.member(a, b)
    frame.support(a, pinned_restraints())
    basis = SupportBasis.inclined((0.0, 0.0, 1.0), (-1.0, 0.0, 0.0))  # x′ = Z, z′ = −X
    frame.support(b, roller_restraints(SupportDof.UX), basis)
    frame.nodal(b, fz=-1.0)
    request = translate_planar_xz(frame.model, frame.config()).request
    assert request.springs == ()
    # Rodillo en Ux′ con x′ = Z: fija exactamente el GDL global 2 (Z) del nudo b.
    assert sorted(fix.fixity for fix in request.fixes) == [(0, 1, 0), (1, 1, 0)]


def test_springs_exact_with_rotation() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(4, 0)
    frame.member(a, b)
    k = Quantity.of(5, "kN*m/rad")
    restraints = build_restraints(
        fixed=[SupportDof.UX, SupportDof.UY, SupportDof.UZ],
        springs={SupportDof.RY: k},
    )
    frame.support(a, restraints)
    frame.support(b, roller_restraints())
    frame.nodal(b, fx=-1.0)
    request = translate_planar_xz(frame.model, frame.config()).request
    (spring,) = request.springs
    assert (spring.dof, spring.stiffness, spring.penalty) == (3, 5000.0, False)


def test_out_of_plane_warnings() -> None:
    frame = cantilever(Frame())
    assert translate_planar_xz(frame.model, frame.config()).warnings == ()
    roller = Frame()
    a, b = roller.node(0, 0), roller.node(2, 0)
    roller.member(a, b)
    roller.support(a, pinned_restraints())
    roller.support(b, roller_restraints())
    roller.nodal(b, fz=-1.0)
    translation = translate_planar_xz(roller.model, roller.config())
    assert {w.params_dict()["dof"] for w in translation.warnings} == {"Rx", "Rz"}
    assert all(w.severity is Severity.WARNING for w in translation.warnings)
    assert HYPOTHESIS_OUT_OF_PLANE in translation.hypotheses


def test_unavailable_restraint_kinds_are_free() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, (FIXED, FREE, FIXED, FREE, FIXED, FREE))
    frame.nodal(b, fz=-1.0)
    (fix,) = translate_planar_xz(frame.model, frame.config()).request.fixes
    assert fix.fixity == (1, 1, 1)


def _two_bar_model(order: tuple[int, ...]) -> tuple[Frame, str]:
    frame = Frame()
    coordinates = [(0.0, 0.0), (2.0, 0.0), (4.0, 1.0)]
    nodes = {i: frame.node(*coordinates[i]) for i in order}
    for i, j in ((0, 1), (1, 2)):
        frame.member(nodes[i], nodes[j])
    frame.support(nodes[0], fixed_restraints())
    frame.nodal(nodes[2], fz=-1.0)
    return frame, translate_planar_xz(frame.model, frame.config()).request.to_json()


def test_translation_is_deterministic_and_does_not_modify_model() -> None:
    frame, first = _two_bar_model((0, 1, 2))
    revision = frame.model.model_revision
    second = translate_planar_xz(frame.model, frame.config()).request.to_json()
    assert first == second
    assert frame.model.model_revision == revision
    assert PlanarRequest.from_json(first).to_json() == first


def test_tags_follow_uuid_order() -> None:
    _, text = _two_bar_model((2, 0, 1))
    request = PlanarRequest.from_json(text)
    assert [n.entity_id for n in request.nodes] == sorted(n.entity_id for n in request.nodes)
    assert [n.tag for n in request.nodes] == [1, 2, 3]
    assert [b.entity_id for b in request.beams] == sorted(b.entity_id for b in request.beams)
