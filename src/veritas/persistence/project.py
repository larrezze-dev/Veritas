"""Guardar y abrir el archivo de proyecto (plan T-012, D1, D5 y D6; DAT-001, NFR-003, NFR-006).

Un único JSON UTF-8 con la envoltura ``{format, schema_version, app_version, canon_version,
content, model_hash, integrity}``. ``integrity`` es el SHA-256 de la codificación canónica del
resto de la envoltura (protege también las etiquetas); ``model_hash`` es el hash del contenido
ANALÍTICO del modelo (``canonical.model_hash``). Abrir es todo o nada: cualquier fallo es un
``DiagnosticError`` y no se devuelve un modelo parcial. Guardar escribe un temporal en el mismo
directorio, copia el archivo previo a ``.bak`` y lo reemplaza con ``os.replace``: un fallo
deja intacto el archivo anterior. El archivo es solo datos; nunca se ejecuta nada de él.
"""

import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from veritas import __version__
from veritas.core.diagnostics import DiagnosticCode
from veritas.core.geometry.model import StructuralModel
from veritas.core.presentation import PresentationProfile
from veritas.persistence.canonical import CANON_VERSION, canonical_bytes, model_hash, sha256_hex
from veritas.persistence.errors import (
    format_error,
    integrity_error,
    value_error,
)
from veritas.persistence.migrations import CURRENT_SCHEMA_VERSION, migrate
from veritas.persistence.runs import RunRecord, decode_record, encode_record
from veritas.persistence.schema import (
    Json,
    Reader,
    decode_profile,
    decode_snapshot,
    encode_profile,
    encode_snapshot,
)

FORMAT: Final = "veritas-project"
INTEGRITY_ALGORITHM: Final = "sha256"
MAX_FILE_BYTES: Final = 64 * 1024 * 1024
MAX_DEPTH: Final = 32
_ENVELOPE: Final = (
    "format",
    "schema_version",
    "app_version",
    "canon_version",
    "content",
    "model_hash",
    "integrity",
)
_CONTENT: Final = ("model", "settings", "runs")
_SETTINGS: Final = ("user_profiles",)
_INTEGRITY: Final = ("algorithm", "digest")
_ALGORITHM_FIELD: Final = "algorithm"
_MODEL_PATH: Final = "content" + ".model"
_RUNS_PATH: Final = "content" + ".runs"
_PROFILES_PATH: Final = "content" + ".settings" + ".user_profiles"


@dataclass(frozen=True, slots=True)
class Project:
    """Proyecto abierto o guardado: modelo, ajustes de presentación y metadatos del archivo."""

    model: StructuralModel
    user_profiles: tuple[PresentationProfile, ...]
    schema_version: int
    app_version: str
    canon_version: str
    model_hash: str
    integrity: str
    runs: tuple[RunRecord, ...] = ()


def _body(
    model: StructuralModel,
    profiles: tuple[PresentationProfile, ...],
    runs: tuple[RunRecord, ...],
    app: str,
) -> Json:
    snapshot = model.snapshot()
    return {
        "format": FORMAT,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "app_version": app,
        "canon_version": CANON_VERSION,
        "content": {
            "model": encode_snapshot(snapshot),
            "settings": {"user_profiles": [encode_profile(p) for p in profiles]},
            "runs": [encode_record(r) for r in runs],
        },
        "model_hash": model_hash(snapshot),
    }


def save_project(
    path: str | Path,
    model: StructuralModel,
    user_profiles: Iterable[PresentationProfile] = (),
    *,
    runs: Iterable[RunRecord] = (),
    app_version: str = __version__,
) -> Project:
    """Guarda el proyecto de forma atómica; los perfiles integrados no se guardan.

    ``runs`` son las ejecuciones históricas (inmutables, solo se añaden; ``append_runs``).
    """
    records = tuple(runs)
    _check_unique_runs(records)
    profiles = tuple(p for p in user_profiles if not p.builtin)
    names = [p.name for p in profiles]
    if len(set(names)) != len(names):
        raise value_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "project.invalid_value",
            {"path": _PROFILES_PATH, "reason": "nombres de perfil repetidos"},
        )
    body = _body(model, profiles, records, app_version)
    digest = sha256_hex(canonical_bytes(body))
    envelope = {**body, "integrity": {"algorithm": INTEGRITY_ALGORITHM, "digest": digest}}
    text = json.dumps(envelope, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False)
    try:
        _write_atomic(Path(path), (text + "\n").encode("utf-8"))
    except OSError as error:
        raise value_error(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            "project.write_failed",
            {"reason": error.strerror or str(error)},
        ) from None
    return Project(
        model,
        profiles,
        CURRENT_SCHEMA_VERSION,
        app_version,
        CANON_VERSION,
        str(body["model_hash"]),
        digest,
        records,
    )


