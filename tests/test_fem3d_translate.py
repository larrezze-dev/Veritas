"""Traductor espacial: mapeo, rechazos de E1, descomposición de apoyos y tags (plan T-018)."""

import ast
import dataclasses
import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import veritas
from fem_support import AREA, E_PA, IY, IZ, Frame, inclined
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.geometry.local_axes import ReferenceSource
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.loads import LoadBasis
from veritas.core.materials.material import MaterialFamily
from veritas.core.messages_es import MESSAGES_ES, render_message, render_text
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.sections.rectangular import torsion_constant
from veritas.core.supports.support import (
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
)
from veritas.core.units import Quantity
from veritas.fem.adapters import opensees_worker_3d
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import SpatialRequest
from veritas.fem.spatial.translator import (
    HYPOTHESIS_CHAIN,
    HYPOTHESIS_LAGRANGE,
    HYPOTHESIS_PENALTY,
    SpatialTranslation,
    aligned_axis,
    translate_spatial,
)

D = DiagnosticCode
G_PA = 81e9
J = torsion_constant(0.2, 0.4)
OBLIQUE = (1.2, -0.7, 0.9)


def config(frame: Frame, **overrides: float) -> SpatialAnalysisConfiguration:
    return SpatialAnalysisConfiguration(frame.case_id, **overrides)  # type: ignore[arg-type]


def translate(frame: Frame, **overrides: float) -> SpatialTranslation:
    return translate_spatial(frame.model, config(frame, **overrides))


def cantilever(
    frame: Frame, tip: tuple[float, float, float] = (2.0, 0.0, 0.0), alpha: float | None = None
) -> Frame:
    a = frame.node(0, 0)
    b = frame.node(tip[0], tip[2], y=tip[1])
    frame.member(a, b, alpha=alpha)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-1000.0)
    return frame


def rejected(frame: Frame, key: str, code: DiagnosticCode = D.UNSUPPORTED_CONFIGURATION) -> None:
    with pytest.raises(InputValueError) as info:
        translate(frame)
    assert info.value.code is code
    assert info.value.diagnostic.message_key == key
    assert "{" not in render_message(info.value.diagnostic)  # Parámetros = plantilla.


def test_fem3d_keys_are_in_catalog() -> None:
    """El test general del catálogo no reconoce claves con dígitos como ``fem3d.*``."""
    package_root = Path(veritas.__file__).resolve().parent
    keys = {
        key
        for path in package_root.rglob("*.py")
        if path.name != "messages_es.py"
        for key in re.findall(r'"(fem3d\.[a-z_]+)"', path.read_text(encoding="utf-8"))
    }
    assert "fem3d.member_end_not_rigid" in keys
    assert keys <= set(MESSAGES_ES)


def test_hypotheses_render() -> None:
    translation = translate(busy_frame())
    for key in translation.hypotheses:
        assert key in MESSAGES_ES
        assert "{" not in render_text(key)


def supported_tip(basis: SupportBasis, **states: object) -> SpatialRequest:
    """Voladizo oblicuo con un apoyo adicional en el extremo libre."""
    frame = cantilever(Frame(), OBLIQUE, alpha=0.37)
    tip = next(n for n in frame.model.nodes.values() if n.position.x != 0.0)
    fixed = states.get("fixed", ())
    springs = states.get("springs", {})
    frame.support(tip, build_restraints(fixed, springs), basis)  # type: ignore[arg-type]
    return translate(frame).request


def tip_tag(request: SpatialRequest) -> int:
    return next(n.tag for n in request.nodes if n.x != 0.0)


# --- Barras ---------------------------------------------------------------------------


def test_cantilever_request_content() -> None:
    frame = cantilever(Frame())
    translation = translate(frame)
    request = translation.request
    (beam,) = request.beams
    assert (beam.area, beam.elastic_modulus, beam.shear_modulus) == (AREA, E_PA, G_PA)
    assert (beam.inertia_y, beam.inertia_z, beam.torsion_constant) == (IY, IZ, J)
    (fix,) = request.fixes
    assert fix.fixity == (1, 1, 1, 1, 1, 1)
    assert (request.springs, request.chains, request.beam_loads) == ((), (), ())
    (load,) = request.nodal_loads
    assert load.values == (0.0, 0.0, -1000.0, 0.0, 0.0, 0.0)
    assert request.model_revision == frame.model.model_revision
    assert HYPOTHESIS_LAGRANGE in translation.hypotheses
    assert HYPOTHESIS_CHAIN not in translation.hypotheses
    assert HYPOTHESIS_PENALTY not in translation.hypotheses


