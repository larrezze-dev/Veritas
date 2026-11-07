"""T-030 E2a (R2-D6): selección múltiple sin Qt."""

from uuid import UUID

import pytest

from fem_support import Frame
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.editing import ModelEditor
from veritas.core.geometry.selection_logic import (
    Selection,
    SelectionKind,
    SelectionSet,
    members_at_node,
    members_by_material,
    members_by_section,
)
from veritas.gui import selection as gui_selection

N, MB = SelectionKind.NODE, SelectionKind.MEMBER


def sel(kind: SelectionKind, n: int) -> Selection:
    return Selection(kind, UUID(int=n))


def test_gui_module_reexports_the_core_names() -> None:
    assert gui_selection.SelectionSet is SelectionSet
    assert gui_selection.Selection is Selection
    assert gui_selection.SelectionKind is SelectionKind


def test_replace_add_remove_and_toggle_return_new_sets() -> None:
    empty = SelectionSet()
    assert not empty
    assert len(empty) == 0
    assert empty.single is None
    one = empty.add(sel(N, 1))
    assert one is not empty
    assert not empty
    assert one.single == sel(N, 1)
    both = one.add(sel(MB, 1), sel(N, 1))  # UUID igual pero tipo distinto: son distintos
    assert len(both) == 2
    assert sel(N, 1) in both
    assert sel(MB, 1) in both
    assert both.single is None
    assert both.replace(sel(MB, 2)).items() == (sel(MB, 2),)
    assert both.remove(sel(N, 1)).items() == (sel(MB, 1),)
    assert both.remove(sel(N, 99)) == both
    toggled = both.toggle(sel(N, 1), sel(MB, 3), sel(MB, 3))  # repetido: una sola vez
    assert sel(N, 1) not in toggled
    assert sel(MB, 3) in toggled
    assert sel(MB, 1) in toggled
    assert "no" not in both


def test_selection_compatibility_with_the_simple_selection() -> None:
    assert SelectionSet.from_selection(None) == SelectionSet()
    assert SelectionSet.from_selection(sel(MB, 5)).single == sel(MB, 5)
    assert SelectionSet.of([sel(N, 1), sel(N, 2)]).single is None


def test_prune_drops_what_the_editor_removed() -> None:
    frame = Frame()
    a, b, c = frame.node(0, 0), frame.node(3, 0), frame.node(6, 0)
    ab, bc = frame.member(a, b), frame.member(b, c)
    editor = ModelEditor(frame.model)
    selection = SelectionSet.of(
        [Selection(N, a.id), Selection(N, c.id), Selection(MB, ab.id), Selection(MB, bc.id)]
    )
    editor.remove_members([bc.id])
    editor.remove_node(c.id)
    pruned = selection.pruned(editor.model)
    assert set(pruned.items()) == {Selection(N, a.id), Selection(MB, ab.id)}
    editor.undo()
    editor.undo()
    assert selection.pruned(editor.model) == selection  # se recupera lo que vuelve


def test_selection_by_attributes() -> None:
    frame = Frame()
    a, b, c = frame.node(0, 0), frame.node(3, 0), frame.node(6, 0)
    ab, bc = frame.member(a, b), frame.member(b, c)
    other = frame.model.add_rectangular_section(
        b=u.Quantity.of(0.1, "m"), h=u.Quantity.of(0.1, "m")
    )
    frame.model.assign_section(bc.id, other.ref)
    model = frame.model
    assert members_by_section(model, other.ref).members == frozenset({bc.id})
    assert members_by_section(model, frame.section.ref).members == frozenset({ab.id})
    assert members_by_material(model, frame.material.ref).members == frozenset({ab.id, bc.id})
    assert members_at_node(model, b.id).members == frozenset({ab.id, bc.id})
    assert members_at_node(model, a.id).members == frozenset({ab.id})
    with pytest.raises(DiagnosticError):
        members_at_node(model, UUID(int=9))
