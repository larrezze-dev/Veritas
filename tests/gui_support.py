"""Ayudas de las pruebas de la GUI (plan T-011, E2).

``frame_model`` es un pórtico XZ de dos pilares y un dintel con un empotramiento y un apoyo
articulado. ``require_gui`` fija la plataforma Qt (``offscreen`` salvo que ya esté definida) y
omite el módulo si falta el extra opcional ``gui``.
"""

import os
from uuid import UUID

import pytest

import veritas.core.units as u
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily
from veritas.core.supports.support import fixed_restraints, pinned_restraints


def _m(value: float) -> u.Quantity:
    return u.Quantity.of(value, "m")


def frame_model() -> tuple[StructuralModel, dict[str, UUID]]:
    """Pórtico plano en XZ: pilares P1 y P2 de 3 m y dintel D de 4 m; apoyos A1 (empotrado)
    y A2 (articulado)."""
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


def require_gui() -> None:
    """Plataforma Qt offscreen por defecto y omisión del módulo si falta el extra ``gui``."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyvista")
    pytest.importorskip("pyvistaqt")