ORIENTATIONS = [
    ("+X", (2.0, 0.0, 0.0), None),
    ("+Y", (0.0, 2.0, 0.0), None),
    ("-X", (-2.0, 0.0, 0.0), None),
    ("+Z", (0.0, 0.0, 2.0), None),
    ("-Z", (0.0, 0.0, -2.0), None),
    ("+X alfa=pi/2", (2.0, 0.0, 0.0), math.pi / 2),
    ("oblicua", OBLIQUE, 0.37),
    # Alrededor del umbral de 1e-8 de la proyección de +Z (ADR-001, paso 2).
    ("casi vertical, por encima", (2e-8 * 2.0, 0.0, 2.0), None),
    ("casi vertical, por debajo", (0.5e-8 * 2.0, 0.0, 2.0), None),
]


@pytest.mark.parametrize(
    ("tip", "alpha"), [o[1:] for o in ORIENTATIONS], ids=[o[0] for o in ORIENTATIONS]
)
def test_vecxz_is_local_ez(tip: tuple[float, float, float], alpha: float | None) -> None:
    frame = cantilever(Frame(), tip, alpha)
    (beam,) = translate(frame).request.beams
    (member_id,) = frame.model.members
    assert beam.vecxz == frame.model.axes(member_id).ez.components()


@pytest.mark.parametrize(
    ("tip", "source"),
    [
        ((0.0, 0.0, 2.0), ReferenceSource.FALLBACK_NEG_X),
        ((0.0, 0.0, -2.0), ReferenceSource.FALLBACK_NEG_X),
        ((0.5e-8 * 2.0, 0.0, 2.0), ReferenceSource.FALLBACK_NEG_X),
        ((2e-8 * 2.0, 0.0, 2.0), ReferenceSource.DEFAULT_Z),
    ],
)
def test_vecxz_near_vertical(tip: tuple[float, float, float], source: ReferenceSource) -> None:
    """Hecho 1: a ambos lados del umbral de 1e-8 la base es continua, ez ≈ −X."""
    frame = cantilever(Frame(), tip)
    (member_id,) = frame.model.members
    assert frame.model.axes(member_id).orientation.source is source
    (beam,) = translate(frame).request.beams
    assert beam.vecxz == pytest.approx((-1.0, 0.0, 0.0), abs=1e-7)


def test_max_diagonal_and_alphas() -> None:
    frame = cantilever(Frame(), OBLIQUE, alpha=0.37)
    translation = translate(frame)
    length = math.hypot(*OBLIQUE)
    expected = max(
        E_PA * AREA / length,
        12 * E_PA * IY / length**3,
        12 * E_PA * IZ / length**3,
        4 * E_PA * IY / length,
        4 * E_PA * IZ / length,
        G_PA * J / length,
    )
    assert translation.max_diagonal == pytest.approx(expected, rel=1e-15)
    assert translation.request.alpha_sp == translation.request.alpha_mp
    assert translation.request.alpha_sp == translation.max_diagonal
    assert translation.chain_length == pytest.approx(length, rel=1e-15)


# --- Rechazos de E1 -------------------------------------------------------------------


def test_timber_rejected_and_aluminium_accepted() -> None:
    rejected(cantilever(Frame(MaterialFamily.TIMBER)), "fem.material_family_unsupported")
    translate(cantilever(Frame(MaterialFamily.ALUMINIUM)))


def test_no_members_and_isolated_node() -> None:
    empty = Frame()
    empty.node(0, 0)
    rejected(empty, "fem.no_members")
    frame = cantilever(Frame())
    frame.node(5, 5)
    rejected(frame, "fem.isolated_node")


def test_empty_case_rejected() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    rejected(frame, "fem.empty_case", D.INPUT_OUT_OF_RANGE)


