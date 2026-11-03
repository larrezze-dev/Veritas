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
from datetime import UTC, datetime
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
    ImportBatch,
    ImportSource,
    ProfileData,
    decode_profile,
    profile_json,
    profile_sha256,
    same_content,
    sha256_of,
)
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, Location
from veritas.core.validation import invalid_type

SCHEMA_VERSION: Final = 3
APPLICATION_ID: Final = 0x56434154  # «VCAT»
_MAX_ID_ATTEMPTS: Final = 1000

type Migration = Callable[[sqlite3.Connection], None]

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

_MIGRATION_1_TO_2: Final = (
    """
    CREATE TABLE import_batches (
        id TEXT PRIMARY KEY,
        catalog_id TEXT NOT NULL REFERENCES catalogs (id),
        imported_at TEXT NOT NULL,
        file_name TEXT NOT NULL,
        file_format TEXT NOT NULL CHECK (file_format IN ('CSV', 'JSON')),
        file_sha256 TEXT NOT NULL,
        mapping_json TEXT NOT NULL,
        mapping_sha256 TEXT NOT NULL,
        rows_read INTEGER NOT NULL CHECK (rows_read >= 0),
        rows_accepted INTEGER NOT NULL CHECK (rows_accepted >= 0),
        rows_rejected INTEGER NOT NULL CHECK (rows_rejected >= 0),
        created INTEGER NOT NULL CHECK (created >= 0),
        revised INTEGER NOT NULL CHECK (revised >= 0),
        unchanged INTEGER NOT NULL CHECK (unchanged >= 0)
    )
    """,
    "CREATE INDEX import_batches_by_catalog ON import_batches (catalog_id, imported_at)",
    "ALTER TABLE profile_revisions ADD COLUMN batch_id TEXT REFERENCES import_batches (id)",
)
"""Esquema v2 (enmienda de ADR-009, T-022 E2-A): lotes de importación y el enlace de cada
revisión con el lote que la creó (nulo en las revisiones anteriores a la migración)."""


def _migration_1_to_2(connection: sqlite3.Connection) -> None:
    for statement in _MIGRATION_1_TO_2:
        connection.execute(statement)


_MIGRATION_2_TO_3: Final = (
    "ALTER TABLE catalogs ADD COLUMN cloned_from TEXT REFERENCES catalogs (id)",
    "ALTER TABLE profile_revisions ADD COLUMN cloned_from_profile_id TEXT",
    "ALTER TABLE profile_revisions ADD COLUMN cloned_from_revision INTEGER",
)
"""Esquema v3 (enmienda de ADR-009, T-023 E2): trazabilidad de los clones. ``cloned_from`` es el
catálogo de origen (nulo si no es un clon); cada revisión copiada guarda la revisión exacta de
origen. Los dos enlaces de revisión **no** llevan clave foránea (el padre sería compuesto) y se
comprueban en ``verify``."""


def _migration_2_to_3(connection: sqlite3.Connection) -> None:
    for statement in _MIGRATION_2_TO_3:
        connection.execute(statement)


MIGRATIONS: Final[dict[int, Migration]] = {1: _migration_1_to_2, 2: _migration_2_to_3}
"""Migraciones reales registradas, ``N -> N+1``. ``create`` construye v1 y aplica estas mismas
migraciones, de modo que un archivo nuevo y uno migrado tienen el mismo esquema."""

_PROFILE_COLUMNS: Final = (
    "profile_id, revision, catalog_id, series, designation, family, "
    "area, iy, iz, torsion_constant, linear_mass, content_json, content_sha256"
)
_SCALAR_FIELDS: Final = ("area", "iy", "iz", "torsion_constant", "linear_mass")
_BATCH_COLUMNS: Final = (
    "id, catalog_id, imported_at, file_name, file_format, file_sha256, mapping_json, "
    "mapping_sha256, rows_read, rows_accepted, rows_rejected, created, revised, unchanged"
)


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
class BatchResult:
    """Lote escrito y resultado por perfil, en el orden recibido."""

    batch: ImportBatch
    results: tuple[PutResult, ...]


