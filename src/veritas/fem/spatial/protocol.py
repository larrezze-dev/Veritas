"""Protocolo interno y efímero padre ↔ worker 3D (ADR-003, plan T-018, D2).

Mismas reglas que el protocolo plano (ADR-002 §5): JSON sin NaN/Inf, claves conocidas y
tipos estrictos; no es formato de proyecto. Base del solver = base global de ADR-001, seis
GDL por nudo (Ux, Uy, Uz, Rx, Ry, Rz). Handler Lagrange con ``alpha_sp`` y ``alpha_mp``
calibrados por el traductor (plan T-018, calibración de D3).
"""

from dataclasses import dataclass
from typing import Any, Final

from veritas.fem.adapters.protocol import (
    ProtocolError,
    WorkerStatus,
    _bool,
    _finite,
    _floats,
    _int,
    _list,
    _object,
    _str,
    dumps,
    loads,
)

PROTOCOL_ID: Final = "veritas.opensees.spatial"
PROTOCOL_VERSION: Final = 1
DOFS_PER_NODE: Final = 6
AXES: Final = 3  # Componentes de una terna de un nudo (traslaciones o giros).

type Vec3 = tuple[float, float, float]
type Six = tuple[float, float, float, float, float, float]


def _check_spatial_header(data: dict[str, Any]) -> None:
    if data.get("protocol") != PROTOCOL_ID or data.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("protocolo o versión no admitidos")


def _vec3(value: object, field: str) -> Vec3:
    x, y, z = _floats(value, field, 3)
    return x, y, z


def _six(value: object, field: str) -> Six:
    a, b, c, d, e, f = _floats(value, field, 6)
    return a, b, c, d, e, f


def _fixity(value: object) -> tuple[int, int, int, int, int, int]:
    items = tuple(_int(v, "fixity") for v in _list(value, "fixity", 6))
    if any(v not in (0, 1) for v in items):
        raise ProtocolError("fixity: se esperaba 0 o 1")
    a, b, c, d, e, f = items
    return a, b, c, d, e, f


@dataclass(frozen=True, slots=True)
class SpatialNode:
    """Nudo del dominio en coordenadas globales (m)."""

    tag: int
    entity_id: str
    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class SpatialBeam:
    """``elasticBeamColumn`` 3D con ``geomTransf Linear`` de vector ``vecxz`` = ez (ADR-001)."""

    tag: int
    entity_id: str
    node_i: int
    node_j: int
    area: float
    elastic_modulus: float
    shear_modulus: float
    torsion_constant: float
    inertia_y: float
    inertia_z: float
    vecxz: Vec3


