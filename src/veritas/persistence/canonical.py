"""Codificación canónica ``veritas-canon/1`` y hash SHA-256 del modelo analítico (ADR-006).

Dos usos con la misma serialización base (JSON UTF-8 sin espacios, claves ordenadas, floats con
su ``repr`` más corto exacto, NaN e infinitos rechazados):

- ``canonical_bytes(..., normalize_zero=False)``: resumen de integridad del ARCHIVO (conserva
  −0,0 y las etiquetas);
- ``model_hash``: hash del contenido ANALÍTICO del modelo. ``normalize_zero=True`` (−0,0 y 0,0
  dan el mismo hash) y se excluyen los datos de presentación y la numeración de revisión:
  etiquetas, nombres y descripciones de casos, ``revision`` de nudos, barras, apoyos, casos y
  cargas, y ``model_revision``. Entran el id y el contenido de cada entidad y SOLO las
  revisiones de material y de sección que alguna barra referencia, con su número de revisión.
  Las listas se ordenan por id: el orden de inserción no cambia el hash.
"""

import hashlib
import json
import math
from typing import Final

from veritas.core.diagnostics import DiagnosticCode
from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.persistence.errors import value_error
from veritas.persistence.schema import Json, encode_snapshot

CANON_VERSION: Final = "veritas-canon/1"


def _prepare(value: object, normalize_zero: bool, path: str) -> object:
    if isinstance(value, dict):
        return {str(k): _prepare(v, normalize_zero, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_prepare(v, normalize_zero, f"{path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise value_error(DiagnosticCode.INPUT_NONFINITE, "project.nonfinite", {"path": path})
        return 0.0 if normalize_zero and value == 0.0 else value
    if value is None or isinstance(value, str | int | bool):
        return value
    raise TypeError(f"Valor no canonizable en {path}: {type(value).__name__}")


def canonical_bytes(value: object, *, normalize_zero: bool = False) -> bytes:
    """Bytes canónicos de una estructura JSON."""
    return json.dumps(
        _prepare(value, normalize_zero, "$"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """SHA-256 en hexadecimal minúscula."""
    return hashlib.sha256(data).hexdigest()


def _without(entry: object, *names: str) -> Json:
    if not isinstance(entry, dict):  # pragma: no cover (estructura propia)
        raise TypeError("entrada de modelo no es un objeto")
    return {k: v for k, v in entry.items() if k not in names}


def _by_id(entries: list[Json]) -> list[Json]:
    return sorted(entries, key=lambda e: str(e["id"]))


def analytic_form(snapshot: ModelSnapshot) -> Json:
    """Estructura del contenido analítico del modelo (sin presentación ni revisiones)."""
    encoded = encode_snapshot(snapshot)
    used_materials = {(str(m.material.entity_id), m.material.revision) for m in snapshot.members}
    used_sections = {(str(m.section.entity_id), m.section.revision) for m in snapshot.members}

    def items(name: str) -> list[Json]:
        raw = encoded[name]
        assert isinstance(raw, list)
        return raw

    return {
        "nodes": _by_id([_without(n, "label", "revision") for n in items("nodes")]),
        "members": _by_id([_without(m, "label", "revision") for m in items("members")]),
        "materials": _by_id(
            [m for m in items("materials") if (str(m["id"]), m["revision"]) in used_materials]
        ),
        "sections": _by_id(
            [s for s in items("sections") if (str(s["id"]), s["revision"]) in used_sections]
        ),
        "supports": _by_id([_without(s, "label", "revision") for s in items("supports")]),
        "load_cases": _by_id(
            [_without(c, "name", "description", "revision") for c in items("load_cases")]
        ),
        "loads": _by_id([_without(load, "revision") for load in items("loads")]),
    }


def model_hash(snapshot: ModelSnapshot) -> str:
    """SHA-256 (hex) del contenido analítico del modelo con la codificación ``veritas-canon/1``."""
    document = {"canon": CANON_VERSION, "model": analytic_form(snapshot)}
    return sha256_hex(canonical_bytes(document, normalize_zero=True))
