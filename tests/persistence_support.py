"""Modelo de referencia con todas las entidades y UUID deterministas (pruebas de T-012)."""

from dataclasses import replace
from itertools import count
from uuid import UUID

import pytest

from veritas.core import units as u
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.core.loads.load_case import LoadCategory
from veritas.core.loads.loads import LoadBasis, SelfWeightMode
from veritas.core.materials.material import MaterialFamily
from veritas.core.supports.support import (
    SupportBasis,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
)
from veritas.core.supports.support import SupportDof as SDof


def deterministic_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """UUID v4 válidos y reproducibles en el orden de creación."""
    counter = count(1)

    def fake() -> UUID:
        return UUID(int=next(counter) | (4 << 76) | (0b10 << 62))

    monkeypatch.setattr("veritas.core.geometry.model.new_entity_id", fake)


# J (Saint-Venant) de las dos secciones de ``rich_model`` como literales: el cálculo usa
# ``math.exp``, cuyo último ulp puede variar entre plataformas (Windows y Linux); el hash fijo
# usa estos valores para no depender de la libm (la comprobación de 1e-12 los acepta).
_TORSION_LITERALS = {1: 0.0007317813667826264, 2: 0.0009974602988327458}


def platform_independent(snapshot: ModelSnapshot) -> ModelSnapshot:
    """Copia del snapshot con la constante de torsión de cada sección como literal."""
    sections = tuple(
        replace(
            s,
            properties=replace(
                s.properties,
                torsion_constant=replace(
                    s.properties.torsion_constant, value=_TORSION_LITERALS[s.revision]
                ),
            ),
        )
        for s in snapshot.sections
    )
    return replace(snapshot, sections=sections)


def m(value: float) -> u.Quantity:
    return u.Quantity.of(value, "m")


def kn(value: float) -> u.Quantity:
    return u.Quantity.of(value, "kN")


def rich_model() -> StructuralModel:
    """Modelo con todas las entidades persistibles (llamar tras ``deterministic_ids``)."""
    model = StructuralModel()
    steel = model.add_material(
        name="Acero",
        family=MaterialFamily.STEEL,
        source="CTE DB-SE-A §4.2",
        standard_edition="2019",
        elastic_modulus=u.Quantity.of(210_000, "N/mm²"),
        shear_modulus=u.Quantity.of(81_000, "N/mm²"),
        poisson_ratio=0.3,
        density=u.Quantity.of(7850, "kg/m³"),
        specific_weight=u.Quantity.of(78.5, "kN/m³"),
    )
    steel2 = model.revise_material(steel.id, name="Acero S275")  # revisión 2
    model.add_material(  # Sin barras: se guarda, pero no entra en el hash.
        name="Madera",
        family=MaterialFamily.TIMBER,
        source="CTE DB-SE-M",
        elastic_modulus=u.Quantity.of(11_000, "N/mm²"),
        shear_modulus=u.Quantity.of(690, "N/mm²"),
        density=u.Quantity.of(420, "kg/m³"),
    )
    aluminium = model.add_material(
        name="Aluminio",
        family=MaterialFamily.ALUMINIUM,
        source="FEM",
        elastic_modulus=u.Quantity.of(70_000, "N/mm²"),
        shear_modulus=u.Quantity.of(27_000, "N/mm²"),
        density=u.Quantity.of(2700, "kg/m³"),
    )
    sec = model.add_rectangular_section(b=m(0.2), h=m(0.4), name="R 200×400")
    sec2 = model.revise_rectangular_section(sec.id, h=m(0.5), name="R 200×500")
    n1 = model.add_node(m(0), m(0), m(0), label="N1")
    n2 = model.add_node(m(3), m(0), m(0), label="N2")
    n3 = model.add_node(m(3), m(2), m(-0.0), label="N3")
    n4 = model.add_node(m(0), m(2), m(2), label="N4")
    # B1 conserva la revisión 1 de material y sección; B2 usa las revisiones 2.
    b1 = model.add_member(n1.id, n2.id, material=steel.ref, section=sec.ref, label="B1")
    b2 = model.add_member(
        n2.id, n3.id, material=steel2.ref, section=sec2.ref, reference=(0, 0, 1), label="B2"
    )
    b3 = model.add_member(
        n3.id, n4.id, material=aluminium.ref, section=sec2.ref, alpha=u.Quantity.of(15, "deg")
    )
    spring = EndConnection.semi_rigid(Dof.RY, u.Quantity(2.5e6, Dof.RY.stiffness_dimension))
    model.set_member_end(b1.id, MemberEnd.J, spring)
    release = (
        EndConnection.rigid()
        .with_dof(Dof.RY, DofState.RELEASED)
        .with_dof(Dof.RZ, DofState.RELEASED)
        .with_dof(Dof.UX, DofState.SPRING, u.Quantity(1e8, Dof.UX.stiffness_dimension))
    )
    model.set_member_end(b2.id, MemberEnd.I, release)
    model.set_support(n1.id, fixed_restraints(), label="Empotramiento")
    model.set_support(
        n4.id,
        build_restraints(fixed=[SDof.UZ], springs={SDof.UX: u.Quantity.of(5e6, "N/m")}),
        basis=SupportBasis.inclined((1, 1, 0), (0, 0, 1)),
        label="Rodillo inclinado con resorte",
    )
    model.set_support(n3.id, pinned_restraints())
    g = model.add_load_case("G", LoadCategory.PERMANENT, description="Permanentes")
    q = model.add_load_case("Q", LoadCategory.VARIABLE)
    model.set_self_weight(g.id, mode=SelfWeightMode.SPECIFIC_WEIGHT, excluded_members=[b3.id])
    model.add_nodal_load(q.id, n2.id, fx=u.Quantity.of(-0.0, "kN"), fz=kn(-12.5))
    model.add_member_uniform_load(q.id, b1.id, basis=LoadBasis.LOCAL, qz=u.Quantity.of(-5, "kN/m"))
    model.add_member_point_load(q.id, b2.id, position=0.25, fz=kn(-3), my=u.Quantity.of(2, "kN·m"))
    model.add_member_distributed_load(
        q.id,
        b3.id,
        start=0.1,
        end=0.9,
        qz_start=u.Quantity.of(-1, "kN/m"),
        qz_end=u.Quantity.of(-4, "kN/m"),
    )
    model.add_member_distributed_moment(
        q.id, b1.id, start=0.0, end=0.5, mx_start=kn(1.5), mx_end=kn(2.5)
    )
    model.add_support_settlement(q.id, n1.id, uz=u.Quantity.of(-5, "mm"))
    model.relabel_node(n2.id, "Nudo 2")  # revisión de nudo 2 sin cambiar model_revision
    return model
