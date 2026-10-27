"""Protocolo interno y efímero padre ↔ worker OpenSeesPy (ADR-002 §5, plan T-007).

JSON sin NaN/Inf, claves conocidas y tipos estrictos. No es formato de proyecto ni
contrato de interoperabilidad. Base del solver: x₁ = X, x₂ = Z, θ = −Ry (G3).
"""

import json
import math
from dataclasses import dataclass
from typing import Any, Final

PROTOCOL_ID: Final = "veritas.opensees.planar"
PROTOCOL_VERSION: Final = 1

type Pair = tuple[float, float]
type Triple = tuple[float, float, float]


class ProtocolError(ValueError):
    """Mensaje del protocolo inválido: la corrida falla, nunca se interpreta a medias."""


class NonFiniteValueError(ProtocolError):
    """El mensaje contiene NaN o infinito (p. ej. un resultado no finito del solver)."""


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProtocolError(f"{field}: se esperaba un número")
    number = float(value)
    if not math.isfinite(number):
        raise NonFiniteValueError(f"{field}: valor no finito")
    return number


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{field}: se esperaba un entero")
    return value


def _str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"{field}: se esperaba texto")
    return value


def _list(value: object, field: str, length: int | None = None) -> list[Any]:
    if not isinstance(value, list) or (length is not None and len(value) != length):
        raise ProtocolError(f"{field}: lista no válida")
    return value


def _floats(value: object, field: str, length: int) -> tuple[float, ...]:
    return tuple(_finite(item, field) for item in _list(value, field, length))


def _pair(value: object, field: str) -> Pair:
    first, second = _floats(value, field, 2)
    return first, second