@dataclass(frozen=True, slots=True)
class CloneResult:
    """Catálogo clonado y sus perfiles (revisión 1), en el orden del origen."""

    catalog: CatalogInfo
    profiles: tuple[CatalogProfile, ...]


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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._connection: sqlite3.Connection | None = connection
        self._path = path
        self._version = version
        self._id_factory = id_factory
        self._clock = clock

    # --- Ciclo de vida -------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        *,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> Self:
        """Crea un catálogo vacío (esquema vigente). Rechaza un archivo existente.

        Se construye en un archivo temporal contiguo (esquema v1 y después las migraciones
        reales, como un archivo antiguo al abrirse) y se publica al terminar: un fallo no deja
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
                for step in range(1, SCHEMA_VERSION):
                    MIGRATIONS[step](connection)
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
        return cls.open(target, id_factory=id_factory, clock=clock)

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        *,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
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
        return cls(connection, source, version, id_factory, clock)

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
            return self._insert_catalog(
                con,
                name=name,
                source=source,
                license=license,
                edition=edition,
                description=description,
            )

    def _insert_catalog(  # noqa: PLR0913 (campos del catálogo por nombre)
        self,
        con: sqlite3.Connection,
        *,
        name: str,
        source: str,
        license: str,
        edition: str,
        description: str,
        cloned_from: UUID | None = None,
    ) -> CatalogInfo:
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
            "INSERT INTO catalogs (id, name, description, source, license, edition, protected,"
            " cloned_from) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (
                str(info.id),
                info.name,
                info.description,
                info.source,
                info.license,
                info.edition,
                None if cloned_from is None else str(cloned_from),
            ),
        )
        return info

    @_translated
    def clone_catalog(
        self, origin_id: UUID, name: str, *, description: str | None = None
    ) -> CloneResult:
        """Clona un catálogo (también uno protegido) en otro nuevo y editable, de forma atómica.

        Copia la última revisión de cada perfil como revisión 1, con identificadores nuevos y el
        mismo contenido (la procedencia de cada perfil se conserva), y deja constancia del origen:
        el catálogo guarda ``cloned_from`` y cada revisión la revisión exacta de la que procede.
        La fuente, la licencia y la edición se copian; la descripción también salvo que se indique
        otra. El origen no se modifica. Un fallo en cualquier punto no deja nada.
        """
        with self._transaction() as con:
            origin = self._catalog(con, origin_id)
            info = self._insert_catalog(
                con,
                name=name,
                source=origin.source,
                license=origin.license,
                edition=origin.edition,
                description=origin.description if description is None else description,
                cloned_from=origin.id,
            )
            sources = self._profiles(con, origin.id, all_revisions=False)
            results = self._put_all(con, info.id, [p.data for p in sources], None)
            for source, result in zip(sources, results, strict=True):
                con.execute(
                    "UPDATE profile_revisions SET cloned_from_profile_id = ?,"
                    " cloned_from_revision = ? WHERE profile_id = ? AND revision = ?",
                    (
                        str(source.id),
                        source.revision,
                        str(result.profile.id),
                        result.profile.revision,
                    ),
                )
            return CloneResult(info, tuple(result.profile for result in results))

    @_translated
    def clone_source(self, catalog_id: UUID) -> CatalogInfo | None:
        """Catálogo del que procede este clon, o ``None`` si no es un clon."""
        con = self._con()
        self._catalog(con, catalog_id)
        row = con.execute(
            "SELECT cloned_from FROM catalogs WHERE id = ?", (str(catalog_id),)
        ).fetchone()
        return None if row[0] is None else self._catalog(con, UUID(str(row[0])))

    @_translated
    def revision_origin(self, profile_id: UUID, revision: int) -> CatalogProfile | None:
        """Revisión de origen de una revisión copiada por un clon, o ``None`` si no lo es."""
        con = self._con()
        row = con.execute(
            "SELECT cloned_from_profile_id, cloned_from_revision FROM profile_revisions"
            " WHERE profile_id = ? AND revision = ?",
            (str(profile_id), revision),
        ).fetchone()
        if row is None:
            raise _profile_missing(f"{profile_id} rev {revision}")
        if row[0] is None or row[1] is None:
            return None
        return self.get_profile(UUID(str(row[0])), int(row[1]))

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
            return self._put_all(con, catalog_id, items, None)

    @_translated
    def import_batch(
        self, catalog_id: UUID, items: Sequence[ProfileData], source: ImportSource
    ) -> BatchResult:
        """``put_profiles`` más el registro del lote, en la **misma** transacción (E2-A).

        El lote guarda el origen (archivo, mapeo y recuentos de lectura) y el resultado por
        perfil; cada revisión nueva queda enlazada a él. Un fallo en cualquier punto no deja ni
        perfiles ni lote. ``source.rows_accepted`` debe coincidir con ``len(items)``.
        """
        if not isinstance(source, ImportSource):
            raise invalid_type("source", "ImportSource", source)
        if source.rows_accepted != len(items):
            raise catalog_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "catalog.invalid_batch",
                {"field": "rows_accepted"},
            )
        with self._transaction() as con:
            self._catalog(con, catalog_id)  # catálogo inexistente antes de escribir el lote
            batch_id = self._new_id(con, "import_batches", "id")
            stamp = self._stamp()
            con.execute(
                "INSERT INTO import_batches (id, catalog_id, imported_at, file_name, file_format,"
                " file_sha256, mapping_json, mapping_sha256, rows_read, rows_accepted,"
                " rows_rejected, created, revised, unchanged)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0)",
                (
                    str(batch_id),
                    str(catalog_id),
                    stamp,
                    source.file_name,
                    source.file_format,
                    source.file_sha256,
                    source.mapping_json,
                    source.mapping_sha256,
                    source.rows_read,
                    source.rows_accepted,
                    source.rows_rejected,
                ),
            )
            results = self._put_all(con, catalog_id, items, batch_id)
            counts = {status: 0 for status in PutStatus}
            for result in results:
                counts[result.status] += 1
            con.execute(
                "UPDATE import_batches SET created = ?, revised = ?, unchanged = ? WHERE id = ?",
                (
                    counts[PutStatus.CREATED],
                    counts[PutStatus.REVISED],
                    counts[PutStatus.UNCHANGED],
                    str(batch_id),
                ),
            )
            batch = ImportBatch(
                id=batch_id,
                catalog_id=catalog_id,
                imported_at=stamp,
                source=source,
                created=counts[PutStatus.CREATED],
                revised=counts[PutStatus.REVISED],
                unchanged=counts[PutStatus.UNCHANGED],
            )
            return BatchResult(batch, results)

    def _stamp(self) -> str:
        moment = self._clock()
        if moment.tzinfo is None:
            raise RuntimeError("El reloj del catálogo debe devolver una fecha con zona horaria")
        return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _put_all(
        self,
        con: sqlite3.Connection,
        catalog_id: UUID,
        items: Sequence[ProfileData],
        batch_id: UUID | None,
    ) -> tuple[PutResult, ...]:
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
        results = tuple(self._put(con, catalog_id, data) for data in items)
        if batch_id is not None:
            for result in results:
                if result.status is not PutStatus.UNCHANGED:
                    con.execute(
                        "UPDATE profile_revisions SET batch_id = ?"
                        " WHERE profile_id = ? AND revision = ?",
                        (str(batch_id), str(result.profile.id), result.profile.revision),
                    )
        return results

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
        return self._profiles(self._con(), catalog_id, all_revisions=all_revisions)

    def _profiles(
        self, con: sqlite3.Connection, catalog_id: UUID, *, all_revisions: bool
    ) -> tuple[CatalogProfile, ...]:
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

    # --- Lotes de importación ------------------------------------------------------------

    @_translated
    def list_batches(self, catalog_id: UUID | None = None) -> tuple[ImportBatch, ...]:
        """Lotes por orden de escritura; los de un catálogo si se indica (que debe existir)."""
        con = self._con()
        if catalog_id is None:
            rows = con.execute(f"SELECT {_BATCH_COLUMNS} FROM import_batches ORDER BY rowid")
        else:
            self._catalog(con, catalog_id)
            rows = con.execute(
                f"SELECT {_BATCH_COLUMNS} FROM import_batches WHERE catalog_id = ? ORDER BY rowid",
                (str(catalog_id),),
            )
        return tuple(_batch_from_row(row) for row in rows.fetchall())

    @_translated
    def revision_batch(self, profile_id: UUID, revision: int) -> ImportBatch | None:
        """Lote que creó la revisión, o ``None`` si es anterior a la migración a v2."""
        con = self._con()
        row = con.execute(
            "SELECT batch_id FROM profile_revisions WHERE profile_id = ? AND revision = ?",
            (str(profile_id), revision),
        ).fetchone()
        if row is None:
            raise _profile_missing(f"{profile_id} rev {revision}")
        if row[0] is None:
            return None
        batch = con.execute(
            f"SELECT {_BATCH_COLUMNS} FROM import_batches WHERE id = ?", (row[0],)
        ).fetchone()
        return None if batch is None else _batch_from_row(batch)

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
                batch_issues = _verify_batches(con)
                clone_issues = _verify_clones(con)
            except sqlite3.DatabaseError as error:
                issues.append(_issue("catalog.verify_integrity", {"detail": str(error)}))
                return VerifyReport(checked, tuple(issues))
            checked = len(rows)
            issues.extend(_verify_rows(rows))
            issues.extend(batch_issues)
            issues.extend(clone_issues)
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


def _batch_from_row(row: tuple[object, ...]) -> ImportBatch:
    """``ImportBatch`` desde una fila de ``_BATCH_COLUMNS``; valida el contenido por completo."""
    source = ImportSource(
        file_name=str(row[3]),
        file_format=str(row[4]),
        file_sha256=str(row[5]),
        mapping_json=str(row[6]),
        mapping_sha256=str(row[7]),
        rows_read=_int(row[8]),
        rows_rejected=_int(row[10]),
    )
    if _int(row[9]) != source.rows_accepted:
        raise catalog_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "catalog.invalid_batch", {"field": "rows_accepted"}
        )
    return ImportBatch(
        id=UUID(str(row[0])),
        catalog_id=UUID(str(row[1])),
        imported_at=str(row[2]),
        source=source,
        created=_int(row[11]),
        revised=_int(row[12]),
        unchanged=_int(row[13]),
    )


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise catalog_error(
            DiagnosticCode.INPUT_INVALID_TYPE, "catalog.invalid_batch", {"field": "recuento"}
        )
    return value


def _verify_batches(con: sqlite3.Connection) -> list[Diagnostic]:
    """Cada lote es válido y las revisiones enlazadas son las que dice haber escrito."""
    issues: list[Diagnostic] = []
    linked: dict[str, dict[str, int]] = {}
    for batch_id, catalog_id, count in con.execute(
        "SELECT batch_id, catalog_id, COUNT(*) FROM profile_revisions"
        " WHERE batch_id IS NOT NULL GROUP BY batch_id, catalog_id"
    ).fetchall():
        linked.setdefault(str(batch_id), {})[str(catalog_id)] = int(count)
    for row in con.execute(f"SELECT {_BATCH_COLUMNS} FROM import_batches ORDER BY rowid"):
        label = str(row[0])
        try:
            batch = _batch_from_row(row)
        except (CatalogError, ValueError, TypeError) as error:
            field = "contenido"
            if isinstance(error, CatalogError):
                field = str(error.diagnostic.params_dict().get("field", field))
            issues.append(_issue("catalog.verify_batch", {"batch": label, "field": field}))
            continue
        per_catalog = linked.get(label, {})
        if sum(per_catalog.values()) != batch.written:
            issues.append(_issue("catalog.verify_batch", {"batch": label, "field": "enlaces"}))
        elif set(per_catalog) - {str(batch.catalog_id)}:
            issues.append(_issue("catalog.verify_batch", {"batch": label, "field": "catálogo"}))
    return issues


def _verify_clones(con: sqlite3.Connection) -> list[Diagnostic]:
    """Trazabilidad de los clones: enlaces completos, de la revisión 1, a un origen que existe en
    el catálogo de origen declarado, con el mismo contenido."""
    issues: list[Diagnostic] = []
    origin_of: dict[str, str | None] = {}
    for catalog_id, cloned_from in con.execute("SELECT id, cloned_from FROM catalogs").fetchall():
        origin_of[str(catalog_id)] = None if cloned_from is None else str(cloned_from)
        if cloned_from is not None and str(cloned_from) == str(catalog_id):
            issues.append(
                _issue(
                    "catalog.verify_clone", {"item": f"catálogo {catalog_id}", "field": "origen"}
                )
            )
    rows = con.execute(
        "SELECT profile_id, revision, catalog_id, content_json, cloned_from_profile_id,"
        " cloned_from_revision FROM profile_revisions"
        " WHERE cloned_from_profile_id IS NOT NULL OR cloned_from_revision IS NOT NULL"
        " ORDER BY profile_id, revision"
    ).fetchall()
    for row in rows:
        field = _clone_link_problem(con, origin_of, row)
        if field is None:
            field = _clone_content_problem(con, str(row[3]), str(row[4]), int(row[5]))
        if field is not None:
            issues.append(
                _issue(
                    "catalog.verify_clone",
                    {"item": f"revisión {row[1]}", "field": field},
                    str(row[0]),
                )
            )
    return issues


def _clone_link_problem(
    con: sqlite3.Connection, origin_of: dict[str, str | None], row: tuple[object, ...]
) -> str | None:
    """Problema de los enlaces de una revisión (``row`` = fila de ``_verify_clones``)."""
    revision, catalog_id, link_profile, link_revision = row[1], row[2], row[4], row[5]
    if link_profile is None or link_revision is None:
        return "enlace incompleto"
    if revision != 1:
        return "revisión posterior a la primera"
    origin = con.execute(
        "SELECT catalog_id FROM profile_revisions WHERE profile_id = ? AND revision = ?",
        (str(link_profile), link_revision),
    ).fetchone()
    if origin is None:
        return "revisión de origen inexistente"
    if origin_of.get(str(catalog_id)) is None or str(origin[0]) != origin_of[str(catalog_id)]:
        return "catálogo de origen distinto"
    return None


def _clone_content_problem(
    con: sqlite3.Connection, text: str, link_profile: str, link_revision: int
) -> str | None:
    origin = con.execute(
        "SELECT content_json FROM profile_revisions WHERE profile_id = ? AND revision = ?",
        (link_profile, link_revision),
    ).fetchone()
    try:
        same = same_content(decode_profile(text).data, decode_profile(str(origin[0])).data)
    except CatalogError:
        return None  # un documento ilegible ya lo informa la verificación de cada revisión
    return None if same else "contenido distinto al del origen"


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
    "BatchResult",
    "CatalogStore",
    "Migration",
    "PutResult",
    "PutStatus",
    "VerifyReport",
]
