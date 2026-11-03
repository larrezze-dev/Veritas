"""Favoritos y perfiles recientes del usuario (plan T-023, E2; D8).

Viven en un archivo JSON **aparte** (``veritas-catalog-user/1``), nunca en el catálogo: un
catálogo protegido rechaza toda escritura y los favoritos deben poder guardarse igualmente. No
hay estado oculto: la ruta se indica siempre (``--usuario RUTA``).

- La clave de un perfil es (id del catálogo, serie, designación): sigue a la última revisión y
  no se confunde con otro catálogo del mismo nombre. El nombre del catálogo se guarda solo para
  mostrarlo.
- **Recientes**: el último consultado primero, sin repetidos, como máximo ``MAX_RECENT``.
- Un archivo ausente es un usuario vacío. Uno ilegible, ajeno o inconsistente da un
  ``CatalogFileError`` (código de salida 2) y **no se modifica nunca**. Solo se guarda tras el
  éxito de la operación, de forma atómica.

Sin ``sqlite3``; no depende de ``persistence`` ni de capas superiores.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import UUID

from veritas.catalogs.columns import load_json
from veritas.catalogs.errors import CatalogError, CatalogFileError, file_error
from veritas.catalogs.fileio import write_files
from veritas.core.diagnostics import DiagnosticCode

USER_FORMAT: Final = "veritas-catalog-user/1"
MAX_RECENT: Final = 20
_KEY_FIELDS: Final = frozenset({"catalog_id", "catalog_name", "series", "designation"})
_DOCUMENT_FIELDS: Final = frozenset({"format", "favorites", "recent"})


@dataclass(frozen=True, slots=True)
class UserKey:
    """Perfil marcado por el usuario: identidad estable y nombre del catálogo para mostrar."""

    catalog_id: UUID
    catalog_name: str
    series: str
    designation: str

    @property
    def identity(self) -> tuple[UUID, str, str]:
        return (self.catalog_id, self.series, self.designation)


@dataclass(frozen=True, slots=True)
class UserData:
    """Favoritos (en el orden en que se añadieron) y recientes (el último, primero)."""

    favorites: tuple[UserKey, ...] = ()
    recent: tuple[UserKey, ...] = ()

    def add_favorite(self, key: UserKey) -> tuple["UserData", bool]:
        """(datos, ``True`` si era nuevo); añadir uno ya presente no cambia nada."""
        if any(k.identity == key.identity for k in self.favorites):
            return self, False
        return UserData((*self.favorites, key), self.recent), True

    def remove_favorite(self, key: UserKey) -> tuple["UserData", bool]:
        """(datos, ``True`` si estaba)."""
        kept = tuple(k for k in self.favorites if k.identity != key.identity)
        return UserData(kept, self.recent), len(kept) != len(self.favorites)

    def touch(self, keys: list[UserKey]) -> "UserData":
        """Registra consultas en orden: la última pasa a ser la primera; sin repetidos."""
        recent = list(self.recent)
        for key in keys:
            recent = [k for k in recent if k.identity != key.identity]
            recent.insert(0, key)
        return UserData(self.favorites, tuple(recent[:MAX_RECENT]))

    def is_favorite(self, key: UserKey) -> bool:
        return any(k.identity == key.identity for k in self.favorites)


def _invalid(path: Path, reason: str) -> CatalogFileError:
    return file_error(
        DiagnosticCode.UNSUPPORTED_CONFIGURATION,
        "catalog.user_file",
        {"path": str(path), "reason": reason},
    )


def _key_from(value: object, where: str, path: Path) -> UserKey:
    if not isinstance(value, dict) or set(value) != _KEY_FIELDS:
        raise _invalid(path, f"{where}: se esperaba {', '.join(sorted(_KEY_FIELDS))}")
    texts = {name: value[name] for name in ("catalog_name", "series", "designation")}
    if not all(isinstance(t, str) and t.strip() for t in texts.values()):
        raise _invalid(path, f"{where}: textos vacíos o de otro tipo")
    try:
        catalog_id = UUID(str(value["catalog_id"]))
    except ValueError:
        raise _invalid(path, f"{where}: catalog_id no es un UUID") from None
    return UserKey(catalog_id, texts["catalog_name"], texts["series"], texts["designation"])


def _keys(value: object, name: str, path: Path, limit: int | None) -> tuple[UserKey, ...]:
    if not isinstance(value, list):
        raise _invalid(path, f"{name} debe ser una lista")
    keys = tuple(_key_from(item, f"{name}[{i}]", path) for i, item in enumerate(value))
    if len({k.identity for k in keys}) != len(keys):
        raise _invalid(path, f"{name} repite un perfil")
    if limit is not None and len(keys) > limit:
        raise _invalid(path, f"{name} supera el máximo de {limit}")
    return keys


def parse_user_data(text: str, path: Path) -> UserData:
    """Interpreta un archivo de usuario; cualquier defecto lanza ``CatalogFileError``."""
    try:
        document = load_json(text)
    except (CatalogError, ValueError) as error:
        raise _invalid(path, f"JSON no válido ({type(error).__name__})") from None
    if not isinstance(document, dict) or set(document) != _DOCUMENT_FIELDS:
        raise _invalid(path, f"se esperaba un objeto con {', '.join(sorted(_DOCUMENT_FIELDS))}")
    if document["format"] != USER_FORMAT:
        raise _invalid(path, f"formato distinto de {USER_FORMAT}")
    return UserData(
        _keys(document["favorites"], "favorites", path, None),
        _keys(document["recent"], "recent", path, MAX_RECENT),
    )


def user_data_text(data: UserData) -> str:
    """Documento del archivo de usuario: determinista, con salto de línea final."""

    def encode(key: UserKey) -> dict[str, str]:
        return {
            "catalog_id": str(key.catalog_id),
            "catalog_name": key.catalog_name,
            "series": key.series,
            "designation": key.designation,
        }

    document = {
        "format": USER_FORMAT,
        "favorites": [encode(k) for k in data.favorites],
        "recent": [encode(k) for k in data.recent],
    }
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def load_user_data(path: Path) -> UserData:
    """Lee el archivo de usuario; ausente = vacío. No escribe nunca."""
    if not path.exists():
        return UserData()
    if not path.is_file():
        raise _invalid(path, "no es un archivo")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _invalid(path, f"no se puede leer ({type(error).__name__})") from None
    return parse_user_data(text, path)


def save_user_data(path: Path, data: UserData) -> None:
    """Guarda de forma atómica (temporal contiguo y ``os.replace``)."""
    write_files([(path, user_data_text(data).encode("utf-8"))], overwrite=True)