@pytest.mark.parametrize("end", list(MemberEnd))
@pytest.mark.parametrize(
    "connection",
    [EndConnection.pinned(), EndConnection.rigid().with_dof(Dof.RX, DofState.RELEASED)],
    ids=["articulado", "torsion"],
)
def test_non_rigid_end_guard_keeps_the_e1_rejection(
    end: MemberEnd, connection: EndConnection
) -> None:
    """E2.2-d: el traductor ya admite extremos; la guarda de orquestación conserva el
    rechazo de E1, con sus parámetros, hasta E2.5."""
    frame = cantilever(Frame())
    (member_id,) = frame.model.members
    frame.model.set_member_end(member_id, end, connection)
    translate(frame)
    with pytest.raises(InputValueError) as info:
        translate_spatial(frame.model, config(frame), e1_end_guard=True)
    assert info.value.code is D.UNSUPPORTED_CONFIGURATION
    assert info.value.diagnostic.message_key == "fem3d.member_end_not_rigid"
    assert info.value.diagnostic.params_dict()["end"] == str(end)
    assert "{" not in render_message(info.value.diagnostic)


def _with_property(frame: Frame, name: str, value: float | None) -> None:
    (member,) = frame.model.members.values()
    section = frame.model.section(member.section)
    status = PropertyStatus.NO_DISPONIBLE if value is None else PropertyStatus.CALCULADO
    prop = SectionProperty(value, section.properties.iy.unit, "test", "test", status)
    # Doble mínimo: la revisión rectangular real valida Iyz = +0, que es lo que se rechaza.
    changed = SimpleNamespace(properties=dataclasses.replace(section.properties, **{name: prop}))
    original = frame.model.section

    def patched(ref: object) -> object:
        return changed if ref == member.section else original(ref)  # type: ignore[arg-type]

    object.__setattr__(frame.model, "section", patched)


def test_torsion_constant_unavailable_rejected() -> None:
    frame = cantilever(Frame())
    _with_property(frame, "torsion_constant", None)
    rejected(frame, "fem.section_property_unavailable")


def test_coupled_bending_rejected() -> None:
    frame = cantilever(Frame())
    _with_property(frame, "iyz", 1e-6)
    rejected(frame, "fem.section_coupled_bending")


# --- Cargas ---------------------------------------------------------------------------


def test_nodal_load_keeps_six_global_components() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(1.2, 0.9, y=-0.7)
    frame.member(a, b, alpha=0.37)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fx=1.0, fy=-2.0, fz=3.0, mx=4.0, my=-5.0, mz=6.0)
    (load,) = translate(frame).request.nodal_loads
    assert load.values == (1.0, -2.0, 3.0, 4.0, -5.0, 6.0)


def test_member_loads_in_local_wy_wz_wx() -> None:
    """Global → Q·q; local tal cual; ``-beamUniform`` (Wy, Wz, Wx) (hecho 9)."""
    frame = cantilever(Frame(), OBLIQUE, alpha=0.37)
    (member_id,) = frame.model.members
    axes = frame.model.axes(member_id)
    q = (300.0, -200.0, 500.0)
    qx, qy, qz = (Quantity.of(v, "N/m") for v in q)
    frame.model.add_member_uniform_load(frame.case_id, member_id, qx=qx, qy=qy, qz=qz)
    frame.model.add_member_uniform_load(
        frame.case_id, member_id, basis=LoadBasis.LOCAL, qx=qx, qy=qy, qz=qz
    )
    loads = translate(frame).request.beam_loads
    local = axes.to_local(Vec3(*q))
    assert sorted((w.wy, w.wz, w.wx) for w in loads) == sorted(
        [(local.y, local.z, local.x), (q[1], q[2], q[0])]
    )


def test_self_weight_projected_to_local() -> None:
    frame = cantilever(Frame(), OBLIQUE, alpha=0.37)
    frame.model.set_self_weight(frame.case_id)
    (member_id,) = frame.model.members
    weight = Vec3(*(c.si_value for c in frame.model.self_weight_load(member_id)))
    local = frame.model.axes(member_id).to_local(weight)
    (load,) = translate(frame).request.beam_loads
    assert (load.wy, load.wz, load.wx) == (local.y, local.z, local.x)
    # Dirección −Z con ρ·A·g calculado aparte (acero 7850 kg/m³, g estándar).
    back = frame.model.axes(member_id).to_global(Vec3(load.wx, load.wy, load.wz))
    rho_a_g = 7850.0 * AREA * 9.80665
    assert back.components() == pytest.approx((0.0, 0.0, -rho_a_g), rel=1e-12, abs=1e-9)


