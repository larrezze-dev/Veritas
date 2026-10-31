"""Codificación canónica y hash del modelo analítico (plan T-012, D4; ADR-006)."""

import hashlib
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from persistence_support import deterministic_ids, kn, m, platform_independent, rich_model
from veritas.core import units as u
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.load_case import LoadCategory
from veritas.core.loads.loads import SelfWeightMode
from veritas.core.supports.support import SupportDof, build_restraints
from veritas.persistence.canonical import CANON_VERSION, canonical_bytes, model_hash

# Valor fijo del modelo de ``persistence_support.rich_model`` con UUID deterministas. Si cambia,
# cambió la codificación o el contenido analítico: no se actualiza sin un ADR (ADR-006).
PINNED_HASH = "6f66cf6a3450e28625ac78b541078257e710e1d3719762d2da1514caa70aff8c"


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> StructuralModel:
    deterministic_ids(monkeypatch)
    return rich_model()


def h(model: StructuralModel) -> str:
    return model_hash(model.snapshot())


def test_canon_version() -> None:
    assert CANON_VERSION == "veritas-canon/1"


def test_empty_model_hash_matches_hand_written_canonical_text() -> None:
    text = (
        '{"canon":"veritas-canon/1","model":{"load_cases":[],"loads":[],"materials":[],'
        '"members":[],"nodes":[],"sections":[],"supports":[]}}'
    )
    assert h(StructuralModel()) == hashlib.sha256(text.encode()).hexdigest()


def test_pinned_hash_value(model: StructuralModel) -> None:
    assert model_hash(platform_independent(model.snapshot())) == PINNED_HASH


def test_hash_is_stable_across_processes() -> None:
    code = (
        "import sys; sys.path.insert(0, 'tests');"
        "import pytest\n"
        "from persistence_support import deterministic_ids, platform_independent, rich_model\n"
        "from veritas.persistence.canonical import model_hash\n"
        "mp = pytest.MonkeyPatch(); deterministic_ids(mp)\n"
        "print(model_hash(platform_independent(rich_model().snapshot())))"
    )
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=root
    )
    assert out.stdout.strip() == PINNED_HASH


def test_labels_and_presentation_do_not_change_the_hash(model: StructuralModel) -> None:
    before = h(model)
    node = next(iter(model.nodes.values()))
    model.relabel_node(node.id, "Otro nombre")
    support = next(iter(model.supports.values()))
    model.relabel_support(support.node_id, "Otra etiqueta")
    case = next(iter(model.load_cases.values()))
    model.revise_load_case(case.id, name="Renombrado", description="Nueva descripción")
    assert h(model) == before


def test_insertion_order_does_not_change_the_hash(model: StructuralModel) -> None:
    snapshot = model.snapshot()
    shuffled = replace(
        snapshot,
        nodes=tuple(reversed(snapshot.nodes)),
        members=tuple(reversed(snapshot.members)),
        loads=tuple(reversed(snapshot.loads)),
        supports=tuple(reversed(snapshot.supports)),
    )
    assert model_hash(shuffled) == model_hash(snapshot)


def test_model_revision_is_not_part_of_the_hash(model: StructuralModel) -> None:
    snapshot = model.snapshot()
    assert model_hash(replace(snapshot, model_revision=999)) == model_hash(snapshot)


def test_negative_zero_and_zero_give_the_same_hash(model: StructuralModel) -> None:
    snapshot = model.snapshot()
    flipped = tuple(
        replace(n, position=Vec3(n.position.x, n.position.y, 0.0))
        if str(n.position.z) == "-0.0"
        else n
        for n in snapshot.nodes
    )
    assert flipped != snapshot.nodes or any(str(n.position.z) == "-0.0" for n in snapshot.nodes)
    assert repr(flipped) != repr(snapshot.nodes)  # son distintos en la forma exacta
    assert model_hash(replace(snapshot, nodes=flipped)) == model_hash(snapshot)


