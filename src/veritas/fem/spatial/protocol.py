"""Protocolo interno y efímero padre ↔ worker 3D (ADR-003, plan T-018, D2).

Mismas reglas que el protocolo plano (ADR-002 §5): JSON sin NaN/Inf, claves conocidas y
tipos estrictos; no es formato de proyecto. Base del solver = base global de ADR-001, seis
GDL por nudo (Ux, Uy, Uz, Rx, Ry, Rz). Handler Lagrange con ``alpha_sp`` y ``alpha_mp``
calibrados por el traductor (plan T-018, calibración de D3). v2 (E2): extremos de barra no
rígidos con ``-releasey``/``-releasez``, A y J efectivos, nudos aux y giros huérfanos.
v3 (ADR-004): penalización iterada en los resortes de penalización de extremo, con la
dirección global b_s y la escala S_s de cada uno y los parámetros de parada en la petición,
y las iteraciones, el motivo de parada y los multiplicadores λ_s en la respuesta.
"""

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Final

from veritas.fem.adapters.protocol import (
    NonFiniteValueError,
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
from veritas.fem.spatial.config import (
    DEFAULT_END_PENALTY_MAX_ITERATIONS,
    DEFAULT_END_PENALTY_TOLERANCE,
    DEFAULT_TOL_PENALTY,
)

PROTOCOL_ID: Final = "veritas.opensees.spatial"
PROTOCOL_VERSION: Final = 4  # 2: extremos (E2-b); 3: penalización iterada (ADR-004); 4: asientos
DOFS_PER_NODE: Final = 6
AXES: Final = 3  # Componentes de una terna de un nudo (traslaciones o giros).
RELEASE_CODES: Final = (0, 1, 2, 3)  # -releasey/-releasez: ninguno, I, J, ambos.
BEAM_ENDS: Final = ("I", "J")
# Motivo de parada de la penalización iterada (ADR-004 §3); ``none``: sin penalización de
# extremo (0 iteraciones).
STOP_TOLERANCE: Final = "tolerance"
STOP_STAGNATION: Final = "stagnation"
STOP_MAX_ITERATIONS: Final = "max_iterations"
STOP_NONE: Final = "none"
END_PENALTY_STOPS: Final = (STOP_TOLERANCE, STOP_STAGNATION, STOP_MAX_ITERATIONS, STOP_NONE)

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


def _mask(value: object, field: str, size: int) -> tuple[int, ...]:
    items = tuple(_int(v, field) for v in _list(value, field, size))
    if any(v not in (0, 1) for v in items):
        raise ProtocolError(f"{field}: se esperaba 0 o 1")
    return items


def _fixity(value: object) -> tuple[int, int, int, int, int, int]:
    a, b, c, d, e, f = _mask(value, "fixity", DOFS_PER_NODE)
    return a, b, c, d, e, f


def _rotation_mask(value: object) -> tuple[int, int, int]:
    x, y, z = _mask(value, "orphan", AXES)
    return x, y, z


def _release(value: object, field: str) -> int:
    code = _int(value, field)
    if code not in RELEASE_CODES:
        raise ProtocolError(f"{field}: se esperaba 0-3")
    return code


def _end(value: object) -> str:
    end = _str(value, "end")
    if end not in BEAM_ENDS:
        raise ProtocolError("end: se esperaba I o J")
    return end


def _dofs(value: object) -> tuple[int, ...]:
    dofs = tuple(_dof(v) for v in _list(value, "dofs"))
    if not dofs or any(a >= b for a, b in pairwise(dofs)):
        raise ProtocolError("dofs: se esperaba una lista creciente no vacía")
    return dofs


def _unique(values: list[int], field: str) -> None:
    if len(set(values)) != len(values):
        raise ProtocolError(f"{field}: nudo repetido")


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
    """``elasticBeamColumn`` 3D con ``geomTransf Linear`` de vector ``vecxz`` = ez (ADR-001).

    ``release_y``/``release_z``: códigos de ``-releasey``/``-releasez`` (0 ninguno, 1 I, 2 J,
    3 ambos; hecho 14). ``area`` y ``torsion_constant`` son efectivos (Ux y Rx no rígidos en
    serie, hechos 5 y 15); valen 0 si y solo si ``axial_released``/``torsion_released``
    (algún extremo liberado). El worker no usa las marcas: solo permiten validar el 0.
    """

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
    release_y: int = 0
    release_z: int = 0
    axial_released: bool = False
    torsion_released: bool = False


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
class SpatialOrphanFix:
    """Giros globales sin rigidez de un nudo que el traductor fija (plan T-018, R4).

    Separado de ``SpatialFix`` para que la normalización distinga ambas reacciones; el
    worker los fusiona en un único ``fix`` por nudo (OpenSees rechaza dos).
    """

    node: int
    rotations: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class SpatialAuxNode:
    """Nudo aux en las coordenadas de ``master``: el elemento ``beam`` se conecta a él en el
    extremo ``end`` (E2-a). Nunca lleva ``fix``; lo unen al maestro los enlaces de extremo."""

    tag: int
    master: int
    beam: int
    end: str


@dataclass(frozen=True, slots=True)
class SpatialEndEqual:
    """``equalDOF(maestro, aux, *dofs)`` en GDL globales 1-6 (componentes rígidas exactas)."""

    aux: int
    dofs: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SpatialEndSpring:
    """``zeroLength`` maestro→aux en la dirección ``dof`` de la base (``axis_x``, ``axis_yp``)
    del extremo, como ``SpatialSpring``. ``penalty`` marca una componente rígida representada
    por penalización (E2-a). Etiqueta de elemento = etiqueta de material."""

    tag: int
    aux: int
    dof: int
    stiffness: float
    axis_x: Vec3
    axis_yp: Vec3
    penalty: bool


type SpatialEndLink = SpatialEndEqual | SpatialEndSpring


@dataclass(frozen=True, slots=True)
class SpatialEndPenalty:
    """Datos de la penalización iterada de un ``SpatialEndSpring`` con ``penalty`` (ADR-004).

    ``direction``: b_s, dirección global unitaria del resorte (``spring_direction``); g_s =
    b_s·(u_aux − u_maestro) es su ``basicDeformation``. ``scale``: S_s ≥ 0, escala del paso
    relativo p_s = |k_s·g_s| / S_s (fuerza del caso para traslaciones, fuerza·L_máx para
    giros). La calcula el padre; el worker no deriva ninguna base.
    """

    spring: int
    direction: Vec3
    scale: float


def penalty_step(force: float, scale: float) -> float:
    """p_s = |k_s·g_s| / S_s (ADR-004 §3); con S_s = 0, 0 si la fuerza es 0 e ∞ si no.

    Misma regla que el worker, que no importa ``veritas``."""
    if scale > 0.0:
        return abs(force) / scale
    return 0.0 if force == 0.0 else float("inf")


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
class SpatialSettlement:
    """Asiento impuesto (T-016) en un GDL global 1-6 del nudo, en m o rad.

    El GDL debe estar en el ``fix`` del nudo; el worker lo retira del ``fix`` y lo impone
    con ``sp`` dentro del patrón de carga (hechos 1, 5 y 6 del plan).
    """

    node: int
    dof: int
    value: float


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
    orphan_fixes: tuple[SpatialOrphanFix, ...] = ()
    aux_nodes: tuple[SpatialAuxNode, ...] = ()
    end_equals: tuple[SpatialEndEqual, ...] = ()
    end_springs: tuple[SpatialEndSpring, ...] = ()
    end_penalties: tuple[SpatialEndPenalty, ...] = ()
    end_penalty_tolerance: float = DEFAULT_END_PENALTY_TOLERANCE
    end_penalty_stagnation: float = DEFAULT_TOL_PENALTY
    end_penalty_max_iterations: int = DEFAULT_END_PENALTY_MAX_ITERATIONS
    settlements: tuple[SpatialSettlement, ...] = ()

    def __post_init__(self) -> None:
        _check_settlements(self)

    def element_node(self, beam: SpatialBeam, end: str) -> int:
        """Nudo al que se conecta el elemento en ese extremo: aux si lo hay, si no el nudo."""
        for aux in self.aux_nodes:
            if aux.beam == beam.tag and aux.end == end:
                return aux.tag
        return beam.node_i if end == "I" else beam.node_j

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
                    b.release_y,
                    b.release_z,
                    b.axial_released,
                    b.torsion_released,
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
            "orphan_fixes": [[o.node, list(o.rotations)] for o in self.orphan_fixes],
            "aux_nodes": [[x.tag, x.master, x.beam, x.end] for x in self.aux_nodes],
            "end_equals": [[q.aux, list(q.dofs)] for q in self.end_equals],
            "end_springs": [
                [
                    s.tag,
                    s.aux,
                    s.dof,
                    s.stiffness,
                    list(s.axis_x),
                    list(s.axis_yp),
                    s.penalty,
                ]
                for s in self.end_springs
            ],
            "end_penalties": [[q.spring, list(q.direction), q.scale] for q in self.end_penalties],
            "end_penalty_tolerance": self.end_penalty_tolerance,
            "end_penalty_stagnation": self.end_penalty_stagnation,
            "end_penalty_max_iterations": self.end_penalty_max_iterations,
            "settlements": [[t.node, t.dof, t.value] for t in self.settlements],
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
        beams = tuple(_beam(_list(item, "beam", 15)) for item in _list(data["beams"], "beams"))
        fixes = tuple(
            SpatialFix(_int(f[0], "fix"), _fixity(f[1]))
            for f in (_list(item, "fix", 2) for item in _list(data["fixes"], "fixes"))
        )
        _unique([f.node for f in fixes], "fixes")
        orphans = tuple(
            SpatialOrphanFix(_int(o[0], "orphan"), _rotation_mask(o[1]))
            for o in (_list(item, "orphan", 2) for item in _list(data["orphan_fixes"], "orph"))
        )
        _unique([o.node for o in orphans], "orphan_fixes")
        aux_nodes = tuple(
            SpatialAuxNode(_int(x[0], "aux"), _int(x[1], "master"), _int(x[2], "beam"), _end(x[3]))
            for x in (_list(item, "aux", 4) for item in _list(data["aux_nodes"], "aux_nodes"))
        )
        if len({(x.beam, x.end) for x in aux_nodes}) != len(aux_nodes):
            raise ProtocolError("aux_nodes: extremo de barra repetido")
        end_equals = tuple(
            SpatialEndEqual(_int(q[0], "aux"), _dofs(q[1]))
            for q in (_list(item, "end_equal", 2) for item in _list(data["end_equals"], "eq"))
        )
        end_springs = tuple(
            SpatialEndSpring(
                _int(s[0], "end_spring"),
                _int(s[1], "aux"),
                _dof(s[2]),
                _finite(s[3], "k"),
                _vec3(s[4], "axis_x"),
                _vec3(s[5], "axis_yp"),
                _bool(s[6], "penalty"),
            )
            for s in (_list(item, "end_spring", 7) for item in _list(data["end_springs"], "es"))
        )
        end_penalties = tuple(
            SpatialEndPenalty(_int(q[0], "end_penalty"), _vec3(q[1], "b"), _finite(q[2], "S"))
            for q in (_list(item, "end_penalty", 3) for item in _list(data["end_penalties"], "ep"))
        )
        _check_end_penalties(end_springs, end_penalties)
        tolerance = _finite(data["end_penalty_tolerance"], "end_penalty_tolerance")
        stagnation = _finite(data["end_penalty_stagnation"], "end_penalty_stagnation")
        if not 0.0 < tolerance < stagnation:
            raise ProtocolError("end_penalty: se esperaba 0 < tolerancia < estancamiento")
        max_iterations = _int(data["end_penalty_max_iterations"], "end_penalty_max_iterations")
        if max_iterations < 1:
            raise ProtocolError("end_penalty_max_iterations: se esperaba ≥ 1")
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
        settlements = tuple(
            SpatialSettlement(_int(t[0], "node"), _dof(t[1]), _finite(t[2], "value"))
            for t in (_list(item, "settlement", 3) for item in _list(data["settlements"], "st"))
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
            orphans,
            aux_nodes,
            end_equals,
            end_springs,
            end_penalties,
            tolerance,
            stagnation,
            max_iterations,
            settlements,
        )


def _check_settlements(request: SpatialRequest) -> None:
    """Validación estricta (plan T-016, D6): GDL 1-6 en el ``fix`` del nudo, sin repetir, y
    un giro asentado nunca figura en ``orphan_fixes`` (revisión, hallazgo 9). Con asientos no
    hay penalizaciones ni cadenas (D4-bis-A; revisión de E2.2, M3)."""
    penalized = any(s.penalty for s in request.springs) or any(
        s.penalty for s in request.end_springs
    )
    if request.settlements and (penalized or request.chains or request.end_penalties):
        raise ProtocolError("asientos con penalizaciones o cadenas (D4-bis-A)")
    fixity = {fix.node: fix.fixity for fix in request.fixes}
    orphans = {orphan.node: orphan.rotations for orphan in request.orphan_fixes}
    seen: set[tuple[int, int]] = set()
    for settlement in request.settlements:
        node, dof = settlement.node, settlement.dof
        if not 1 <= dof <= DOFS_PER_NODE:
            raise ProtocolError(f"asiento: GDL no válido {dof}")
        if (node, dof) in seen:
            raise ProtocolError(f"asiento repetido en el nudo {node}")
        seen.add((node, dof))
        if not fixity.get(node, (0,) * DOFS_PER_NODE)[dof - 1]:
            raise ProtocolError(f"asiento fuera del fix del nudo {node}")
        if dof > AXES and orphans.get(node, (0, 0, 0))[dof - AXES - 1]:
            raise ProtocolError(f"giro asentado en orphan_fixes del nudo {node}")
        if not math.isfinite(settlement.value):
            raise NonFiniteValueError("asiento: valor no finito")


def _check_end_penalties(
    springs: tuple[SpatialEndSpring, ...], penalties: tuple[SpatialEndPenalty, ...]
) -> None:
    """Una entrada por cada resorte de extremo con ``penalty`` y ninguna más; S_s ≥ 0."""
    tags = [q.spring for q in penalties]
    expected = sorted(s.tag for s in springs if s.penalty)
    if sorted(tags) != expected or len(set(tags)) != len(tags):
        raise ProtocolError("end_penalties: no corresponden a los resortes de penalización")
    if any(q.scale < 0.0 for q in penalties):
        raise ProtocolError("end_penalties: escala negativa")


def _beam(b: list[Any]) -> SpatialBeam:
    area, elastic, shear, torsion, inertia_y, inertia_z = _floats(b[4:10], "section", 6)
    axial_released, torsion_released = _bool(b[13], "axial"), _bool(b[14], "torsion")
    # E2-b: A y J efectivos ≥ 0, y 0 solo con el extremo correspondiente liberado.
    for value, released, field in (
        (area, axial_released, "area"),
        (torsion, torsion_released, "torsion"),
    ):
        if value < 0.0 or (value == 0.0) != released:
            raise ProtocolError(f"{field}: 0 si y solo si el extremo está liberado")
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
        _release(b[11], "release_y"),
        _release(b[12], "release_z"),
        axial_released,
        torsion_released,
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
        "orphan_fixes",
        "aux_nodes",
        "end_equals",
        "end_springs",
        "end_penalties",
        "end_penalty_tolerance",
        "end_penalty_stagnation",
        "end_penalty_max_iterations",
        "settlements",
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
        "aux",
        "end_springs",
        "end_penalty_iterations",
        "end_penalty_stop",
        "end_penalty_converged",
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
    aux_results: tuple[tuple[int, Six, Six], ...] = ()
    # (tag, basicForce = k·g, basicDeformation = g, λ de la última resolución); λ = 0 en
    # los resortes sin penalización. Fuerza del enlace = λ + k·g (ADR-004 §6).
    end_spring_results: tuple[tuple[int, float, float, float], ...] = ()
    end_penalty_iterations: int = 0
    end_penalty_stop: str = STOP_NONE
    end_penalty_converged: bool = True

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
        aux = tuple(
            (_int(x[0], "aux"), _six(x[1], "aux_disp"), _six(x[2], "aux_reaction"))
            for x in (_list(item, "aux", 3) for item in _list(data["aux"], "aux"))
        )
        # basicForce y basicDeformation del zeroLength de una dirección (E2.5, F = k·Δ) y
        # el multiplicador λ de la penalización iterada (ADR-004 §5).
        end_springs = tuple(
            (
                _int(s[0], "end_spring"),
                _finite(s[1], "force"),
                _finite(s[2], "deformation"),
                _finite(s[3], "multiplier"),
            )
            for s in (_list(item, "end_spring", 4) for item in _list(data["end_springs"], "es"))
        )
        iterations = _int(data["end_penalty_iterations"], "end_penalty_iterations")
        stop = _str(data["end_penalty_stop"], "end_penalty_stop")
        converged = _bool(data["end_penalty_converged"], "end_penalty_converged")
        # 0 iteraciones ⇔ sin penalización (none); convergida ⇔ no se agotó el máximo.
        if (
            stop not in END_PENALTY_STOPS
            or iterations < 0
            or (iterations == 0) != (stop == STOP_NONE)
            or converged != (stop != STOP_MAX_ITERATIONS)
        ):
            raise ProtocolError("end_penalty: iteraciones, motivo y convergencia incoherentes")
        return cls(
            status,
            analyze_code,
            _str(data["message"], "message"),
            versions,
            node_results,
            grounds,
            chains,
            beams,
            aux,
            end_springs,
            iterations,
            stop,
            converged,
        )
