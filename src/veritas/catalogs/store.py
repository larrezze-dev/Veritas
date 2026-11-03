"""Almacén SQLite del catálogo: único módulo que importa ``sqlite3`` (ADR-009; T-022 E1).

Garantías:

- **Archivo propio y reconocible.** ``PRAGMA application_id`` identifica un catálogo de Veritas
  y ``PRAGMA user_version`` su esquema. Un archivo ajeno o con ``user_version`` 0 sin la marca
  nunca se escribe ni se migra. Una versión mayor que la soportada se rechaza.
- **Transaccional.** Toda escritura va en una única transacción (``BEGIN IMMEDIATE``); ante
  cualquier excepción se hace ``ROLLBACK`` y el archivo queda idéntico byte a byte. Se usa el
  diario por defecto (sin WAL, que crearía archivos auxiliares).
- **Sin efectos al abrir.** Abrir no crea archivos (modo ``rw``, nunca ``rwc``) y no escribe si
  no hay migración. Crear construye el archivo completo aparte y lo publica al final: un fallo
  no deja un archivo a medias.
- **Historial inmutable.** Una clave natural (catálogo, serie, designación) tiene un
  ``profile_id`` y revisiones 1..n; solo se añaden filas. Contenido idéntico a la última
  revisión = sin cambios; distinto = revisión n+1.
- **Protegido.** Un catálogo protegido rechaza escribir perfiles. Protegerlo es irreversible
  (editar es clonar, T-023).
- **Verificable.** ``verify`` comprueba el diario de SQLite, las claves foráneas, el hash de
  cada revisión, que el documento sea canónico y coincida con las columnas de consulta y la
  coherencia de identidades y revisiones.

La fila guarda el documento canónico completo (``content_json``, fuente de verdad) y, aparte,
columnas numéricas derivadas para las consultas de T-023.
"""

import contextlib
import os
import sqlite3
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from functools import update_wrapper
from pathlib import Path
from typing import Concatenate, Final, Self
from uuid import UUID, uuid4

from veritas.catalogs.errors import (
    CatalogError,
    CatalogFileError,
    catalog_error,
    file_error,
)
from veritas.catalogs.model import (
    CatalogInfo,
    CatalogProfile,
    ProfileData,
    decode_profile,
    profile_json,
    profile_sha256,
    same_content,
    sha256_of,
)
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, Location
from veritas.core.validation import invalid_type

SCHEMA_VERSION: Final = 1
APPLICATION_ID: Final = 0x56434154  # «VCAT»
_MAX_ID_ATTEMPTS: Final = 1000

type Migration = Callable[[sqlite3.Connection], None]

MIGRATIONS: Final[dict[int, Migration]] = {}
"""Migraciones reales registradas, ``N -> N+1``. Vacío: el esquema v1 es el inicial."""

_SCHEMA_V1: Final = (
    """
    CREATE TABLE catalogs (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT NOT NULL,
        source TEXT NOT NULL,
        license TEXT NOT NULL,
        edition TEXT NOT NULL,
        protected INTEGER NOT NULL CHECK (protected IN (0, 1))
    )
    """,
    """
    CREATE TABLE profile_revisions (
        profile_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        catalog_id TEXT NOT NULL REFERENCES catalogs (id),
        series TEXT NOT NULL,
        designation TEXT NOT NULL,
        family TEXT NOT NULL,
        area REAL,
        iy REAL,
        iz REAL,
        torsion_constant REAL,
        linear_mass REAL,
        content_json TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        PRIMARY KEY (profile_id, revision),
        UNIQUE (catalog_id, series, designation, revision)
    )
    """,
    "CREATE INDEX profile_revisions_by_catalog ON profile_revisions (catalog_id, series)",
)

_PROFILE_COLUMNS: Final = (
    "profile_id, revision, catalog_id, series, designation, family, "
    "area, iy, iz, torsion_constant, linear_mass, content_json, content_sha256"
)
_SCALAR_FIELDS: Final = ("area", "iy", "iz", "torsion_constant", "linear_mass")