def test_unreferenced_revisions_are_not_part_of_the_hash(model: StructuralModel) -> None:
    before = h(model)
    section = next(iter(model.snapshot().sections))
    model.revise_rectangular_section(section.id, b=m(0.3))  # nadie la referencia
    assert h(model) == before


@pytest.mark.parametrize(
    "change",
    [
        "move_node",
        "assign_section",
        "assign_material",
        "end_spring_value",
        "end_state",
        "orientation",
        "support_restraint",
        "support_removed",
        "nodal_load",
        "load_removed",
        "excluded_members",
        "self_weight_mode",
        "case_category",
    ],
)
def test_any_analytical_change_changes_the_hash(  # noqa: PLR0912 (un caso por cambio)
    model: StructuralModel, change: str
) -> None:
    before = h(model)
    members = {mm.label: mm for mm in model.members.values()}
    nodes = {n.label: n for n in model.nodes.values()}
    case_q = next(c for c in model.load_cases.values() if c.name == "Q")
    case_g = next(c for c in model.load_cases.values() if c.name == "G")
    if change == "move_node":
        node = nodes["N1"]
        model.move_node(node.id, m(0.001), m(0), m(0))
    elif change == "assign_section":
        sec = model.revise_rectangular_section(members["B1"].section.entity_id, h=m(0.45))
        model.assign_section(members["B1"].id, sec.ref)
    elif change == "assign_material":
        mat = model.revise_material(
            members["B1"].material.entity_id, elastic_modulus=u.Quantity.of(200_000, "N/mm²")
        )
        model.assign_material(members["B1"].id, mat.ref)
    elif change == "end_spring_value":
        spring = EndConnection.semi_rigid(Dof.RY, u.Quantity(2.6e6, Dof.RY.stiffness_dimension))
        model.set_member_end(members["B1"].id, MemberEnd.J, spring)
    elif change == "end_state":
        model.set_member_end(
            members["B1"].id,
            MemberEnd.I,
            EndConnection.rigid().with_dof(Dof.RZ, DofState.RELEASED),
        )
    elif change == "orientation":
        model.set_member_orientation(members["B1"].id, alpha=u.Quantity.of(10, "deg"))
    elif change == "support_restraint":
        model.set_support(
            nodes["N4"].id,
            build_restraints(
                fixed=[SupportDof.UZ], springs={SupportDof.UX: u.Quantity.of(6e6, "N/m")}
            ),
            basis=model.support_at(nodes["N4"].id).basis,  # type: ignore[union-attr]
        )
    elif change == "support_removed":
        model.remove_support(nodes["N3"].id)
    elif change == "nodal_load":
        model.add_nodal_load(case_q.id, nodes["N3"].id, fy=kn(1))
    elif change == "load_removed":
        load = next(iter(model.loads_in_case(case_q.id)))
        model.remove_load(load.id)
    elif change == "excluded_members":
        model.set_self_weight(
            case_g.id,
            mode=SelfWeightMode.SPECIFIC_WEIGHT,
            excluded_members=[members["B2"].id],
        )
    elif change == "self_weight_mode":
        model.set_self_weight(case_g.id, mode=SelfWeightMode.MASS_DENSITY)
    else:
        model.revise_load_case(case_q.id, category=LoadCategory.ACCIDENTAL)
    assert h(model) != before


def test_canonical_bytes_rules() -> None:
    assert canonical_bytes({"b": [1, 2.5], "a": None}) == b'{"a":null,"b":[1,2.5]}'
    assert canonical_bytes({"x": -0.0}) == b'{"x":-0.0}'
    assert canonical_bytes({"x": -0.0}, normalize_zero=True) == b'{"x":0.0}'
    assert canonical_bytes({"x": "ñ"}) == '{"x":"ñ"}'.encode()
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(DiagnosticError) as info:
            canonical_bytes({"x": bad})
        assert info.value.code is DiagnosticCode.INPUT_NONFINITE
    with pytest.raises(TypeError):
        canonical_bytes({"x": object()})