def _object(value: object, field: str, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ProtocolError(f"{field}: claves no válidas")
    return value


def _check_header(data: dict[str, Any]) -> None:
    if data.get("protocol") != PROTOCOL_ID or data.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("protocolo o versión no admitidos")


def dumps(data: object) -> str:
    """JSON determinista sin NaN/Inf."""
    return json.dumps(data, allow_nan=False, sort_keys=True, separators=(",", ":"))


def loads(text: str) -> object:
    """JSON que rechaza NaN/Infinity."""

    def reject(constant: str) -> float:
        raise NonFiniteValueError(f"constante JSON no admitida: {constant}")

    try:
        return json.loads(text, parse_constant=reject)
    except json.JSONDecodeError as error:
        raise ProtocolError(f"JSON no válido: {error.msg}") from None


@dataclass(frozen=True, slots=True)
class PlanarNode:
    """Nudo del dominio en la base del solver (m)."""

    tag: int
    entity_id: str
    x1: float
    x2: float


@dataclass(frozen=True, slots=True)
class PlanarBeam:
    """``elasticBeamColumn`` 2D. ``inertia_axis`` indica la inercia usada (Iy o Iz)."""

    tag: int
    entity_id: str
    node_i: int
    node_j: int
    area: float
    elastic_modulus: float
    inertia: float
    inertia_axis: str


@dataclass(frozen=True, slots=True)
class PlanarFix:
    """``fix`` sobre GDL globales del plano (1 = fijo)."""

    node: int
    fixity: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class PlanarSpring:
    """``zeroLength`` del nudo a un nudo auxiliar fijo, en la base del apoyo.

    ``dof`` 1 sigue ``axis1``, 2 sigue ``axis2`` y 3 es el giro. ``penalty`` marca una
    traslación oblicua rígida representada por penalización (D4).
    """

    tag: int
    support_id: str
    node: int
    aux_node: int
    dof: int
    stiffness: float
    axis1: Pair
    axis2: Pair
    penalty: bool


@dataclass(frozen=True, slots=True)
class PlanarNodalLoad:
    """Carga nodal en la base del solver: (P₁, P₂, M) = (Fx, Fz, −My)."""

    node: int
    p1: float
    p2: float
    m: float


@dataclass(frozen=True, slots=True)
class PlanarBeamLoad:
    """``-beamUniform`` (Wy, Wx) en la base 2D del elemento (G2)."""

    beam: int
    wy: float
    wx: float


@dataclass(frozen=True, slots=True)
class PlanarRequest:
    """Petición completa y determinista de una corrida."""

    model_revision: int
    case_id: str
    case_revision: int
    nodes: tuple[PlanarNode, ...]
    beams: tuple[PlanarBeam, ...]
    fixes: tuple[PlanarFix, ...]
    springs: tuple[PlanarSpring, ...]
    nodal_loads: tuple[PlanarNodalLoad, ...]
    beam_loads: tuple[PlanarBeamLoad, ...]

    def to_dict(self) -> dict[str, Any]:
        """Diccionario JSON del protocolo."""
        return {
            "protocol": PROTOCOL_ID,
            "version": PROTOCOL_VERSION,
            "model_revision": self.model_revision,
            "case_id": self.case_id,
            "case_revision": self.case_revision,
            "nodes": [[n.tag, n.entity_id, n.x1, n.x2] for n in self.nodes],
            "beams": [
                [
                    b.tag,
                    b.entity_id,
                    b.node_i,
                    b.node_j,
                    b.area,
                    b.elastic_modulus,
                    b.inertia,
                    b.inertia_axis,
                ]
                for b in self.beams
            ],
            "fixes": [[f.node, list(f.fixity)] for f in self.fixes],
            "springs": [
                [
                    s.tag,
                    s.support_id,
                    s.node,
                    s.aux_node,
                    s.dof,
                    s.stiffness,
                    list(s.axis1),
                    list(s.axis2),
                    s.penalty,
                ]
                for s in self.springs
            ],
            "nodal_loads": [[p.node, p.p1, p.p2, p.m] for p in self.nodal_loads],
            "beam_loads": [[w.beam, w.wy, w.wx] for w in self.beam_loads],
        }

    def to_json(self) -> str:
        """JSON determinista (D6)."""
        return dumps(self.to_dict())

    @classmethod
    def from_json(cls, text: str) -> "PlanarRequest":
        """Lee y valida una petición (pruebas de round-trip)."""
        data = _object(loads(text), "request", _REQUEST_KEYS)
        _check_header(data)
        nodes = tuple(
            PlanarNode(_int(t, "node"), _str(e, "node"), _finite(x1, "x1"), _finite(x2, "x2"))
            for t, e, x1, x2 in (_list(n, "node", 4) for n in _list(data["nodes"], "nodes"))
        )
        beams = tuple(
            PlanarBeam(
                _int(b[0], "beam"),
                _str(b[1], "beam"),
                _int(b[2], "node_i"),
                _int(b[3], "node_j"),
                _finite(b[4], "A"),
                _finite(b[5], "E"),
                _finite(b[6], "I"),
                _str(b[7], "axis"),
            )
            for b in (_list(item, "beam", 8) for item in _list(data["beams"], "beams"))
        )
        fixes = tuple(
            PlanarFix(
                _int(f[0], "fix"),
                tuple(_int(v, "fixity") for v in _list(f[1], "fixity", 3)),  # type: ignore[arg-type]
            )
            for f in (_list(item, "fix", 2) for item in _list(data["fixes"], "fixes"))
        )
        springs = tuple(
            PlanarSpring(
                _int(s[0], "spring"),
                _str(s[1], "support"),
                _int(s[2], "node"),
                _int(s[3], "aux"),
                _int(s[4], "dof"),
                _finite(s[5], "k"),
                _pair(s[6], "axis1"),
                _pair(s[7], "axis2"),
                _bool(s[8], "penalty"),
            )
            for s in (_list(item, "spring", 9) for item in _list(data["springs"], "springs"))
        )
        nodal = tuple(
            PlanarNodalLoad(_int(p[0], "node"), *_floats(p[1:], "load", 3))
            for p in (_list(item, "load", 4) for item in _list(data["nodal_loads"], "loads"))
        )
        beam_loads = tuple(
            PlanarBeamLoad(_int(w[0], "beam"), *_floats(w[1:], "beam_load", 2))
            for w in (_list(item, "beam_load", 3) for item in _list(data["beam_loads"], "bl"))
        )
        return cls(
            _int(data["model_revision"], "model_revision"),
            _str(data["case_id"], "case_id"),
            _int(data["case_revision"], "case_revision"),
            nodes,
            beams,
            fixes,
            springs,
            nodal,
            beam_loads,
        )


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError(f"{field}: se esperaba booleano")
    return value


_REQUEST_KEYS: Final = frozenset(
    {
        "protocol",
        "version",
        "model_revision",
        "case_id",
        "case_revision",
        "nodes",
        "beams",
        "fixes",
        "springs",
        "nodal_loads",
        "beam_loads",
    }
)
_RESPONSE_KEYS: Final = frozenset(
    {
        "protocol",
        "version",
        "status",
        "analyze_code",
        "message",
        "versions",
        "nodes",
        "aux",
        "beams",
    }
)
_VERSION_KEYS: Final = frozenset({"python", "openseespy", "openseespywin"})


class WorkerStatus:
    """Estados que puede declarar el worker."""

    OK: Final = "OK"
    ANALYZE_FAILED: Final = "ANALYZE_FAILED"
    SOLVER_ERROR: Final = "SOLVER_ERROR"
    PROTOCOL_ERROR: Final = "PROTOCOL_ERROR"
    ALL: Final = frozenset({OK, ANALYZE_FAILED, SOLVER_ERROR, PROTOCOL_ERROR})


@dataclass(frozen=True, slots=True)
class WorkerResponse:
    """Respuesta validada del worker. Valores brutos en la base del solver."""

    status: str
    analyze_code: int | None
    message: str
    versions: dict[str, str | None]
    node_results: tuple[tuple[int, Triple, Triple], ...]
    aux_reactions: tuple[tuple[int, Triple], ...]
    beam_forces: tuple[tuple[int, tuple[float, ...]], ...]

    @classmethod
    def from_json(cls, text: str) -> "WorkerResponse":
        """Valida estrictamente la respuesta; cualquier desviación es ``ProtocolError``."""
        data = _object(loads(text), "response", _RESPONSE_KEYS)
        _check_header(data)
        status = _str(data["status"], "status")
        if status not in WorkerStatus.ALL:
            raise ProtocolError(f"estado desconocido: {status}")
        code = data["analyze_code"]
        analyze_code = None if code is None else _int(code, "analyze_code")
        versions_raw = _object(data["versions"], "versions", _VERSION_KEYS)
        versions = {
            key: None if value is None else _str(value, key) for key, value in versions_raw.items()
        }
        node_results = tuple(
            (_int(n[0], "node"), _floats(n[1], "disp", 3), _floats(n[2], "reaction", 3))
            for n in (_list(item, "node", 3) for item in _list(data["nodes"], "nodes"))
        )
        aux = tuple(
            (_int(a[0], "aux"), _floats(a[1], "aux_reaction", 3))
            for a in (_list(item, "aux", 2) for item in _list(data["aux"], "aux"))
        )
        beams = tuple(
            (_int(b[0], "beam"), _floats(b[1], "local_force", 6))
            for b in (_list(item, "beam", 2) for item in _list(data["beams"], "beams"))
        )
        return cls(
            status,
            analyze_code,
            _str(data["message"], "message"),
            versions,
            node_results,  # type: ignore[arg-type]
            aux,  # type: ignore[arg-type]
            beams,
        )
