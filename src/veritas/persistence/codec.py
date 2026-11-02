"""Codec genérico de dataclasses de resultados a JSON, con validación estricta (plan T-012, E2).

Las corridas y sus resultados (``AnalysisRun``, ``SpatialAnalysisRun``, ``RawPlanarResults``,
``PlanarResults``…) son datos puros: se serializan por reflexión sobre sus campos y anotaciones
de tipo, sin lógica propia. ``encode`` escribe TODOS los campos; ``decode`` exige el conjunto
exacto de campos, los tipos y los rangos, y construye cada objeto con su constructor (que
aplica sus invariantes). Un floats se escribe con su ``repr`` (incluido −0,0); NaN e
infinitos se rechazan en ambos sentidos. Los enums se guardan por valor (``StrEnum``) o por
nombre (``Enum`` simple); las fechas, en ISO 8601 con zona.
"""

import math
import types
import typing
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, Final
from uuid import UUID

from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.persistence.errors import format_error, value_error

_NONE_TYPE: Final = type(None)


def encode(value: object, path: str = "$") -> Any:  # noqa: PLR0911 (un retorno por tipo)
    """Estructura JSON de una dataclass de resultados (sin pérdida)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, str | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise value_error(DiagnosticCode.INPUT_NONFINITE, "project.nonfinite", {"path": path})
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise value_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "project.invalid_value",
                {"path": path, "reason": "fecha sin zona horaria"},
            )
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name), f"{path}.{f.name}") for f in fields(value)}
    if isinstance(value, tuple | list):
        return [encode(item, f"{path}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, dict):
        return {str(k): encode(v, f"{path}.{k}") for k, v in value.items()}
    raise TypeError(f"Valor no serializable en {path}: {type(value).__name__}")


def _bad(path: str, expected: str) -> DiagnosticError:
    return format_error("project.invalid_field", {"expected": expected}, path=path)


def _unwrap(tp: Any) -> Any:
    while isinstance(tp, typing.TypeAliasType):
        tp = tp.__value__
    return tp


def decode(tp: Any, data: object, path: str = "$") -> Any:  # noqa: PLR0911, PLR0912
    """Construye un valor del tipo anotado ``tp`` desde su estructura JSON."""
    tp = _unwrap(tp)
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is typing.Literal:
        if data in args and not isinstance(data, bool):
            return data
        raise _bad(path, "uno de: " + ", ".join(map(str, args)))
    if origin is types.UnionType or origin is typing.Union:
        return _decode_union(args, data, path)
    if origin is tuple:
        return _decode_tuple(args, data, path)
    if origin is dict:
        if not isinstance(data, dict):
            raise _bad(path, "objeto")
        return {str(k): decode(args[1], v, f"{path}.{k}") for k, v in data.items()}
    if tp is bool:
        if isinstance(data, bool):
            return data
        raise _bad(path, "booleano")
    if tp is str:
        if isinstance(data, str):
            return data
        raise _bad(path, "texto")
    if tp is int:
        if isinstance(data, int) and not isinstance(data, bool):
            return data
        raise _bad(path, "entero")
    if tp is float:
        if isinstance(data, bool) or not isinstance(data, int | float):
            raise _bad(path, "número")
        number = float(data)
        if not math.isfinite(number):
            raise value_error(DiagnosticCode.INPUT_NONFINITE, "project.nonfinite", {"path": path})
        return number
    if tp is UUID:
        return _decode_uuid(data, path)
    if tp is datetime:
        return _decode_datetime(data, path)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return _decode_enum(tp, data, path)
    if isinstance(tp, type) and is_dataclass(tp):
        return _decode_dataclass(tp, data, path)
    raise TypeError(f"Tipo no soportado por el codec en {path}: {tp!r}")  # pragma: no cover


def _decode_union(args: tuple[Any, ...], data: object, path: str) -> Any:
    if data is None:
        if _NONE_TYPE in args:
            return None
        raise _bad(path, "no nulo")
    members = [a for a in args if a is not _NONE_TYPE]
    if len(members) == 1:
        return decode(members[0], data, path)
    # Uniones de primitivos (``str | float | int``): se conserva el tipo JSON.
    for member in members:
        if isinstance(data, bool):
            break
        if (
            (member is str and isinstance(data, str))
            or (member is int and isinstance(data, int))
            or (member is float and isinstance(data, float))
        ):
            return decode(member, data, path)
    raise _bad(path, " o ".join(getattr(m, "__name__", str(m)) for m in members))


def _decode_tuple(args: tuple[Any, ...], data: object, path: str) -> tuple[Any, ...]:
    if not isinstance(data, list):
        raise _bad(path, "lista")
    if len(args) == 2 and args[1] is Ellipsis:  # noqa: PLR2004 (tuple[X, ...])
        return tuple(decode(args[0], item, f"{path}[{i}]") for i, item in enumerate(data))
    if len(data) != len(args):
        raise _bad(path, f"lista de {len(args)} elementos")
    return tuple(
        decode(a, item, f"{path}[{i}]") for i, (a, item) in enumerate(zip(args, data, strict=True))
    )


def _decode_uuid(data: object, path: str) -> UUID:
    if isinstance(data, str):
        try:
            parsed = UUID(data)
        except ValueError:
            parsed = None
        if parsed is not None and str(parsed) == data:
            return parsed
    raise _bad(path, "UUID en minúsculas")


def _decode_datetime(data: object, path: str) -> datetime:
    if isinstance(data, str):
        try:
            parsed = datetime.fromisoformat(data)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None and parsed.isoformat() == data:
            return parsed
    raise _bad(path, "fecha ISO 8601 con zona horaria")


def _decode_enum(tp: type[Enum], data: object, path: str) -> Enum:
    if isinstance(data, str):
        if issubclass(tp, StrEnum):
            try:
                return tp(data)
            except ValueError:
                pass
        elif data in tp.__members__:
            return tp[data]
    allowed = ", ".join(str(m.value if issubclass(tp, StrEnum) else m.name) for m in tp)
    raise _bad(path, f"uno de: {allowed}")


def _decode_dataclass(tp: type, data: object, path: str) -> Any:
    if not isinstance(data, dict):
        raise _bad(path, "objeto")
    names = [f.name for f in fields(tp)]
    unknown = sorted(set(data) - set(names))
    if unknown:
        raise format_error("project.unknown_fields", {"fields": ", ".join(unknown)}, path=path)
    missing = [n for n in names if n not in data]
    if missing:
        raise format_error("project.missing_fields", {"fields": ", ".join(missing)}, path=path)
    hints = typing.get_type_hints(tp)
    values = {n: decode(hints[n], data[n], f"{path}.{n}") for n in names}
    try:
        return tp(**values)
    except DiagnosticError:
        raise
    except (TypeError, ValueError) as error:
        raise format_error(
            "project.invalid_value", {"reason": str(error)[:200]}, path=path
        ) from None
