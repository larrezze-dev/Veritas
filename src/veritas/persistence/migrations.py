"""Marco mínimo de migraciones del esquema de proyecto (plan T-012, D7-A; DAT-002).

Cada migración transforma el CONTENIDO (``content``) de la versión N a la N+1 en memoria,
después de verificar la integridad del archivo tal como se guardó y antes de validarlo. El
esquema inicial fue la v1; la v2 (T-012 E2) añade las ejecuciones guardadas y la v3 (T-014 E2), las
normativas de usuario y la seleccionada. El registro es un
argumento para poder probar la cadena con migraciones sintéticas.
"""

from collections.abc import Callable, Mapping
from typing import Final

from veritas.core.diagnostics import DiagnosticCode
from veritas.persistence.errors import format_error, value_error

CURRENT_SCHEMA_VERSION: Final = 4

type Migration = Callable[[dict[str, object]], dict[str, object]]


def _v1_to_v2(content: dict[str, object]) -> dict[str, object]:
    """v2 (T-012 E2) añade ``runs``: un proyecto de la v1 no tiene ejecuciones guardadas."""
    if "runs" in content:  # Un contenido v1 no puede traerlo: no se pisa en silencio.
        raise format_error("project.unknown_fields", {"fields": "runs"}, path="content")
    return {**content, "runs": []}


def _v2_to_v3(content: dict[str, object]) -> dict[str, object]:
    """v3 (T-014 E2) añade ``norms``: un proyecto de la v2 no tiene normativas propias."""
    if "norms" in content:  # Un contenido v2 no puede traerlo: no se pisa en silencio.
        raise format_error("project.unknown_fields", {"fields": "norms"}, path="content")
    return {**content, "norms": {"user": [], "selected": None}}


def _v3_to_v4(content: dict[str, object]) -> dict[str, object]:
    """v4 (T-024 E1, ADR-010) admite secciones de catálogo; la forma del contenido v3 no cambia.

    Un archivo v3 solo puede tener secciones rectangulares: una sección con otra familia no es
    de esa versión y se rechaza en vez de reinterpretarse en silencio.
    """
    model = content.get("model")
    sections = model.get("sections") if isinstance(model, dict) else None
    if isinstance(sections, list):
        for index, section in enumerate(sections):
            family = section.get("family") if isinstance(section, dict) else None
            if family != "RECTANGULAR":
                raise format_error(
                    "project.invalid_field",
                    {"expected": "RECTANGULAR (esquema v3)"},
                    path=f"content.model.sections[{index}].family",
                )
    return dict(content)


MIGRATIONS: Final[dict[int, Migration]] = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4}
"""Migraciones reales registradas, ``N -> N+1``."""


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
