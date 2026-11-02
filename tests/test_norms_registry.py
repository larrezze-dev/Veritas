"""Registro de configuraciones de normativas: crear, editar, copiar, borrar (T-014 E1)."""

import math
from itertools import count
from uuid import UUID

import pytest

from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.references import RevisionRef
from veritas.design_codes.builtin import CTE_PROTECTED, CTE_PROTECTED_ID
from veritas.design_codes.norms import NormConfiguration, NormKind, NormParameter, NormSystem
from veritas.design_codes.registry import NormRegistry


def make_ids() -> "count[int]":
    return count(1)


def registry(guard: object = None) -> NormRegistry:
    counter = make_ids()

    def factory() -> UUID:
        return UUID(int=next(counter) | (4 << 76) | (0b10 << 62))

    return NormRegistry(id_factory=factory, selected_guard=guard)  # type: ignore[arg-type]


def param(key: str = "k1", value: float = 1.5, source: str = "Fuente 1") -> NormParameter:
    return NormParameter(key=key, value=value, dimension=u.STRESS, unit="MPa", source=source)


def create(reg: NormRegistry, name: str = "Mía", **kw: object) -> NormConfiguration:
    args: dict[str, object] = {
        "name": name,
        "system": NormSystem.OTHER,
        "jurisdiction": "Prueba",
        "edition": "2020",
    }
    args.update(kw)
    return reg.create(**args)  # type: ignore[arg-type]


def code_of(action: object) -> DiagnosticCode:
    assert callable(action)
    with pytest.raises(DiagnosticError) as info:
        action()
    return info.value.code


def key_of(action: object) -> str:
    assert callable(action)
    with pytest.raises(DiagnosticError) as info:
        action()
    return info.value.diagnostic.message_key


# --- Estado inicial ----------------------------------------------------------------------


def test_new_registry_contains_only_the_protected_configuration() -> None:
    reg = registry()
    assert reg.all_configurations() == (CTE_PROTECTED,)
    assert reg.user_configurations() == ()
    assert reg.latest(CTE_PROTECTED_ID) == CTE_PROTECTED
    assert reg.get(RevisionRef(CTE_PROTECTED_ID, 1)) == CTE_PROTECTED
    assert reg.is_protected(CTE_PROTECTED_ID)


# --- Crear -------------------------------------------------------------------------------


def test_create_makes_user_revision_one_with_new_id() -> None:
    reg = registry()
    c = create(reg, documents=("Doc A",), description="d", parameters=(param(),))
    assert c.revision == 1
    assert c.kind is NormKind.USER
    assert c.derived_from is None
    assert c.id != CTE_PROTECTED_ID
    assert c.parameters == (param(),)
    assert reg.latest(c.id) == c
    assert reg.user_configurations() == (c,)
    assert not reg.is_protected(c.id)


def test_create_validates_via_the_domain() -> None:
    reg = registry()
    assert code_of(lambda: create(reg, name="")) is DiagnosticCode.INPUT_INVALID_TYPE
    assert code_of(lambda: create(reg, edition="  ")) is DiagnosticCode.INPUT_INVALID_TYPE
    assert (
        code_of(lambda: create(reg, parameters=(param(), param())))
        is DiagnosticCode.INPUT_OUT_OF_RANGE
    )
    assert reg.user_configurations() == ()


def test_create_cannot_produce_a_protected_configuration() -> None:
    reg = registry()
    c = create(reg)
    assert c.kind is NormKind.USER


def test_failed_create_does_not_consume_visible_state() -> None:
    reg = registry()
    code_of(lambda: create(reg, name=""))
    assert reg.all_configurations() == (CTE_PROTECTED,)


# --- Editar ------------------------------------------------------------------------------


def test_edit_creates_next_revision_and_keeps_previous_untouched() -> None:
    reg = registry()
    first = create(reg, parameters=(param(),))
    before = repr(first)
    second = reg.edit(first.id, name="Nueva", parameters=(param(value=2.5),))
    assert second.revision == 2
    assert second.name == "Nueva"
    assert second.id == first.id
    assert second.kind is NormKind.USER
    assert second.system is first.system
    assert second.derived_from == first.derived_from
    assert reg.get(RevisionRef(first.id, 1)) is first
    assert repr(reg.get(RevisionRef(first.id, 1))) == before
    assert reg.history(first.id) == (first, second)
    assert reg.latest(first.id) == second


