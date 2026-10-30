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
# Penalización iterada de extremo (ADR-004 §2-§3; hecho 23 del plan T-018).
DEFAULT_TOL_PENALTY: Final = 1e-6
DEFAULT_END_PENALTY_TOLERANCE: Final = 1e-10
DEFAULT_END_PENALTY_MAX_ITERATIONS: Final = 20


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
    - ``end_penalty_factor``: k de un resorte de penalización de extremo (E2-a) = factor ·
      escala de E2-a del maestro; resuelto por penalización iterada (ADR-004 §2). No rige
      los apoyos D4, que usan ``penalty_factor``.
    - ``end_penalty_tolerance``: convergencia de la iteración de extremo (ADR-004 §3);
      0 < ``end_penalty_tolerance`` < ``tol_penalty``.
    - ``end_penalty_max_iterations``: iteraciones máximas; agotarlas es FAILED.
    - ``tol_equilibrium``: control relativo ΣR + ΣP = 0.
    - ``max_free_dofs``: GDL libres admitidos, incluidos los de nudos auxiliares y de cadena.
    """

    load_case_id: UUID
    timeout_s: float = DEFAULT_TIMEOUT_S
    tol_axis: float = 1e-9
    tol_pivot: float = 1e-12
    penalty_factor: float = 1e8
    tol_penalty: float = DEFAULT_TOL_PENALTY
    tol_equilibrium: float = 1e-9
    max_free_dofs: int = DEFAULT_MAX_FREE_DOFS
    end_penalty_factor: float = 1e1
    end_penalty_tolerance: float = DEFAULT_END_PENALTY_TOLERANCE
    end_penalty_max_iterations: int = DEFAULT_END_PENALTY_MAX_ITERATIONS

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
            "end_penalty_factor",
            "end_penalty_tolerance",
        ):
            value = positive_float(getattr(self, name), name, Location(field=name))
            object.__setattr__(self, name, value)
        if self.tol_axis > MAX_TOL_AXIS:
            raise out_of_range(
                "tol_axis", self.tol_axis, f"<= {MAX_TOL_AXIS}", Location(field="tol_axis")
            )
        if self.end_penalty_tolerance >= self.tol_penalty:
            raise out_of_range(
                "end_penalty_tolerance",
                self.end_penalty_tolerance,
                f"< tol_penalty = {self.tol_penalty}",
                Location(field="end_penalty_tolerance"),
            )
        for name in ("max_free_dofs", "end_penalty_max_iterations"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise invalid_type(name, "entero", value)
            positive_float(float(value), name, Location(field=name))

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
            "end_penalty_factor": self.end_penalty_factor,
            "end_penalty_tolerance": self.end_penalty_tolerance,
            "end_penalty_max_iterations": self.end_penalty_max_iterations,
        }
