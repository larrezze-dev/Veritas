"""Escena 3D derivada del modelo (plan T-011, E1): datos de dibujo sin Qt ni PyVista."""

import ast
import math
from pathlib import Path
from uuid import UUID

import pytest

import veritas.core.units as u
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.core.materials.material import MaterialFamily
from veritas.core.supports.support import (
    FREE,
    RestraintKind,
    fixed_restraints,
    pinned_restraints,
)
from veritas.gui.scene import DEFAULT_GLYPH_LENGTH, GLYPH_FRACTION, Scene, build_scene


def _m(value: float) -> u.Quantity:
    return u.Quantity.of(value, "m")


def _dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(p * q for p, q in zip(a, b, strict=True))


def _frame() -> tuple[StructuralModel, dict[str, UUID]]:
    """Pórtico plano en XZ: dos pilares de 3 m y un dintel de 4 m."""
    model = StructuralModel()
    steel = model.add_material(
        name="Acero",
        family=MaterialFamily.STEEL,
        source="prueba",
        elastic_modulus=u.Quantity.of(210_000, "N/mm²"),
        shear_modulus=u.Quantity.of(81_000, "N/mm²"),
        density=u.Quantity.of(7850, "kg/m³"),
    )
    section = model.add_rectangular_section(b=_m(0.2), h=_m(0.4), name="R 200×400")
    n1 = model.add_node(_m(0), _m(0), _m(0), label="N1")
    n2 = model.add_node(_m(0), _m(0), _m(3), label="N2")
    n3 = model.add_node(_m(4), _m(0), _m(3), label="N3")
    n4 = model.add_node(_m(4), _m(0), _m(0), label="N4")
    p1 = model.add_member(n1.id, n2.id, material=steel.ref, section=section.ref, label="P1")
    dintel = model.add_member(n2.id, n3.id, material=steel.ref, section=section.ref, label="D")
    p2 = model.add_member(n3.id, n4.id, material=steel.ref, section=section.ref, label="P2")
    model.set_support(n1.id, fixed_restraints(), label="A1")
    model.set_support(n4.id, pinned_restraints(), label="A2")
    ids = {
        "n1": n1.id,
        "n2": n2.id,
        "n3": n3.id,
        "n4": n4.id,
        "p1": p1.id,
        "d": dintel.id,
        "p2": p2.id,
    }
    return model, ids


@pytest.fixture
def frame() -> tuple[Scene, StructuralModel, dict[str, UUID]]:
    model, ids = _frame()
    return build_scene(model.snapshot()), model, ids


def test_nodes_keep_si_coordinates_and_order(
    frame: tuple[Scene, StructuralModel, dict[str, UUID]],
) -> None:
    scene, _, _ = frame
    assert scene.points == (
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 3.0),
        (4.0, 0.0, 3.0),
        (4.0, 0.0, 0.0),
    )
    assert [node.label for node in scene.nodes] == ["N1", "N2", "N3", "N4"]


def test_segments_join_the_node_indices_of_each_member(
    frame: tuple[Scene, StructuralModel, dict[str, UUID]],
) -> None:
    scene, _, ids = frame
    assert scene.segments == ((0, 1), (1, 2), (2, 3))
    assert scene.node_index[ids["n3"]] == 2
    assert scene.member_index[ids["d"]] == 1


def test_member_length_and_midpoint_are_geometric(
    frame: tuple[Scene, StructuralModel, dict[str, UUID]],
) -> None:
    scene, _, _ = frame
    assert [member.length for member in scene.members] == pytest.approx([3.0, 4.0, 3.0])
    assert scene.members[1].midpoint == (2.0, 0.0, 3.0)


def test_local_axes_are_those_of_the_domain_and_orthonormal(
    frame: tuple[Scene, StructuralModel, dict[str, UUID]],
) -> None:
    scene, model, ids = frame
    for key in ("p1", "d", "p2"):
        origin, ex, ey, ez = scene.axes_of(ids[key])
        axes = model.axes(ids[key])
        assert ex == (axes.ex.x, axes.ex.y, axes.ex.z)
        assert ey == (axes.ey.x, axes.ey.y, axes.ey.z)
        assert ez == (axes.ez.x, axes.ez.y, axes.ez.z)
        assert _dot(ex, ex) == pytest.approx(1.0)
        assert _dot(ex, ey) == pytest.approx(0.0, abs=1e-12)
        assert _dot(ex, ez) == pytest.approx(0.0, abs=1e-12)
        assert origin == scene.members[scene.member_index[ids[key]]].midpoint


