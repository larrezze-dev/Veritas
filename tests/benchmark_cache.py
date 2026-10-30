"""Referencias espaciales y contraste cruzado, una vez por sesión de pytest (revisión de E1).

Cada variante lanza una corrida del worker; varios módulos las usan. Las funciones tienen la
firma de ``benchmarks`` para sustituirlas con ``monkeypatch`` en las pruebas de la CLI. Solo
se reutiliza la corrida con el tiempo límite por defecto (``None`` o el de la CLI); cualquier
otro llega a ``benchmarks`` sin caché, también uno no válido.
"""

from functools import cache

from veritas.fem import benchmarks
from veritas.fem.spatial.config import DEFAULT_TIMEOUT_S

_RUN_SPATIAL_REFERENCE = benchmarks.run_spatial_reference
_RUN_CROSS_PLANAR = benchmarks.run_cross_planar


@cache
def _spatial_reference(case_id: str) -> tuple[benchmarks.SpatialReferenceOutcome, ...]:
    return _RUN_SPATIAL_REFERENCE(case_id)


@cache
def _cross_planar(case_id: str) -> tuple[benchmarks.SpatialReferenceOutcome, ...]:
    return _RUN_CROSS_PLANAR(case_id)


def run_spatial_reference(
    case_id: str, timeout_s: float | None = None
) -> tuple[benchmarks.SpatialReferenceOutcome, ...]:
    if timeout_s not in (None, DEFAULT_TIMEOUT_S):
        return _RUN_SPATIAL_REFERENCE(case_id, timeout_s)
    return _spatial_reference(case_id)


def run_cross_planar(
    case_id: str, timeout_s: float | None = None
) -> tuple[benchmarks.SpatialReferenceOutcome, ...]:
    if timeout_s not in (None, DEFAULT_TIMEOUT_S):
        return _RUN_CROSS_PLANAR(case_id, timeout_s)
    return _cross_planar(case_id)


_RUN_SETTLEMENT_BATTERY = benchmarks.run_settlement_battery


@cache
def _settlement_battery() -> tuple[
    benchmarks.ReferenceOutcome | benchmarks.SpatialReferenceOutcome, ...
]:
    return _RUN_SETTLEMENT_BATTERY()


def run_settlement_battery(
    timeout_s: float | None = None,
) -> tuple[benchmarks.ReferenceOutcome | benchmarks.SpatialReferenceOutcome, ...]:
    """Batería T-ASI (plan T-016, E2.3), una vez por sesión con el tiempo límite por defecto."""
    if timeout_s not in (None, DEFAULT_TIMEOUT_S):
        return _RUN_SETTLEMENT_BATTERY(timeout_s)
    return _settlement_battery()