def test_edit_with_identical_content_makes_no_new_revision() -> None:
    reg = registry()
    first = create(reg, documents=("A",), parameters=(param(),))
    same = reg.edit(first.id, name=first.name, documents=("A",), parameters=(param(),))
    assert same is first
    assert reg.edit(first.id) is first
    assert len(reg.history(first.id)) == 1


def test_edit_negative_zero_is_a_change() -> None:
    reg = registry()
    first = create(reg, parameters=(param(value=0.0),))
    second = reg.edit(first.id, parameters=(param(value=-0.0),))
    assert second.revision == 2
    assert math.copysign(1.0, second.parameters[0].value) == -1.0
    assert math.copysign(1.0, first.parameters[0].value) == 1.0


def test_edit_can_remove_all_parameters_and_documents() -> None:
    reg = registry()
    first = create(reg, documents=("A",), parameters=(param(),))
    second = reg.edit(first.id, documents=(), parameters=())
    assert second.documents == ()
    assert second.parameters == ()
    assert first.parameters == (param(),)


def test_edit_revisions_accumulate() -> None:
    reg = registry()
    c = create(reg)
    for n in range(2, 6):
        c = reg.edit(c.id, description=f"v{n}")
        assert c.revision == n
    assert [r.description for r in reg.history(c.id)] == ["", "v2", "v3", "v4", "v5"]


def test_invalid_edit_leaves_registry_unchanged() -> None:
    reg = registry()
    first = create(reg)
    assert code_of(lambda: reg.edit(first.id, name="")) is DiagnosticCode.INPUT_INVALID_TYPE
    assert (
        code_of(lambda: reg.edit(first.id, parameters=(param(), param())))
        is DiagnosticCode.INPUT_OUT_OF_RANGE
    )
    assert reg.history(first.id) == (first,)


def test_edit_protected_is_rejected_and_changes_nothing() -> None:
    reg = registry()
    assert code_of(lambda: reg.edit(CTE_PROTECTED_ID, name="Otro")) is (
        DiagnosticCode.UNSUPPORTED_CONFIGURATION
    )
    assert key_of(lambda: reg.edit(CTE_PROTECTED_ID, name="Otro")) == "norm.protected"
    assert key_of(lambda: reg.edit(CTE_PROTECTED_ID)) == "norm.protected"
    assert reg.history(CTE_PROTECTED_ID) == (CTE_PROTECTED,)


def test_edit_unknown_id_is_reference_not_found() -> None:
    reg = registry()
    missing = UUID(int=99 | (4 << 76) | (0b10 << 62))
    assert code_of(lambda: reg.edit(missing, name="x")) is DiagnosticCode.REFERENCE_NOT_FOUND


# --- Copiar ------------------------------------------------------------------------------


def test_copy_user_configuration_exact_revision() -> None:
    reg = registry()
    first = create(reg, documents=("A",), parameters=(param(),))
    second = reg.edit(first.id, parameters=(param(value=9.0),))
    copy = reg.copy(RevisionRef(first.id, 1), name="Copia")
    assert copy.id not in {first.id, CTE_PROTECTED_ID}
    assert copy.revision == 1
    assert copy.kind is NormKind.USER
    assert copy.derived_from == RevisionRef(first.id, 1)
    assert copy.parameters == first.parameters
    assert copy.parameters != second.parameters
    assert copy.documents == first.documents
    assert copy.system is first.system
    assert copy.name == "Copia"
    assert reg.latest(copy.id) == copy


def test_copy_protected_gives_editable_user_configuration() -> None:
    reg = registry()
    copy = reg.copy(CTE_PROTECTED.ref, name="Mi CTE")
    assert copy.kind is NormKind.USER
    assert copy.system is NormSystem.CTE
    assert copy.derived_from == RevisionRef(CTE_PROTECTED_ID, 1)
    assert copy.documents == CTE_PROTECTED.documents
    assert copy.edition == CTE_PROTECTED.edition
    assert copy.parameters == ()
    edited = reg.edit(copy.id, edition="2019", parameters=(param(source="DB-SE-A 4.2"),))
    assert edited.revision == 2
    assert edited.derived_from == RevisionRef(CTE_PROTECTED_ID, 1)
    assert reg.history(CTE_PROTECTED_ID) == (CTE_PROTECTED,)


