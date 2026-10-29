"""Configuración de una corrida espacial (plan T-018, D6).

Snapshot inmutable: se registra completo en la procedencia de la corrida. Los valores por
defecto de ``tol_pivot``, ``penalty_factor`` y ``tol_penalty`` están calibrados en E1 con
márgenes medidos (``plans/T-018.md``); cualquier cambio es una desviación.
"""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import Location
from veritas.core.validation import invalid_type, out_of_range, positive_float

DEFAULT_TIMEOUT_S: Final = 60.0
DEFAULT_MAX_FREE_DOFS: Final = 300
# Cota de tol_axis (seno): muy por debajo de 1/√2, así dos filas ortonormales nunca se
# alinean con el mismo eje global y el ajuste de eje queda acotado (aprobado 2026-10-28).
MAX_TOL_AXIS: Final = 1e-6


@dataclass(frozen=True, slots=True)
class SpatialAnalysisConfiguration:
    """Parámetros de una corrida espacial de seis GDL por nudo. Todas las tolerancias son > 0.

    - ``tol_axis``: prueba en forma seno de la alineación de un eje de la base de apoyo con
      un eje global (D4); ≤ ``MAX_TOL_AXIS``.
    - ``tol_pivot``: pivote mínimo de la matriz escalada (salvaguarda de singularidad).
    - ``penalty_factor``: k_pen de un giro fijo parcial en base oblicua (D4-A) = factor ·
      Σ máx(4EIy/L, 4EIz/L, GJ/L) de las barras del nudo. Calibrado con ``tol_penalty`` en
      la batería con penalización (margen ≈ 47×); con el estimador de D6, en la práctica
      ``penalty_factor`` ≲ 4,5e9 (plan T-018).
    - ``tol_penalty``: tolerancia de las corridas con penalización (D6); no se aplica a las
      corridas exactas.
    - ``tol_equilibrium``: control relativo ΣR + ΣP = 0.
    - ``max_free_dofs``: GDL libres admitidos, incluidos los de nudos auxiliares y de cadena.
    """

    load_case_id: UUID
    timeout_s: float = DEFAULT_TIMEOUT_S
    tol_axis: float = 1e-9
    tol_pivot: float = 1e-12
    penalty_factor: float = 1e8
    tol_penalty: float = 1e-6
    tol_equilibrium: float = 1e-9
    max_free_dofs: int = DEFAULT_MAX_FREE_DOFS

    def __post_init__(self) -> None:
        if not isinstance(self.load_case_id, UUID):
            raise invalid_type("load_case_id", "UUID", self.load_case_id)
        for name in (
            "timeout_s",
            "tol_axis",
            "tol_pivot",
            "penalty_factor",
            "tol_penalty",
            "tol_equilibrium",
        ):
            value = positive_float(getattr(self, name), name, Location(field=name))
            object.__setattr__(self, name, value)
        if self.tol_axis > MAX_TOL_AXIS:
            raise out_of_range(
                "tol_axis", self.tol_axis, f"<= {MAX_TOL_AXIS}", Location(field="tol_axis")
            )
        if isinstance(self.max_free_dofs, bool) or not isinstance(self.max_free_dofs, int):
            raise invalid_type("max_free_dofs", "entero", self.max_free_dofs)
        positive_float(float(self.max_free_dofs), "max_free_dofs", Location(field="max_free_dofs"))

    def snapshot(self) -> dict[str, str | float | int]:
        """Valores exactos para la procedencia."""
        return {
            "load_case_id": str(self.load_case_id),
            "timeout_s": self.timeout_s,
            "tol_axis": self.tol_axis,
            "tol_pivot": self.tol_pivot,
            "penalty_factor": self.penalty_factor,
            "tol_penalty": self.tol_penalty,
            "tol_equilibrium": self.tol_equilibrium,
            "max_free_dofs": self.max_free_dofs,
        }
