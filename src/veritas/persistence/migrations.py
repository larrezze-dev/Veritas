"""Marco mínimo de migraciones del esquema de proyecto (plan T-012, D7-A; DAT-002).

Cada migración transforma el CONTENIDO (``content``) de la versión N a la N+1 en memoria,
después de verificar la integridad del archivo tal como se guardó y antes de validarlo. El
esquema inicial es la v1: no existen migraciones reales. El registro es un argumento para
poder probar la cadena con migraciones sintéticas.
"""

from collections.abc import Callable, Mapping
from typing import Final

from veritas.core.diagnostics import DiagnosticCode
from veritas.persistence.errors import value_error

CURRENT_SCHEMA_VERSION: Final = 1

type Migration = Callable[[dict[str, object]], dict[str, object]]

MIGRATIONS: Final[dict[int, Migration]] = {}
"""Migraciones reales registradas, ``N -> N+1``. Vacío en la v1."""


def migrate(
    content: dict[str, object],
    version: object,
    *,
    target: int = CURRENT_SCHEMA_VERSION,
    registry: Mapping[int, Migration] | None = None,
) -> dict[str, object]:
    """Lleva ``content`` de ``version`` a ``target`` aplicando la cadena de migraciones."""
    steps = MIGRATIONS if registry is None else registry
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise value_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "project.invalid_version", {"found": repr(version)}
        )
    if version > target:
        raise value_error(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            "project.unsupported_version",
            {"found": version, "supported": target},
        )
    current = content
    for step in range(version, target):
        migration = steps.get(step)
        if migration is None:
            raise value_error(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "project.migration_gap",
                {"step": step, "next": step + 1},
            )
        current = migration(current)
    return current