class PutStatus(StrEnum):
    """Resultado de escribir un perfil (D8)."""

    CREATED = "CREATED"
    REVISED = "REVISED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True, slots=True)
class PutResult:
    """Perfil resultante y qué ocurrió con él."""

    status: PutStatus
    profile: CatalogProfile


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """Resultado de ``verify``: ``ok`` solo si no hay ningún problema."""

    revisions_checked: int
    issues: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def _translated[**P, R](
    method: Callable[Concatenate["CatalogStore", P], R],
) -> Callable[Concatenate["CatalogStore", P], R]:
    """Convierte ``sqlite3.Error`` en ``CatalogFileError`` con la causa técnica."""

    def wrapper(self: "CatalogStore", /, *args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return method(self, *args, **kwargs)
        except sqlite3.Error as error:
            raise _database_error(error) from error

    update_wrapper(wrapper, method)
    return wrapper


def _database_error(error: sqlite3.Error) -> CatalogFileError:
    return file_error(
        DiagnosticCode.UNSUPPORTED_CONFIGURATION,
        "catalog.database_error",
        cause=f"{type(error).__name__}: {error}",
    )


def _uri(path: Path, mode: str) -> str:
    return f"{path.resolve().as_uri()}?mode={mode}"


def _connect(path: Path, mode: str) -> sqlite3.Connection:
    connection = sqlite3.connect(_uri(path, mode), uri=True, isolation_level=None, timeout=5.0)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _pragma_int(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    return int(row[0])


class CatalogStore:
    """Catálogo SQLite abierto. Usar como gestor de contexto o llamar a ``close``."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        path: Path,
        version: int,
        id_factory: Callable[[], UUID],
    ) -> None:
        self._connection: sqlite3.Connection | None = connection
        self._path = path
        self._version = version
        self._id_factory = id_factory

    # --- Ciclo de vida -------------------------------------------------------------------

    @classmethod
    def create(
        cls, path: str | os.PathLike[str], *, id_factory: Callable[[], UUID] = uuid4
    ) -> Self:
        """Crea un catálogo vacío (esquema v1). Rechaza un archivo existente.

        Se construye en un archivo temporal contiguo y se publica al terminar: un fallo no deja
        un archivo a medias ni toca el destino.
        """
        target = Path(path)
        if target.exists():
            raise file_error(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "catalog.file_exists",
                {"path": str(target)},
            )
        temporary = target.with_name(f"{target.name}.creating")
        if temporary.exists():
            raise file_error(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "catalog.file_exists",
                {"path": str(temporary)},
            )
        try:
            connection = _connect(temporary, "rwc")
            try:
                connection.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA_V1:
                    connection.execute(statement)
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.execute("COMMIT")
            finally:
                connection.close()
            os.replace(temporary, target)
        except sqlite3.Error as error:
            _discard(temporary)
            raise _database_error(error) from error
        except BaseException:
            _discard(temporary)
            raise
        return cls.open(target, id_factory=id_factory)

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        *,
        id_factory: Callable[[], UUID] = uuid4,
        migrations: dict[int, Migration] | None = None,
        target_version: int = SCHEMA_VERSION,
    ) -> Self:
        """Abre un catálogo existente, migrándolo si su ``user_version`` es menor.

        ``migrations`` y ``target_version`` permiten probar la cadena con migraciones
        sintéticas (como ``persistence.migrations.migrate``). No crea archivos y no escribe en
        uno ajeno, de versión mayor o con una cadena de migración incompleta.
        """
        source = Path(path)
        if not source.is_file():
            raise file_error(
                DiagnosticCode.REFERENCE_NOT_FOUND, "catalog.file_missing", {"path": str(source)}
            )
        steps = MIGRATIONS if migrations is None else migrations
        try:
            connection = _connect(source, "rw")
        except sqlite3.Error as error:
            raise _database_error(error) from error
        try:
            version = cls._check_header(connection, source, steps, target_version)
            if version < target_version:
                version = _migrate(connection, version, target_version, steps)
        except BaseException:
            connection.close()
            raise
        return cls(connection, source, version, id_factory)

    @staticmethod
    def _check_header(
        connection: sqlite3.Connection,
        path: Path,
        steps: dict[int, Migration],
        target_version: int,
    ) -> int:
        try:
            application_id = _pragma_int(connection, "application_id")
            version = _pragma_int(connection, "user_version")
        except sqlite3.DatabaseError as error:
            raise file_error(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "catalog.unreadable",
                {"path": str(path)},
                cause=f"{type(error).__name__}: {error}",
            ) from error
        if application_id != APPLICATION_ID:
            raise file_error(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "catalog.not_a_catalog",
                {"path": str(path)},
            )
        if version > target_version or version < 0:
            raise file_error(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "catalog.unsupported_version",
                {"found": version, "supported": target_version},
            )
        for step in range(version, target_version):
            if step not in steps:
                raise file_error(
                    DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                    "catalog.migration_gap",
                    {"step": step, "next": step + 1},
                )
        return version

    def close(self) -> None:
        """Cierra la conexión (idempotente)."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def version(self) -> int:
        """``user_version`` del archivo."""
        return self._version

    def _con(self) -> sqlite3.Connection:
        if self._connection is None:
            raise catalog_error(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "catalog.store_closed",
                {"path": str(self._path)},
            )
        return self._connection

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection]:
        """Transacción única de escritura: ``COMMIT`` al salir bien, ``ROLLBACK`` si no."""
        connection = self._con()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise

    # --- Catálogos -----------------------------------------------------------------------

    @_translated
    def create_catalog(
        self, *, name: str, source: str, license: str, edition: str, description: str = ""
    ) -> CatalogInfo:
        """Alta de un catálogo (no protegido). El nombre es único sin distinguir mayúsculas."""
        with self._transaction() as con:
            info = CatalogInfo(
                id=self._new_id(con, "catalogs", "id"),
                name=name,
                description=description,
                source=source,
                license=license,
                edition=edition,
                protected=False,
            )
            taken = con.execute("SELECT 1 FROM catalogs WHERE name = ?", (info.name,)).fetchone()
            if taken is not None:
                raise catalog_error(
                    DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                    "catalog.name_taken",
                    {"name": info.name},
                )
            con.execute(
                "INSERT INTO catalogs (id, name, description, source, license, edition, protected)"
                " VALUES (?, ?, ?, ?, ?, ?, 0)",
                (
                    str(info.id),
                    info.name,
                    info.description,
                    info.source,
                    info.license,
                    info.edition,
                ),
            )
        return info

    @_translated
    def lock_catalog(self, catalog_id: UUID) -> CatalogInfo:
        """Marca el catálogo como protegido (irreversible, idempotente)."""
        with self._transaction() as con:
            self._catalog(con, catalog_id)
            con.execute("UPDATE catalogs SET protected = 1 WHERE id = ?", (str(catalog_id),))
            return self._catalog(con, catalog_id)

    @_translated
    def get_catalog(self, catalog_id: UUID) -> CatalogInfo:
        """Catálogo por id o ``REFERENCE_NOT_FOUND``."""
        return self._catalog(self._con(), catalog_id)

    @_translated
    def catalog_by_name(self, name: str) -> CatalogInfo:
        """Catálogo por nombre (sin distinguir mayúsculas) o ``REFERENCE_NOT_FOUND``."""
        row = (
            self._con()
            .execute(
                "SELECT id, name, description, source, license, edition, protected"
                " FROM catalogs WHERE name = ?",
                (name.strip(),),
            )
            .fetchone()
        )
        if row is None:
            raise _catalog_missing(name)
        return _catalog_from_row(row)

    @_translated
    def list_catalogs(self) -> tuple[CatalogInfo, ...]:
        """Todos los catálogos, ordenados por nombre."""
        rows = (
            self._con()
            .execute(
                "SELECT id, name, description, source, license, edition, protected"
                " FROM catalogs ORDER BY name"
            )
            .fetchall()
        )
        return tuple(_catalog_from_row(row) for row in rows)

    # --- Perfiles ------------------------------------------------------------------------

    @_translated
    def put_profiles(self, catalog_id: UUID, items: Sequence[ProfileData]) -> tuple[PutResult, ...]:
        """Escribe todos los perfiles en una única transacción (todo o nada).

        Por cada uno: clave nueva = revisión 1; contenido idéntico a la última revisión = sin
        cambios; contenido distinto = revisión n+1 con el mismo ``profile_id``. Rechaza un
        catálogo protegido y claves naturales repetidas dentro del lote.
        """
        with self._transaction() as con:
            info = self._catalog(con, catalog_id)
            if info.protected:
                raise catalog_error(
                    DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                    "catalog.protected",
                    {"catalog": info.name},
                )
            seen: set[tuple[str, str]] = set()
            for data in items:
                if not isinstance(data, ProfileData):
                    raise invalid_type("items", "ProfileData", data)
                if data.key in seen:
                    raise catalog_error(
                        DiagnosticCode.REVISION_CONFLICT,
                        "catalog.duplicate_key",
                        {"series": data.series, "designation": data.designation},
                    )
                seen.add(data.key)
            return tuple(self._put(con, catalog_id, data) for data in items)

    def put_profile(self, catalog_id: UUID, data: ProfileData) -> PutResult:
        """``put_profiles`` de un solo perfil."""
        return self.put_profiles(catalog_id, [data])[0]

    def _put(self, con: sqlite3.Connection, catalog_id: UUID, data: ProfileData) -> PutResult:
        row = con.execute(
            f"SELECT {_PROFILE_COLUMNS} FROM profile_revisions"
            " WHERE catalog_id = ? AND series = ? AND designation = ?"
            " ORDER BY revision DESC LIMIT 1",
            (str(catalog_id), data.series, data.designation),
        ).fetchone()
        if row is None:
            profile = CatalogProfile(
                id=self._new_id(con, "profile_revisions", "profile_id"),
                revision=1,
                catalog_id=catalog_id,
                data=data,
            )
            self._insert_revision(con, profile)
            return PutResult(PutStatus.CREATED, profile)
        latest = decode_profile(row[11])
        if same_content(latest.data, data):
            return PutResult(PutStatus.UNCHANGED, latest)
        profile = CatalogProfile(
            id=latest.id, revision=latest.revision + 1, catalog_id=catalog_id, data=data
        )
        self._insert_revision(con, profile)
        return PutResult(PutStatus.REVISED, profile)

    def _insert_revision(self, con: sqlite3.Connection, profile: CatalogProfile) -> None:
        """Inserta una fila de revisión (punto único de escritura; las pruebas inyectan fallos)."""
        props = profile.properties
        con.execute(
            f"INSERT INTO profile_revisions ({_PROFILE_COLUMNS})"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(profile.id),
                profile.revision,
                str(profile.catalog_id),
                profile.series,
                profile.designation,
                profile.family.value,
                *(getattr(props, name).value for name in _SCALAR_FIELDS),
                profile_json(profile),
                profile_sha256(profile),
            ),
        )

    @_translated
    def get_profile(self, profile_id: UUID, revision: int) -> CatalogProfile:
        """Revisión exacta o ``REFERENCE_NOT_FOUND``."""
        row = (
            self._con()
            .execute(
                "SELECT content_json FROM profile_revisions WHERE profile_id = ? AND revision = ?",
                (str(profile_id), revision),
            )
            .fetchone()
        )
        if row is None:
            raise _profile_missing(f"{profile_id} rev {revision}")
        return decode_profile(row[0])

    @_translated
    def history(
        self, catalog_id: UUID, series: str, designation: str
    ) -> tuple[CatalogProfile, ...]:
        """Revisiones de una clave natural (1..n); ``REFERENCE_NOT_FOUND`` si no existe."""
        rows = (
            self._con()
            .execute(
                "SELECT content_json FROM profile_revisions"
                " WHERE catalog_id = ? AND series = ? AND designation = ? ORDER BY revision",
                (str(catalog_id), series.strip(), designation.strip()),
            )
            .fetchall()
        )
        if not rows:
            raise _profile_missing(f"{series.strip()} / {designation.strip()}")
        return tuple(decode_profile(row[0]) for row in rows)

    def latest(self, catalog_id: UUID, series: str, designation: str) -> CatalogProfile:
        """Última revisión de una clave natural."""
        return self.history(catalog_id, series, designation)[-1]

    @_translated
    def list_profiles(
        self, catalog_id: UUID, *, all_revisions: bool = False
    ) -> tuple[CatalogProfile, ...]:
        """Perfiles de un catálogo por serie y designación; por defecto, solo la última revisión."""
        con = self._con()
        self._catalog(con, catalog_id)
        latest_only = (
            " AND revision = (SELECT MAX(r.revision) FROM profile_revisions AS r"
            " WHERE r.profile_id = profile_revisions.profile_id)"
            if not all_revisions
            else ""
        )
        rows = con.execute(
            "SELECT content_json FROM profile_revisions WHERE catalog_id = ?"
            f"{latest_only} ORDER BY series, designation, revision",
            (str(catalog_id),),
        ).fetchall()
        return tuple(decode_profile(row[0]) for row in rows)

    # --- Verificación --------------------------------------------------------------------

    @_translated
    def verify(self) -> VerifyReport:
        """Comprueba la integridad del archivo y de cada revisión; no modifica nada."""
        con = self._con()
        issues: list[Diagnostic] = []
        checked = 0
        con.execute("BEGIN")
        try:
            issues.extend(_verify_header(con))
            try:
                issues.extend(_verify_sqlite(con))
                rows = con.execute(
                    f"SELECT {_PROFILE_COLUMNS} FROM profile_revisions"
                    " ORDER BY profile_id, revision"
                ).fetchall()
            except sqlite3.DatabaseError as error:
                issues.append(_issue("catalog.verify_integrity", {"detail": str(error)}))
                return VerifyReport(checked, tuple(issues))
            checked = len(rows)
            issues.extend(_verify_rows(rows))
        finally:
            with contextlib.suppress(sqlite3.Error):
                con.execute("ROLLBACK")
        return VerifyReport(checked, tuple(issues))

    # --- Internos ------------------------------------------------------------------------

    def _catalog(self, con: sqlite3.Connection, catalog_id: UUID) -> CatalogInfo:
        row = con.execute(
            "SELECT id, name, description, source, license, edition, protected"
            " FROM catalogs WHERE id = ?",
            (str(catalog_id),),
        ).fetchone()
        if row is None:
            raise _catalog_missing(str(catalog_id))
        return _catalog_from_row(row)

    def _new_id(self, con: sqlite3.Connection, table: str, column: str) -> UUID:
        for _ in range(_MAX_ID_ATTEMPTS):
            candidate = self._id_factory()
            used = con.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ?",
                (str(candidate),),
            ).fetchone()
            if used is None:
                return candidate
        raise RuntimeError("No se pudo obtener un identificador de catálogo no usado")