@dataclass(frozen=True, slots=True)
class SpatialFix:
    """``fix`` sobre los seis GDL globales (1 = fijo)."""

    node: int
    fixity: tuple[int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class SpatialSpring:
    """``zeroLength`` del nudo de tierra ``ground`` (fijo, mismas coordenadas) al nudo.

    ``-orient`` = (``axis_x``, ``axis_yp``): base del apoyo. ``dof`` 1-3 son traslaciones y
    4-6 giros según esa base. ``penalty`` marca una componente rígida representada por
    penalización (D4). La reacción sobre la estructura en la base del apoyo es
    B·nodeReaction(ground) (plan T-018, hecho 10).
    """

    tag: int
    support_id: str
    node: int
    ground: int
    dof: int
    stiffness: float
    axis_x: Vec3
    axis_yp: Vec3
    penalty: bool


@dataclass(frozen=True, slots=True)
class SpatialChain:
    """Traslación fija según una normal no global, sin penalización (hechos 6, 8 y 11).

    El worker crea R en N − ``length``·``normal`` con traslaciones fijas, C en N,
    ``rigidLink beam`` R→C, ``equalDOF`` C→N en (1, 2, 3), y un ``zeroLength`` de rigidez
    ``stiffness`` en el giro de R alrededor de la normal, desde el nudo de tierra ``ground``
    (fijo, en R). ``tangent`` completa la orientación de ese resorte. La reacción del apoyo
    se lee en N.
    """

    tag: int
    support_id: str
    node: int
    c_node: int
    r_node: int
    ground: int
    normal: Vec3
    tangent: Vec3
    length: float
    stiffness: float


@dataclass(frozen=True, slots=True)
class SpatialNodalLoad:
    """Carga nodal global (Fx, Fy, Fz, Mx, My, Mz)."""

    node: int
    values: Six


@dataclass(frozen=True, slots=True)
class SpatialBeamLoad:
    """``-beamUniform`` (Wy, Wz, Wx) en la base local del elemento (hecho 9)."""

    beam: int
    wy: float
    wz: float
    wx: float


@dataclass(frozen=True, slots=True)
class SpatialRequest:
    """Petición completa y determinista de una corrida espacial."""

    model_revision: int
    case_id: str
    case_revision: int
    alpha_sp: float
    alpha_mp: float
    nodes: tuple[SpatialNode, ...]
    beams: tuple[SpatialBeam, ...]
    fixes: tuple[SpatialFix, ...]
    springs: tuple[SpatialSpring, ...]
    chains: tuple[SpatialChain, ...]
    nodal_loads: tuple[SpatialNodalLoad, ...]
    beam_loads: tuple[SpatialBeamLoad, ...]

    def to_dict(self) -> dict[str, Any]:
        """Diccionario JSON del protocolo."""
        return {
            "protocol": PROTOCOL_ID,
            "version": PROTOCOL_VERSION,
            "model_revision": self.model_revision,
            "case_id": self.case_id,
            "case_revision": self.case_revision,
            "alpha_sp": self.alpha_sp,
            "alpha_mp": self.alpha_mp,
            "nodes": [[n.tag, n.entity_id, n.x, n.y, n.z] for n in self.nodes],
            "beams": [
                [
                    b.tag,
                    b.entity_id,
                    b.node_i,
                    b.node_j,
                    b.area,
                    b.elastic_modulus,
                    b.shear_modulus,
                    b.torsion_constant,
                    b.inertia_y,
                    b.inertia_z,
                    list(b.vecxz),
                ]
                for b in self.beams
            ],
            "fixes": [[f.node, list(f.fixity)] for f in self.fixes],
            "springs": [
                [
                    s.tag,
                    s.support_id,
                    s.node,
                    s.ground,
                    s.dof,
                    s.stiffness,
                    list(s.axis_x),
                    list(s.axis_yp),
                    s.penalty,
                ]
                for s in self.springs
            ],
            "chains": [
                [
                    c.tag,
                    c.support_id,
                    c.node,
                    c.c_node,
                    c.r_node,
                    c.ground,
                    list(c.normal),
                    list(c.tangent),
                    c.length,
                    c.stiffness,
                ]
                for c in self.chains
            ],
            "nodal_loads": [[p.node, list(p.values)] for p in self.nodal_loads],
            "beam_loads": [[w.beam, w.wy, w.wz, w.wx] for w in self.beam_loads],
        }

    def to_json(self) -> str:
        """JSON determinista."""
        return dumps(self.to_dict())

    @classmethod
    def from_json(cls, text: str) -> "SpatialRequest":
        """Lee y valida una petición (pruebas de round-trip)."""
        data = _object(loads(text), "request", _REQUEST_KEYS)
        _check_spatial_header(data)
        nodes = tuple(
            SpatialNode(_int(n[0], "node"), _str(n[1], "node"), *_vec3(n[2:], "xyz"))
            for n in (_list(item, "node", 5) for item in _list(data["nodes"], "nodes"))
        )
        beams = tuple(_beam(_list(item, "beam", 11)) for item in _list(data["beams"], "beams"))
        fixes = tuple(
            SpatialFix(_int(f[0], "fix"), _fixity(f[1]))
            for f in (_list(item, "fix", 2) for item in _list(data["fixes"], "fixes"))
        )
        springs = tuple(
            SpatialSpring(
                _int(s[0], "spring"),
                _str(s[1], "support"),
                _int(s[2], "node"),
                _int(s[3], "ground"),
                _dof(s[4]),
                _finite(s[5], "k"),
                _vec3(s[6], "axis_x"),
                _vec3(s[7], "axis_yp"),
                _bool(s[8], "penalty"),
            )
            for s in (_list(item, "spring", 9) for item in _list(data["springs"], "springs"))
        )
        chains = tuple(
            SpatialChain(
                _int(c[0], "chain"),
                _str(c[1], "support"),
                _int(c[2], "node"),
                _int(c[3], "c_node"),
                _int(c[4], "r_node"),
                _int(c[5], "ground"),
                _vec3(c[6], "normal"),
                _vec3(c[7], "tangent"),
                _finite(c[8], "length"),
                _finite(c[9], "k"),
            )
            for c in (_list(item, "chain", 10) for item in _list(data["chains"], "chains"))
        )
        nodal = tuple(
            SpatialNodalLoad(_int(p[0], "node"), _six(p[1], "load"))
            for p in (_list(item, "load", 2) for item in _list(data["nodal_loads"], "loads"))
        )
        beam_loads = tuple(
            SpatialBeamLoad(_int(w[0], "beam"), *_floats(w[1:], "beam_load", 3))
            for w in (_list(item, "beam_load", 4) for item in _list(data["beam_loads"], "bl"))
        )
        return cls(
            _int(data["model_revision"], "model_revision"),
            _str(data["case_id"], "case_id"),
            _int(data["case_revision"], "case_revision"),
            _finite(data["alpha_sp"], "alpha_sp"),
            _finite(data["alpha_mp"], "alpha_mp"),
            nodes,
            beams,
            fixes,
            springs,
            chains,
            nodal,
            beam_loads,
        )


def _beam(b: list[Any]) -> SpatialBeam:
    area, elastic, shear, torsion, inertia_y, inertia_z = _floats(b[4:10], "section", 6)
    return SpatialBeam(
        _int(b[0], "beam"),
        _str(b[1], "beam"),
        _int(b[2], "node_i"),
        _int(b[3], "node_j"),
        area,
        elastic,
        shear,
        torsion,
        inertia_y,
        inertia_z,
        _vec3(b[10], "vecxz"),
    )


def _dof(value: object) -> int:
    dof = _int(value, "dof")
    if not 1 <= dof <= DOFS_PER_NODE:
        raise ProtocolError("dof: se esperaba 1-6")
    return dof


_REQUEST_KEYS: Final = frozenset(
    {
        "protocol",
        "version",
        "model_revision",
        "case_id",
        "case_revision",
        "alpha_sp",
        "alpha_mp",
        "nodes",
        "beams",
        "fixes",
        "springs",
        "chains",
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
        "grounds",
        "chains",
        "beams",
    }
)
_VERSION_KEYS: Final = frozenset({"python", "openseespy", "openseespywin"})


@dataclass(frozen=True, slots=True)
class SpatialChainResult:
    """Valores brutos de una cadena: desplazamientos de C y R y reacción de R."""

    tag: int
    c_disp: Six
    r_disp: Six
    r_reaction: Six


@dataclass(frozen=True, slots=True)
class SpatialWorkerResponse:
    """Respuesta validada del worker 3D. Valores brutos en la base global y local."""

    status: str
    analyze_code: int | None
    message: str
    versions: dict[str, str | None]
    node_results: tuple[tuple[int, Six, Six], ...]
    ground_reactions: tuple[tuple[int, Six], ...]
    chain_results: tuple[SpatialChainResult, ...]
    beam_forces: tuple[tuple[int, tuple[float, ...]], ...]

    @classmethod
    def from_json(cls, text: str) -> "SpatialWorkerResponse":
        """Valida estrictamente la respuesta; cualquier desviación es ``ProtocolError``."""
        data = _object(loads(text), "response", _RESPONSE_KEYS)
        _check_spatial_header(data)
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
            (_int(n[0], "node"), _six(n[1], "disp"), _six(n[2], "reaction"))
            for n in (_list(item, "node", 3) for item in _list(data["nodes"], "nodes"))
        )
        grounds = tuple(
            (_int(g[0], "ground"), _six(g[1], "ground_reaction"))
            for g in (_list(item, "ground", 2) for item in _list(data["grounds"], "grounds"))
        )
        chains = tuple(
            SpatialChainResult(
                _int(c[0], "chain"),
                _six(c[1], "c_disp"),
                _six(c[2], "r_disp"),
                _six(c[3], "r_reaction"),
            )
            for c in (_list(item, "chain", 4) for item in _list(data["chains"], "chains"))
        )
        beams = tuple(
            (_int(b[0], "beam"), _floats(b[1], "local_force", 12))
            for b in (_list(item, "beam", 2) for item in _list(data["beams"], "beams"))
        )
        return cls(
            status,
            analyze_code,
            _str(data["message"], "message"),
            versions,
            node_results,
            grounds,
            chains,
            beams,
        )