def test_copy_requires_valid_name_and_existing_reference() -> None:
    reg = registry()
    assert code_of(lambda: reg.copy(CTE_PROTECTED.ref, name="")) is (
        DiagnosticCode.INPUT_INVALID_TYPE
    )
    assert code_of(lambda: reg.copy(RevisionRef(CTE_PROTECTED_ID, 2), name="x")) is (
        DiagnosticCode.REFERENCE_NOT_FOUND
    )
    assert code_of(lambda: reg.copy(CTE_PROTECTED_ID, name="x")) is (  # type: ignore[arg-type]
        DiagnosticCode.INPUT_INVALID_TYPE
    )
    assert reg.user_configurations() == ()


def test_copy_of_a_copy_keeps_the_immediate_origin() -> None:
    reg = registry()
    a = create(reg)
    b = reg.copy(a.ref, name="b")
    c = reg.copy(b.ref, name="c")
    assert c.derived_from == b.ref


# --- Borrar ------------------------------------------------------------------------------


def test_delete_removes_all_revisions_and_reads_fail_afterwards() -> None:
    reg = registry()
    first = create(reg)
    reg.edit(first.id, name="Dos")
    removed = reg.delete(first.id)
    assert removed == 2
    assert reg.user_configurations() == ()
    assert code_of(lambda: reg.latest(first.id)) is DiagnosticCode.REFERENCE_NOT_FOUND
    assert code_of(lambda: reg.get(RevisionRef(first.id, 1))) is DiagnosticCode.REFERENCE_NOT_FOUND
    assert code_of(lambda: reg.history(first.id)) is DiagnosticCode.REFERENCE_NOT_FOUND
    assert RevisionRef(first.id, 1) not in reg
    assert code_of(lambda: reg.delete(first.id)) is DiagnosticCode.REFERENCE_NOT_FOUND


def test_delete_does_not_recycle_ids() -> None:
    counter = count(1)
    emitted: list[UUID] = []

    def factory() -> UUID:
        # Primero repite el id borrado; luego da ids frescos.
        n = next(counter)
        value = UUID(int=(1 if n == 2 else n) | (4 << 76) | (0b10 << 62))
        emitted.append(value)
        return value

    reg = NormRegistry(id_factory=factory)
    first = create(reg)
    reg.delete(first.id)
    second = create(reg, name="Otra")
    assert second.id != first.id
    assert first.id in emitted
    assert code_of(lambda: reg.latest(first.id)) is DiagnosticCode.REFERENCE_NOT_FOUND


def test_delete_protected_is_rejected_and_changes_nothing() -> None:
    reg = registry()
    assert code_of(lambda: reg.delete(CTE_PROTECTED_ID)) is (
        DiagnosticCode.UNSUPPORTED_CONFIGURATION
    )
    assert key_of(lambda: reg.delete(CTE_PROTECTED_ID)) == "norm.protected"
    assert reg.all_configurations() == (CTE_PROTECTED,)


def test_delete_selected_configuration_is_rejected() -> None:
    selected: set[UUID] = set()
    reg = registry(guard=lambda entity_id: entity_id in selected)
    c = create(reg)
    selected.add(c.id)
    assert code_of(lambda: reg.delete(c.id)) is DiagnosticCode.UNSUPPORTED_CONFIGURATION
    assert key_of(lambda: reg.delete(c.id)) == "norm.in_use"
    assert reg.latest(c.id) == c
    selected.clear()
    assert reg.delete(c.id) == 1


def test_selected_guard_does_not_block_edit_or_copy() -> None:
    reg = registry(guard=lambda entity_id: True)
    c = create(reg)
    assert reg.edit(c.id, name="x").revision == 2
    assert reg.copy(c.ref, name="y").revision == 1


def test_delete_origin_of_a_copy_is_allowed_and_copy_survives() -> None:
    reg = registry()
    origin = create(reg)
    copy = reg.copy(origin.ref, name="Copia")
    reg.delete(origin.id)
    assert reg.latest(copy.id) == copy
    assert reg.latest(copy.id).derived_from == origin.ref
    assert code_of(lambda: reg.get(origin.ref)) is DiagnosticCode.REFERENCE_NOT_FOUND


def test_delete_one_leaves_others_and_protected_untouched() -> None:
    reg = registry()
    a = create(reg, name="A")
    b = create(reg, name="B")
    reg.delete(a.id)
    assert reg.user_configurations() == (b,)
    assert reg.latest(CTE_PROTECTED_ID) == CTE_PROTECTED


def test_registries_are_independent() -> None:
    one, two = registry(), registry()
    create(one)
    assert two.user_configurations() == ()