def _write_atomic(target: Path, data: bytes) -> None:
    """Temporal en el mismo directorio, copia ``.bak`` del anterior y ``os.replace``."""
    directory = target.parent
    handle, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=directory)
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            shutil.copyfile(target, target.with_name(target.name + ".bak"))
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _check_unique_runs(records: tuple[RunRecord, ...]) -> None:
    ids = [r.run_id for r in records]
    if len(set(ids)) != len(ids):
        raise value_error(
            DiagnosticCode.REVISION_CONFLICT,
            "project.duplicate_run",
            {"run_id": next(str(i) for i in ids if ids.count(i) > 1)},
        )


def append_runs(path: str | Path, new: Iterable[RunRecord]) -> Project:
    """Añade ejecuciones a un proyecto existente sin modificar las anteriores (DAT-004).

    Abre el archivo (con toda su validación), rechaza un ``run_id`` repetido y lo guarda de
    nuevo con las corridas previas intactas y las nuevas al final.
    """
    current = open_project(path)
    return save_project(
        path,
        current.model,
        current.user_profiles,
        runs=(*current.runs, *new),
    )


# --- Apertura ----------------------------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise format_error("project.duplicate_key", {"key": key})
        result[key] = value
    return result


def _no_constants(name: str) -> object:
    raise format_error("project.nonfinite_literal", {"value": name})


def _depth(value: object) -> int:
    deepest, stack = 0, [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, dict | list):
            deepest = max(deepest, level)
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, level + 1) for child in children)
    return deepest


def _parse(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8")
        data = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except UnicodeDecodeError as error:
        raise format_error("project.invalid_json", {"reason": f"UTF-8: {error.reason}"}) from None
    except json.JSONDecodeError as error:
        raise format_error(
            "project.invalid_json", {"reason": f"{error.msg} (posición {error.pos})"}
        ) from None
    except RecursionError:
        raise value_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "project.too_deep", {"limit": MAX_DEPTH}
        ) from None
    if _depth(data) > MAX_DEPTH:
        raise value_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "project.too_deep", {"limit": MAX_DEPTH}
        )
    return data


def _read_bytes(path: Path) -> bytes:
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise value_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "project.too_large",
                {"size": size, "limit": MAX_FILE_BYTES},
            )
        return path.read_bytes()
    except OSError as error:
        raise format_error("project.unreadable", {"reason": error.strerror or str(error)}) from None


def _check_integrity(data: dict[str, object]) -> str:
    stored = data.get("integrity")
    if not isinstance(stored, dict):
        raise format_error("project.missing_fields", {"fields": "integrity"})
    entry = Reader(stored, "integrity", _INTEGRITY)
    if entry.text("algorithm") != INTEGRITY_ALGORITHM:
        raise format_error(
            "project.invalid_field",
            {"expected": INTEGRITY_ALGORITHM},
            path="integrity" + "." + _ALGORITHM_FIELD,
        )
    expected = entry.text("digest")
    body = {key: value for key, value in data.items() if key != "integrity"}
    actual = sha256_hex(canonical_bytes(body))
    if actual != expected:
        raise integrity_error(expected, actual)
    return expected


def open_project(path: str | Path) -> Project:
    """Abre y valida un proyecto; todo o nada. ``DiagnosticError`` si algo falla."""
    data = _parse(_read_bytes(Path(path)))
    if not isinstance(data, dict):
        raise format_error("project.invalid_field", {"expected": "objeto"})
    found = data.get("format")
    if found != FORMAT:
        raise format_error("project.bad_format", {"found": str(found)})
    digest = _check_integrity(data)
    content = data.get("content")
    if not isinstance(content, dict):
        raise format_error("project.invalid_field", {"expected": "objeto"}, path="content")
    data["content"] = migrate(content, data.get("schema_version"))
    envelope = Reader(data, "", _ENVELOPE)
    # La versión del esquema ya está validada por ``migrate``; la envoltura migrada es la actual.
    version = envelope.integer("schema_version")
    body = envelope.obj("content", _CONTENT)
    model = StructuralModel.restore(decode_snapshot(body.raw("model"), _MODEL_PATH))
    settings = body.obj("settings", _SETTINGS)
    profiles = tuple(
        decode_profile(item, f"{_PROFILES_PATH}[{i}]")
        for i, item in enumerate(settings.items("user_profiles"))
    )
    if len({p.name for p in profiles}) != len(profiles):
        raise format_error(
            "project.invalid_value",
            {"reason": "nombres de perfil repetidos"},
            path=_PROFILES_PATH,
        )
    records = tuple(
        decode_record(item, f"{_RUNS_PATH}[{i}]") for i, item in enumerate(body.items("runs"))
    )
    _check_unique_runs(records)
    canon = envelope.text("canon_version")
    computed = model_hash(model.snapshot())
    stored_hash = envelope.text("model_hash")
    if canon != CANON_VERSION:
        raise value_error(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            "project.unsupported_canon",
            {"found": canon, "supported": CANON_VERSION},
        )
    if stored_hash != computed:
        raise integrity_error(stored_hash, computed)
    return Project(
        model, profiles, version, envelope.text("app_version"), canon, stored_hash, digest, records
    )
