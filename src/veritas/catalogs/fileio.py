"""Escritura atómica de archivos de salida del catálogo (plan T-023, E2).

Sin ``sqlite3`` ni dependencias de capas superiores. Usada por los archivos de usuario
(favoritos y recientes) y por la exportación: se escribe todo en archivos temporales contiguos y
solo entonces se publican con ``os.replace``, de modo que un fallo antes de publicar no deja
archivos a medias ni toca los existentes.
"""

import contextlib
import os
from collections.abc import Sequence
from pathlib import Path

from veritas.catalogs.errors import CatalogFileError, file_error
from veritas.core.diagnostics import DiagnosticCode


def output_error(path: Path, error: OSError) -> CatalogFileError:
    return file_error(
        DiagnosticCode.UNSUPPORTED_CONFIGURATION,
        "catalog.output_error",
        {"path": str(path)},
        cause=f"{type(error).__name__}: {error}",
    )


def write_files(files: Sequence[tuple[Path, bytes]], *, overwrite: bool) -> None:
    """Escribe todos los archivos o ninguno (salvo un fallo del propio sistema al publicar).

    Sin ``overwrite`` no se escribe nada si alguno de los destinos ya existe
    (``catalog.output_exists``). Los temporales se retiran siempre.
    """
    if not overwrite:
        for target, _ in files:
            if target.exists():
                raise file_error(
                    DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                    "catalog.output_exists",
                    {"path": str(target)},
                )
    temporaries: list[tuple[Path, Path]] = []
    try:
        for target, content in files:
            temporary = target.with_name(f"{target.name}.writing")
            temporaries.append((temporary, target))
            temporary.write_bytes(content)
        for temporary, target in temporaries:
            os.replace(temporary, target)
    except OSError as error:
        raise output_error(target, error) from error
    finally:
        for temporary, _ in temporaries:
            with contextlib.suppress(OSError):
                temporary.unlink()
