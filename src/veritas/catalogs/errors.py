"""Errores del catálogo: diagnósticos con claves ``catalog.*`` (ADR-009; plan T-022, D7/D9).

Se reutilizan los códigos de ADR-001 §5; no se añade ninguno. ``CatalogError`` es un
``InputValueError`` (por tanto ``ValueError`` y ``DiagnosticError``); ``CatalogFileError``
distingue los fallos del archivo SQLite (inexistente, ajeno, ilegible, de versión no soportada)
de los de contenido, para que la frontera (CLI de E2) los lleve al código de salida 2.

Asignación de códigos:

- ``REFERENCE_NOT_FOUND``: catálogo o perfil inexistente, archivo inexistente.
- ``UNSUPPORTED_CONFIGURATION``: catálogo protegido, nombre ocupado, archivo existente o ajeno,
  versión o migración no soportada, almacén cerrado, error de la base de datos.
- ``REVISION_CONFLICT``: clave natural repetida en un mismo lote.
- ``INPUT_OUT_OF_RANGE`` / ``INPUT_INVALID_TYPE`` / ``UNITS_INCOMPATIBLE``: contenido inválido.
"""

from collections.abc import Mapping

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputValueError,
    Location,
    ParamValue,
)


class CatalogError(InputValueError):
    """Error de contenido u operación del catálogo, con diagnóstico ``catalog.*``."""


class CatalogFileError(CatalogError):
    """El archivo del catálogo no se puede abrir, crear o usar."""


def catalog_error(
    code: DiagnosticCode,
    key: str,
    params: Mapping[str, ParamValue] | None = None,
    location: Location | None = None,
    *,
    cause: str | None = None,
) -> CatalogError:
    """``CatalogError`` con el diagnóstico dado."""
    return CatalogError(Diagnostic.create(code, key, params, location=location, cause=cause))


def file_error(
    code: DiagnosticCode,
    key: str,
    params: Mapping[str, ParamValue] | None = None,
    *,
    cause: str | None = None,
) -> CatalogFileError:
    """``CatalogFileError`` con el diagnóstico dado."""
    return CatalogFileError(Diagnostic.create(code, key, params, cause=cause))