# --- Apoyos (D4) ----------------------------------------------------------------------


def oblique_basis() -> SupportBasis:
    return SupportBasis.inclined((0.3, -0.5, 0.81), (1.0, 0.0, 0.0))


def test_three_fixed_translations_in_oblique_basis_are_a_global_fix() -> None:
    pinned = tuple(d for d in SupportDof if pinned_restraints()[d.value].kind.value == "FIXED")
    request = supported_tip(oblique_basis(), fixed=pinned)
    fix = next(f for f in request.fixes if f.node == tip_tag(request))
    assert fix.fixity == (1, 1, 1, 0, 0, 0)
    assert (request.chains, request.springs) == ((), ())


def test_inclined_roller_in_xz_is_fix_plus_chain() -> None:
    """T-FEM-004 en 3D: Uy′ = Y fijo (fix global) y Uz′ según una normal en XZ (cadena)."""
    basis = inclined(30.0)
    request = supported_tip(basis, fixed=(SupportDof.UY, SupportDof.UZ))
    node = tip_tag(request)
    fix = next(f for f in request.fixes if f.node == node)
    assert fix.fixity == (0, 1, 0, 0, 0, 0)
    (chain,) = request.chains
    rows = basis.matrix()
    assert (chain.node, chain.normal, chain.tangent) == (node, rows[2], rows[0])
    assert chain.length == pytest.approx(math.hypot(*OBLIQUE), rel=1e-15)
    assert chain.stiffness == request.alpha_sp
    assert request.springs == ()


def test_slider_is_two_chains() -> None:
    basis = oblique_basis()
    request = supported_tip(basis, fixed=(SupportDof.UX, SupportDof.UY))
    rows = basis.matrix()
    assert [c.normal for c in request.chains] == [rows[0], rows[1]]
    assert [c.tangent for c in request.chains] == [rows[1], rows[2]]
    assert all(f.node != tip_tag(request) for f in request.fixes)


def test_oblique_springs_share_one_ground_and_the_support_basis() -> None:
    basis = oblique_basis()
    springs = {
        SupportDof.UX: Quantity.of(1e6, "N/m"),
        SupportDof.UZ: Quantity.of(2e7, "N/m"),
        SupportDof.RY: Quantity.of(3e5, "N*m/rad"),
    }
    request = supported_tip(basis, springs=springs)
    rows = basis.matrix()
    assert [(s.dof, s.stiffness, s.penalty) for s in request.springs] == [
        (1, 1e6, False),
        (3, 2e7, False),
        (5, 3e5, False),
    ]
    assert {s.ground for s in request.springs} == {request.springs[0].ground}
    assert all((s.axis_x, s.axis_yp) == (rows[0], rows[1]) for s in request.springs)
    assert all(s.node == tip_tag(request) for s in request.springs)


def test_partial_fixed_rotation_in_oblique_basis_uses_penalty() -> None:
    frame = cantilever(Frame(), OBLIQUE, alpha=0.37)
    tip = next(n for n in frame.model.nodes.values() if n.position.x != 0.0)
    frame.support(tip, build_restraints((SupportDof.RX,)), oblique_basis())
    translation = translate(frame, penalty_factor=1e7)
    (spring,) = translation.request.springs
    length = math.hypot(*OBLIQUE)
    scale = max(4 * E_PA * IY, 4 * E_PA * IZ, G_PA * J) / length
    assert (spring.dof, spring.penalty) == (4, True)
    assert spring.stiffness == pytest.approx(1e7 * scale, rel=1e-15)
    assert HYPOTHESIS_PENALTY in translation.hypotheses


def test_partial_fixed_rotations_in_global_basis_are_exact() -> None:
    request = supported_tip(SupportBasis.GLOBAL, fixed=(SupportDof.RX, SupportDof.RZ))
    fix = next(f for f in request.fixes if f.node == tip_tag(request))
    assert fix.fixity == (0, 0, 0, 1, 0, 1)
    assert request.springs == ()


