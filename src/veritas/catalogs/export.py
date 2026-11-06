"""Exportación de perfiles del catálogo (plan T-023, E2; SEC-009 y NFR-012).

Funciones puras que producen el texto de los archivos; la escritura la hace la CLI con
``fileio.write_files``. Sin ``sqlite3`` ni capas superiores.

- **CSV** en el formato nativo del importador (columnas con el nombre del campo, unidades SI,
  coma como separador y punto decimal): se reimporta sin mapeo. Los números van con ``repr`` de
  ``float`` (ida y vuelta exacta) y una propiedad ausente es una celda vacía, nunca 0. Como el
  importador rechaza columnas desconocidas, la procedencia y el aviso de redistribución no
  pueden ir dentro: acompañan en ``<archivo>.procedencia.json`` (con el SHA-256 del CSV).
- **JSON** ``veritas-catalog-export/1``: el catálogo (fuente, licencia, edición) y, por perfil,
  el documento completo con identidad, revisión, estado y método de cada propiedad y su hash.
  Es de intercambio y archivo; el importador no lo lee.

Solo se exporta la última revisión de cada perfil.
"""

import csv
import io
import json
from collections.abc import Sequence
from typing import Final

from veritas.catalogs.columns import DIMENSION_PREFIX, PROPERTY_FIELDS
from veritas.catalogs.model import (
    CatalogInfo,
    CatalogProfile,
    profile_document,
    profile_sha256,
    sha256_of,
)

EXPORT_FORMAT: Final = "veritas-catalog-export/1"
PROVENANCE_FORMAT: Final = "veritas-catalog-export-provenance/1"
PROVENANCE_SUFFIX: Final = ".procedencia.json"
TEXT_COLUMNS: Final = ("series", "designation", "family")


def redistribution_warning(license_text: str) -> str:
    """Aviso de redistribución (NFR-012): la licencia de los datos no la cambia la exportación."""
    return (
        f"Los datos de este catálogo se rigen por su licencia «{license_text}»: compruebe que "
        "puede redistribuirlos antes de compartir este archivo. Las propiedades importadas no "
        "están verificadas (IMPORTADO_SIN_VERIFICAR). Aplicación experimental."
    )


def _catalog_document(info: CatalogInfo, cloned_from: CatalogInfo | None) -> dict[str, object]:
    return {
        "id": str(info.id),
        "name": info.name,
        "description": info.description,
        "source": info.source,
        "license": info.license,
        "edition": info.edition,
        "protected": info.protected,
        "cloned_from": None if cloned_from is None else cloned_from.name,
    }


def dimension_names(profiles: Sequence[CatalogProfile]) -> list[str]:
    """Unión ordenada de los nombres de dimensión de los perfiles."""
    return sorted({d.name for p in profiles for d in p.data.dimensions})


def csv_text(profiles: Sequence[CatalogProfile]) -> str:
    """CSV nativo (SI) de los perfiles, en el orden recibido; reimportable sin mapeo."""
    names = dimension_names(profiles)
    # T-028: las columnas comerciales solo si algún perfil las declara (CSV antiguo intacto).
    commercial = [
        column
        for column, present in (
            ("manufacturer", any(p.data.manufacturer is not None for p in profiles)),
            ("compatible_materials", any(p.data.compatible_materials for p in profiles)),
        )
        if present
    ]
    header = [
        *TEXT_COLUMNS,
        *commercial,
        *PROPERTY_FIELDS,
        *(f"{DIMENSION_PREFIX}{n}" for n in names),
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for profile in profiles:
        dims = {d.name: d.value for d in profile.data.dimensions}
        cells: list[str] = [profile.series, profile.designation, profile.family.value]
        if "manufacturer" in commercial:
            cells.append(profile.data.manufacturer or "")
        if "compatible_materials" in commercial:
            cells.append(";".join(profile.data.compatible_materials))
        for field in PROPERTY_FIELDS:
            value = getattr(profile.properties, field).value
            cells.append("" if value is None else repr(float(value)))
        cells.extend("" if n not in dims else repr(float(dims[n])) for n in names)
        writer.writerow(cells)
    return buffer.getvalue()


def json_text(
    info: CatalogInfo,
    profiles: Sequence[CatalogProfile],
    *,
    exported_at: str,
    cloned_from: CatalogInfo | None = None,
) -> str:
    """Exportación JSON completa de un catálogo (última revisión de cada perfil)."""
    document = {
        "format": EXPORT_FORMAT,
        "exported_at": exported_at,
        "warning": redistribution_warning(info.license),
        "catalog": _catalog_document(info, cloned_from),
        "profiles": [{**profile_document(p), "sha256": profile_sha256(p)} for p in profiles],
    }
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def provenance_text(  # noqa: PLR0913 (datos de la procedencia por nombre)
    info: CatalogInfo,
    *,
    file_name: str,
    content: str,
    rows: int,
    exported_at: str,
    cloned_from: CatalogInfo | None = None,
) -> str:
    """Archivo acompañante del CSV: catálogo, licencia, aviso y huella del CSV exportado."""
    document = {
        "format": PROVENANCE_FORMAT,
        "exported_at": exported_at,
        "warning": redistribution_warning(info.license),
        "catalog": _catalog_document(info, cloned_from),
        "file": {"name": file_name, "sha256": sha256_of(content), "rows": rows},
    }
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
