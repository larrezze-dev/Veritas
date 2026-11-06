"""Marco mínimo de migraciones del esquema de proyecto (plan T-012, D7-A; DAT-002).

Cada migración transforma el CONTENIDO (``content``) de la versión N a la N+1 en memoria,
después de verificar la integridad del archivo tal como se guardó y antes de validarlo. El
esquema inicial fue la v1; la v2 (T-012 E2) añade las ejecuciones guardadas y la v3 (T-014 E2), las
normativas de usuario y la seleccionada; la v4 (T-024) admite secciones de catálogo y la v5 (T-025),
perfiles paramétricos y el grupo de cortante y la v6 (T-028), el fabricante y los materiales
compatibles del snapshot de catálogo. El registro es un argumento para poder probar la
cadena con migraciones sintéticas.
"""

from collections.abc import Callable, Mapping
from typing import Final

from veritas.core.diagnostics import DiagnosticCode
from veritas.persistence.errors import format_error, value_error

CURRENT_SCHEMA_VERSION: Final = 8

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


def _v4_to_v5(content: dict[str, object]) -> dict[str, object]:
    """v5 (T-025 E1, ADR-011) admite perfiles paramétricos y el grupo ``shear`` en secciones.

    Solo VERIFICA: la forma de una sección v4 no cambia (el hash del modelo tampoco). Un archivo
    v4 solo puede tener secciones rectangulares o de catálogo y sin ``shear``; lo que no sea de
    esa versión se rechaza en vez de reinterpretarse en silencio.
    """
    model = content.get("model")
    sections = model.get("sections") if isinstance(model, dict) else None
    if isinstance(sections, list):
        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            path = f"content.model.sections[{index}]"
            if section.get("family") not in ("RECTANGULAR", "CATALOGO"):
                raise format_error(
                    "project.invalid_field",
                    {"expected": "RECTANGULAR o CATALOGO (esquema v4)"},
                    path=f"{path}.family",
                )
            if "shear" in section:
                raise format_error("project.unknown_fields", {"fields": "shear"}, path=path)
    return dict(content)


def _v5_to_v6(content: dict[str, object]) -> dict[str, object]:
    """v6 (T-028, ADR-012) admite ``manufacturer`` y ``compatible_materials`` en el snapshot.

    Solo VERIFICA: un archivo v5 no puede traer esas claves (no se reinterpretan en silencio) y
    su forma, y por tanto el hash del modelo, no cambia: «sin declarar» es lo que significa su
    ausencia.
    """
    model = content.get("model")
    sections = model.get("sections") if isinstance(model, dict) else None
    if isinstance(sections, list):
        for index, section in enumerate(sections):
            params = section.get("parameters") if isinstance(section, dict) else None
            if not isinstance(params, dict) or section.get("family") != "CATALOGO":
                continue
            found = [k for k in ("manufacturer", "compatible_materials") if k in params]
            if found:
                raise format_error(
                    "project.unknown_fields",
                    {"fields": ", ".join(found)},
                    path=f"content.model.sections[{index}].parameters",
                )
    return dict(content)


def _v6_to_v7(content: dict[str, object]) -> dict[str, object]:
    """v7 (T-029, ADR-013) admite la familia ``FREEFORM`` (croquis libre poligonal).

    Solo VERIFICA: un archivo v6 no puede traer esa familia (no se reinterpreta en silencio) y su
    forma, y por tanto el hash del modelo, no cambia.
    """
    model = content.get("model")
    sections = model.get("sections") if isinstance(model, dict) else None
    if isinstance(sections, list):
        for index, section in enumerate(sections):
            if isinstance(section, dict) and section.get("family") == "FREEFORM":
                raise format_error(
                    "project.invalid_field",
                    {"expected": "RECTANGULAR, CATALOGO o PARAMETRICA (esquema v6)"},
                    path=f"content.model.sections[{index}].family",
                )
    return dict(content)


def _v7_to_v8(content: dict[str, object]) -> dict[str, object]:
    """v8 (T-031, TK-032) admite ``shape`` (forma declarada) en los parámetros de ``FREEFORM``.

    Solo VERIFICA: un archivo v7 no puede traer esa clave (no se reinterpreta en silencio) y su
    forma, y por tanto el hash del modelo, no cambia: «sin declarar» es lo que significa su
    ausencia.
    """
    model = content.get("model")
    sections = model.get("sections") if isinstance(model, dict) else None
    if isinstance(sections, list):
        for index, section in enumerate(sections):
            params = section.get("parameters") if isinstance(section, dict) else None
            if (
                isinstance(params, dict)
                and section.get("family") == "FREEFORM"
                and "shape" in params
            ):
                raise format_error(
                    "project.unknown_fields",
                    {"fields": "shape"},
                    path=f"content.model.sections[{index}].parameters",
                )
    return dict(content)


MIGRATIONS: Final[dict[int, Migration]] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
    3: _v3_to_v4,
    4: _v4_to_v5,
    5: _v5_to_v6,
    6: _v6_to_v7,
    7: _v7_to_v8,
}
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
