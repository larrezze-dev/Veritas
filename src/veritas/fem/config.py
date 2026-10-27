"""Configuración de una corrida de análisis (plan T-007, D7).

Snapshot inmutable: se registra completo en la procedencia de la corrida.
"""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import Location
from veritas.core.validation import invalid_type, positive_float

DEFAULT_TIMEOUT_S: Final = 60.0
DEFAULT_MAX_FREE_DOFS: Final = 300


@dataclass(frozen=True, slots=True)
class AnalysisConfiguration:
    """Parámetros de una corrida plana XZ. Todas las tolerancias son > 0.

    - ``tol_plane`` (m): |Y| admisible de un nudo.
    - ``tol_axis``: pruebas en forma seno de la alineación de barras y bases de apoyo.
    - ``tol_member_load_y``: residuo relativo |q_Y|/|q| de una carga de barra LOCAL.
    - ``tol_pivot``: pivote mínimo de la matriz escalada (salvaguarda de singularidad).
    - ``penalty_factor``: k_pen = factor · max diag K de una traslación oblicua rígida.
    - ``tol_equilibrium``: control relativo ΣR + ΣP = 0.
    """

    load_case_id: UUID
    timeout_s: float = DEFAULT_TIMEOUT_S
    tol_plane: float = 1e-9
    tol_axis: float = 1e-9
    tol_member_load_y: float = 1e-9
    tol_pivot: float = 1e-12
    penalty_factor: float = 1e8
    tol_equilibrium: float = 1e-9
    max_free_dofs: int = DEFAULT_MAX_FREE_DOFS

    def __post_init__(self) -> None:
        if not isinstance(self.load_case_id, UUID):
            raise invalid_type("load_case_id", "UUID", self.load_case_id)
        for name in (
            "timeout_s",
            "tol_plane",
            "tol_axis",
            "tol_member_load_y",
            "tol_pivot",
            "penalty_factor",
            "tol_equilibrium",
        ):
            value = positive_float(getattr(self, name), name, Location(field=name))
            object.__setattr__(self, name, value)
        if isinstance(self.max_free_dofs, bool) or not isinstance(self.max_free_dofs, int):
            raise invalid_type("max_free_dofs", "entero", self.max_free_dofs)
        positive_float(float(self.max_free_dofs), "max_free_dofs", Location(field="max_free_dofs"))

    def snapshot(self) -> dict[str, str | float | int]:
        """Valores exactos para la procedencia."""
        return {
            "load_case_id": str(self.load_case_id),
            "timeout_s": self.timeout_s,
            "tol_plane": self.tol_plane,
            "tol_axis": self.tol_axis,
            "tol_member_load_y": self.tol_member_load_y,
            "tol_pivot": self.tol_pivot,
            "penalty_factor": self.penalty_factor,
            "tol_equilibrium": self.tol_equilibrium,
            "max_free_dofs": self.max_free_dofs,
        }