def test_horizontal_member_axes_follow_the_default_reference(
    frame: tuple[Scene, StructuralModel, dict[str, UUID]],
) -> None:
    # Dintel a lo largo de +X: ez local = +Z global (ADR-001 §1), ey = ez × ex = +Y.
    scene, _, ids = frame
    _, ex, ey, ez = scene.axes_of(ids["d"])
    assert ex == pytest.approx((1.0, 0.0, 0.0))
    assert ez == pytest.approx((0.0, 0.0, 1.0))
    assert ey == pytest.approx((0.0, 1.0, 0.0))


def test_supports_report_fixed_translations_and_rotations(
    frame: tuple[Scene, StructuralModel, dict[str, UUID]],
) -> None:
    scene, _, ids = frame
    fixed, pinned = scene.supports
    assert (fixed.label, fixed.node) == ("A1", scene.node_index[ids["n1"]])
    assert (fixed.translations_fixed, fixed.rotations_fixed) == (3, 3)
    assert (pinned.translations_fixed, pinned.rotations_fixed) == (3, 0)
    assert pinned.restraints[3:] == (RestraintKind.FREE,) * 3
    assert fixed.global_basis
    assert pinned.global_basis
    assert FREE.kind is RestraintKind.FREE


def test_bounds_and_glyph_length_scale_with_the_model(
    frame: tuple[Scene, StructuralModel, dict[str, UUID]],
) -> None:
    scene, _, _ = frame
    assert scene.bounds == ((0.0, 0.0, 0.0), (4.0, 0.0, 3.0))
    assert scene.glyph_length == pytest.approx(GLYPH_FRACTION * 5.0)


def test_empty_model_gives_an_empty_scene() -> None:
    scene = build_scene(StructuralModel().snapshot())
    assert (scene.nodes, scene.members, scene.supports) == ((), (), ())
    assert scene.points == ()
    assert scene.segments == ()
    assert scene.bounds == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert scene.glyph_length == DEFAULT_GLYPH_LENGTH


def test_single_node_uses_the_default_glyph_length() -> None:
    model = StructuralModel()
    model.add_node(_m(1), _m(2), _m(3))
    scene = build_scene(model.snapshot())
    assert scene.points == ((1.0, 2.0, 3.0),)
    assert scene.glyph_length == DEFAULT_GLYPH_LENGTH


def test_scene_does_not_depend_on_the_model_afterwards(
    frame: tuple[Scene, StructuralModel, dict[str, UUID]],
) -> None:
    # Proyección: mover un nudo no cambia una escena ya construida; una nueva sí.
    scene, model, ids = frame
    model.move_node(ids["n3"], _m(5), _m(0), _m(3))
    assert scene.points[2] == (4.0, 0.0, 3.0)
    assert build_scene(model.snapshot()).points[2] == (5.0, 0.0, 3.0)
    assert math.isclose(build_scene(model.snapshot()).members[1].length, 5.0)


def test_member_with_a_missing_node_is_rejected() -> None:
    model, ids = _frame()
    snapshot = model.snapshot()
    broken = ModelSnapshot(
        nodes=tuple(n for n in snapshot.nodes if n.id != ids["n3"]),
        members=snapshot.members,
        materials=snapshot.materials,
        sections=snapshot.sections,
        supports=snapshot.supports,
    )
    with pytest.raises(ValueError, match="nudo inexistente"):
        build_scene(broken)


def test_support_with_a_missing_node_is_rejected() -> None:
    model, ids = _frame()
    snapshot = model.snapshot()
    broken = ModelSnapshot(
        nodes=tuple(n for n in snapshot.nodes if n.id != ids["n1"]),
        members=tuple(m for m in snapshot.members if ids["n1"] not in (m.node_i, m.node_j)),
        materials=snapshot.materials,
        sections=snapshot.sections,
        supports=snapshot.supports,
    )
    with pytest.raises(ValueError, match="nudo inexistente"):
        build_scene(broken)


def test_scene_module_imports_neither_qt_nor_pyvista() -> None:
    path = Path(__file__).resolve().parents[1] / "src" / "veritas" / "gui" / "scene.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert not roots & {"PySide6", "shiboken6", "pyvista", "pyvistaqt", "vtk", "vtkmodules"}
    assert not roots & {"openseespy", "numpy"}