# --- Funciones auxiliares -------------------------------------------------------------------


def _discard(path: Path) -> None:
    for candidate in (path, path.with_name(path.name + "-journal")):
        with contextlib.suppress(OSError):
            candidate.unlink()


def _migrate(
    connection: sqlite3.Connection, version: int, target: int, steps: dict[int, Migration]
) -> int:
    """Aplica la cadena ``version -> target`` en una única transacción."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for step in range(version, target):
                steps[step](connection)
            connection.execute(f"PRAGMA user_version = {target}")
            connection.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise
    except sqlite3.Error as error:
        raise _database_error(error) from error
    return target


def _catalog_from_row(row: tuple[object, ...]) -> CatalogInfo:
    return CatalogInfo(
        id=UUID(str(row[0])),
        name=str(row[1]),
        description=str(row[2]),
        source=str(row[3]),
        license=str(row[4]),
        edition=str(row[5]),
        protected=bool(row[6]),
    )


def _catalog_missing(what: str) -> CatalogError:
    return catalog_error(DiagnosticCode.REFERENCE_NOT_FOUND, "catalog.not_found", {"catalog": what})


def _profile_missing(what: str) -> CatalogError:
    return catalog_error(
        DiagnosticCode.REFERENCE_NOT_FOUND, "catalog.profile_not_found", {"profile": what}
    )


def _issue(key: str, params: dict[str, str | int], profile_id: str | None = None) -> Diagnostic:
    location = None
    if profile_id is not None:
        try:
            location = Location(entity_id=UUID(profile_id))
        except (ValueError, TypeError):
            params = {**params, "profile": profile_id}
    return Diagnostic.create(DiagnosticCode.INPUT_OUT_OF_RANGE, key, params, location=location)


def _verify_header(con: sqlite3.Connection) -> list[Diagnostic]:
    issues: list[Diagnostic] = []
    application_id = _pragma_int(con, "application_id")
    version = _pragma_int(con, "user_version")
    if application_id != APPLICATION_ID:
        issues.append(_issue("catalog.verify_header", {"field": "application_id"}))
    if version != SCHEMA_VERSION:
        issues.append(_issue("catalog.verify_header", {"field": "user_version"}))
    return issues


def _verify_sqlite(con: sqlite3.Connection) -> list[Diagnostic]:
    issues: list[Diagnostic] = []
    for (message,) in con.execute("PRAGMA integrity_check").fetchall():
        if message != "ok":
            issues.append(_issue("catalog.verify_integrity", {"detail": str(message)}))
    for table, _rowid, parent, _index in con.execute("PRAGMA foreign_key_check").fetchall():
        issues.append(_issue("catalog.verify_orphan", {"table": str(table), "parent": str(parent)}))
    return issues


def _verify_rows(rows: list[tuple[object, ...]]) -> list[Diagnostic]:
    issues: list[Diagnostic] = []
    revisions: dict[str, list[int]] = {}
    identity: dict[str, tuple[object, ...]] = {}
    keys: dict[tuple[object, ...], str] = {}
    for row in rows:
        profile_id, revision = str(row[0]), int(str(row[1]))
        issues.extend(_verify_row(row))
        revisions.setdefault(profile_id, []).append(revision)
        natural = (row[2], row[3], row[4])
        if identity.setdefault(profile_id, natural) != natural:
            issues.append(_issue("catalog.verify_identity", {"revision": revision}, profile_id))
        if keys.setdefault(natural, profile_id) != profile_id:
            issues.append(_issue("catalog.verify_identity", {"revision": revision}, profile_id))
    for profile_id, numbers in revisions.items():
        if numbers != list(range(1, len(numbers) + 1)):
            issues.append(
                _issue(
                    "catalog.verify_revision_sequence",
                    {"found": ",".join(str(n) for n in numbers)},
                    profile_id,
                )
            )
    return issues


def _verify_row(row: tuple[object, ...]) -> list[Diagnostic]:
    profile_id, revision = str(row[0]), int(str(row[1]))
    text, stored_hash = str(row[11]), str(row[12])
    if sha256_of(text) != stored_hash:
        return [_issue("catalog.verify_hash", {"revision": revision}, profile_id)]
    try:
        profile = decode_profile(text)
    except CatalogError:
        return [_issue("catalog.verify_document", {"revision": revision}, profile_id)]
    if profile_json(profile) != text:
        return [_issue("catalog.verify_document", {"revision": revision}, profile_id)]
    expected: dict[str, object] = {
        "profile_id": str(profile.id),
        "revision": profile.revision,
        "catalog_id": str(profile.catalog_id),
        "series": profile.series,
        "designation": profile.designation,
        "family": profile.family.value,
        **{name: getattr(profile.properties, name).value for name in _SCALAR_FIELDS},
    }
    names = (
        "profile_id",
        "revision",
        "catalog_id",
        "series",
        "designation",
        "family",
        *_SCALAR_FIELDS,
    )
    differing = [
        name
        for name, stored in zip(names, row[:11], strict=True)
        if repr(stored) != repr(expected[name])
    ]
    if differing:
        return [
            _issue(
                "catalog.verify_columns",
                {"revision": revision, "fields": ", ".join(differing)},
                profile_id,
            )
        ]
    return []


__all__ = [
    "APPLICATION_ID",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "CatalogStore",
    "Migration",
    "PutResult",
    "PutStatus",
    "VerifyReport",
]