@pytest.mark.parametrize(("tilt", "chains"), [(1e-12, 0), (1e-6, 1)])
def test_alignment_uses_tol_axis(tilt: float, chains: int) -> None:
    basis = SupportBasis.inclined((1.0, 0.0, tilt), (0.0, 0.0, 1.0))
    request = supported_tip(basis, fixed=(SupportDof.UX,))
    assert len(request.chains) == chains
    fixity = next((f.fixity for f in request.fixes if f.node == tip_tag(request)), None)
    assert fixity == ((1, 0, 0, 0, 0, 0) if chains == 0 else None)


def test_aligned_axis_sine_form() -> None:
    assert aligned_axis(Vec3(0.0, -1.0, 0.0), 1e-9) == 1
    assert aligned_axis(Vec3(1e-10, 0.0, 1.0), 1e-9) == 2
    assert aligned_axis(Vec3(0.6, 0.0, 0.8), 1e-9) is None


# --- Tags, determinismo y handler -----------------------------------------------------


def busy_frame() -> Frame:
    """Pórtico 3D con apoyos de todos los tipos de D4 en nudos distintos."""
    frame = Frame()
    nodes = [frame.node(*p) for p in ((0, 0), (0, 3), (4, 3), (4, 0), (4, 3, 3.0), (4, 0, 3.0))]
    for a, b in ((0, 1), (1, 2), (2, 3), (2, 4), (4, 5)):
        frame.member(nodes[a], nodes[b])
    frame.support(nodes[0], fixed_restraints())
    frame.support(nodes[3], build_restraints((SupportDof.UY, SupportDof.UZ)), inclined(30.0))
    frame.support(
        nodes[5],
        build_restraints(
            (SupportDof.UX, SupportDof.UY, SupportDof.RX),
            {SupportDof.UZ: Quantity.of(1e6, "N/m"), SupportDof.RZ: Quantity.of(1e4, "N*m/rad")},
        ),
        oblique_basis(),
    )
    frame.nodal(nodes[1], fx=1000.0, my=50.0)
    return frame


def test_tags_are_unique_and_one_fix_per_node() -> None:
    request = translate(busy_frame()).request
    assert request.chains
    assert request.springs
    domain = [n.tag for n in request.nodes]
    grounds = {s.ground for s in request.springs}
    chain_nodes = [t for c in request.chains for t in (c.c_node, c.r_node, c.ground)]
    all_nodes = [*domain, *grounds, *chain_nodes]
    assert len(all_nodes) == len(set(all_nodes))
    # Elementos; los resortes y cadenas reutilizan su tag como uniaxialMaterial.
    elements = [
        *(b.tag for b in request.beams),
        *(s.tag for s in request.springs),
        *(c.tag for c in request.chains),
    ]
    assert len(elements) == len(set(elements))
    fixed_nodes = [f.node for f in request.fixes]
    assert len(fixed_nodes) == len(set(fixed_nodes))
    assert set(fixed_nodes) <= set(domain)


def test_translation_is_deterministic_and_does_not_modify_model() -> None:
    frame = busy_frame()
    revision = frame.model.model_revision
    first = translate(frame).request.to_json()
    assert translate(frame).request.to_json() == first
    assert frame.model.model_revision == revision
    request = SpatialRequest.from_json(first)
    assert request.to_json() == first
    assert [n.entity_id for n in request.nodes] == sorted(n.entity_id for n in request.nodes)
    assert [n.tag for n in request.nodes] == list(range(1, len(request.nodes) + 1))
    assert [b.entity_id for b in request.beams] == sorted(b.entity_id for b in request.beams)


