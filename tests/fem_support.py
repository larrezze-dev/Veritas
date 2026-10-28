"""Constructor de modelos planos XZ para las pruebas FEM (plan T-007)."""

import math
from collections.abc import Sequence
from uuid import UUID

import pytest

from veritas import fem
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.model import StructuralModel
from veritas.core.loads.load_case import LoadCategory
from veritas.core.materials.material import MaterialFamily
from veritas.core.supports.support import Restraints, SupportBasis
from veritas.core.units import Quantity
from veritas.fem.adapters.protocol import PlanarRequest, WorkerResponse
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.results.run import AnalysisRun

E_PA = 210e9
B, H = 0.2, 0.4
AREA = B * H
IY = B * H**3 / 12
IZ = H * B**3 / 12


class Frame:
    """Constructor de modelos planos XZ: acero E = 210 GPa, R 200×400 (A, Iy, Iz exactos)."""

    def __init__(self, family: MaterialFamily = MaterialFamily.STEEL) -> None:
        self.model = StructuralModel()
        self.material = self.model.add_material(
            name="Sintético",
            family=family,
            source="test",
            elastic_modulus=Quantity.of(E_PA, "Pa"),
            shear_modulus=Quantity.of(81e9, "Pa"),
            density=Quantity.of(7850, "kg/m³"),
        )
        self.section = self.model.add_rectangular_section(
            b=Quantity.of(B, "m"), h=Quantity.of(H, "m")
        )
        self.case = self.model.add_load_case("Q", LoadCategory.PERMANENT)

    def node(self, x: float, z: float, y: float = 0.0) -> Node:
        return self.model.add_node(Quantity.of(x, "m"), Quantity.of(y, "m"), Quantity.of(z, "m"))

    def member(
        self,
        a: Node,
        b: Node,
        *,
        alpha: float | None = None,
        reference: Sequence[float] | None = None,
    ) -> Member:
        return self.model.add_member(
            a.id,
            b.id,
            material=self.material.ref,
            section=self.section.ref,
            alpha=None if alpha is None else Quantity.of(alpha, "rad"),
            reference=reference,
        )

    def support(
        self, node: Node, restraints: Restraints, basis: SupportBasis = SupportBasis.GLOBAL
    ) -> None:
        self.model.set_support(node.id, restraints, basis=basis)

    def nodal(self, node: Node, **components: float) -> None:
        units = {"fx": "N", "fy": "N", "fz": "N", "mx": "N*m", "my": "N*m", "mz": "N*m"}
        self.model.add_nodal_load(
            self.case.id,
            node.id,
            **{key: Quantity.of(value, units[key]) for key, value in components.items()},
        )

    def config(self, **overrides: float) -> AnalysisConfiguration:
        return AnalysisConfiguration(self.case.id, **overrides)  # type: ignore[arg-type]

    @property
    def case_id(self) -> UUID:
        return self.case.id


def inclined(angle_deg: float) -> SupportBasis:
    """Base inclinada con x′ a ``angle_deg`` en el plano XZ y ey′ = +Y."""
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    return SupportBasis.inclined((c, 0.0, s), (-s, 0.0, c))


def solved_pair(
    frame: Frame, monkeypatch: pytest.MonkeyPatch, **overrides: float
) -> tuple[PlanarRequest, WorkerResponse, AnalysisConfiguration, AnalysisRun]:
    """Corrida real y la (petición, respuesta) que llega al control de equilibrio (T-017)."""
    captured: list[tuple[PlanarRequest, WorkerResponse]] = []
    original = fem.equilibrium_residual

    def spy(
        request: PlanarRequest, response: WorkerResponse, config: AnalysisConfiguration
    ) -> tuple[bool, float, float]:
        captured.append((request, response))
        return original(request, response, config)

    monkeypatch.setattr(fem, "equilibrium_residual", spy)
    config = frame.config(**overrides)
    run = fem.analyze_planar_xz(frame.model, config)
    monkeypatch.setattr(fem, "equilibrium_residual", original)
    assert len(captured) == 1, run.diagnostics
    return captured[0][0], captured[0][1], config, run