def test_no_constraint_handler_other_than_lagrange() -> None:
    """D3-A: la petición no elige handler y el worker solo llama a constraints("Lagrange")."""
    fields = {f.name for f in dataclasses.fields(SpatialRequest)}
    assert not {"handler", "constraints"} & fields
    tree = ast.parse(Path(opensees_worker_3d.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "constraints"
    ]
    assert len(calls) == 1
    first = calls[0].args[0]
    assert isinstance(first, ast.Constant)
    assert first.value == "Lagrange"


def test_rejects_wrong_argument_types() -> None:
    frame = cantilever(Frame())
    with pytest.raises(TypeError):
        translate_spatial(frame.model, frame.config())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        translate_spatial(object(), config(frame))  # type: ignore[arg-type]


def test_three_fixed_rotations_in_oblique_basis_are_a_global_fix() -> None:
    request = supported_tip(oblique_basis(), fixed=(SupportDof.RX, SupportDof.RY, SupportDof.RZ))
    fix = next(f for f in request.fixes if f.node == tip_tag(request))
    assert fix.fixity == (0, 0, 0, 1, 1, 1)
    assert request.springs == ()


def test_aligned_rotation_in_inclined_basis_is_exact() -> None:
    """inclined(30°): ey′ = +Y, así que Ry′ fijo es un fix global en Ry, sin penalización."""
    request = supported_tip(inclined(30.0), fixed=(SupportDof.RY,))
    fix = next(f for f in request.fixes if f.node == tip_tag(request))
    assert fix.fixity == (0, 0, 0, 0, 1, 0)
    assert request.springs == ()


def test_rotation_penalty_sums_bars_and_chain_length_is_longest_bar() -> None:
    frame = Frame()
    a, b, c = frame.node(0, 0), frame.node(1.2, 0.9, y=-0.7), frame.node(1.2, 4.9, y=-0.7)
    frame.member(a, b, alpha=0.37)
    frame.member(b, c)
    frame.support(a, fixed_restraints())
    frame.support(b, build_restraints((SupportDof.RX, SupportDof.UX)), oblique_basis())
    frame.nodal(c, fx=1.0)
    translation = translate(frame, penalty_factor=1e7)
    (spring,) = translation.request.springs
    scale = sum(
        max(4 * E_PA * IY, 4 * E_PA * IZ, G_PA * J) / length
        for length in (math.hypot(*OBLIQUE), 4.0)
    )
    assert spring.stiffness == pytest.approx(1e7 * scale, rel=1e-15)
    assert translation.chain_length == 4.0
    (chain,) = translation.request.chains
    assert chain.length == 4.0


def oblique_in_plan() -> SupportBasis:
    """Base girada en planta: ex′ y ey′ oblicuas en XY, ez′ = Z."""
    return SupportBasis.inclined((0.8, 0.6, 0.0), (0.0, 0.0, 1.0))


def test_prop1_two_fixed_rotations_with_aligned_free_axis_are_exact() -> None:
    """PROP-1: Rx′ y Ry′ fijos con ez′ = Z → fix exacto en Rx y Ry, sin penalización."""
    springs = {SupportDof.RZ: Quantity.of(1e4, "N*m/rad")}
    request = supported_tip(
        oblique_in_plan(), fixed=(SupportDof.RX, SupportDof.RY), springs=springs
    )
    fix = next(f for f in request.fixes if f.node == tip_tag(request))
    assert fix.fixity == (0, 0, 0, 1, 1, 0)
    (spring,) = request.springs  # El resorte del eje libre sigue como zeroLength exacto.
    assert (spring.dof, spring.penalty, spring.stiffness) == (6, False, 1e4)


def test_prop1_two_fixed_translations_with_aligned_free_axis_are_exact() -> None:
    request = supported_tip(oblique_in_plan(), fixed=(SupportDof.UX, SupportDof.UY))
    fix = next(f for f in request.fixes if f.node == tip_tag(request))
    assert fix.fixity == (1, 1, 0, 0, 0, 0)
    assert request.chains == ()


def test_prop1_does_not_apply_when_free_axis_is_oblique() -> None:
    request = supported_tip(oblique_basis(), fixed=(SupportDof.RX, SupportDof.RY))
    assert [(s.dof, s.penalty) for s in request.springs] == [(4, True), (5, True)]


@pytest.mark.parametrize("tol_axis", [1e-6, 1e-9])
def test_tol_axis_upper_bound_accepted(tol_axis: float) -> None:
    assert config(cantilever(Frame()), tol_axis=tol_axis).tol_axis == tol_axis


def test_tol_axis_above_bound_rejected() -> None:
    with pytest.raises(InputValueError) as info:
        config(cantilever(Frame()), tol_axis=2e-6)
    assert info.value.code is D.INPUT_OUT_OF_RANGE
