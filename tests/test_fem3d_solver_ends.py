"""Puertas del worker 3D con extremos no rígidos (plan T-018, E2.1: hechos 14-16, R3, R4, R7).

Montaje de las sondas: I empotrado (nudo 1), B1 de I a J (nudo 2) con el extremo no rígido,
B2 rígida de J a K empotrado (nudo 3), ``-beamUniform`` en B1 y carga nodal en J.

Referencia independiente de OpenSees: la 12×12 de ``stiffness.py`` con un GDL extra local
por componente de extremo liberada o con resorte (resorte entre el extra y la componente
del nudo), resuelta en Python puro. Equivale a la condensación estática exacta. Con Rx o Ux
liberados en los dos extremos la condensación es singular y la referencia es J = 0 / A = 0
en la 12×12 propia (R7). Criterio exacto |e| ≤ 1e-9·escala.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from test_fem3d_solver import (
    IY,
    IZ,
    LOAD,
    OBLIQUE,
    REL,
    A,
    E,
    G,
    J,
    Matrix,
    _cross,
    _solve,
    _unit,
    assert_close,
    assert_disp,
    node,
    run,
)
from veritas.core.geometry.local_axes import compute_local_axes
from veritas.core.geometry.vector import Vec3
from veritas.fem.spatial.protocol import (
    Six,
    SpatialAuxNode,
    SpatialBeam,
    SpatialBeamLoad,
    SpatialChain,
    SpatialEndEqual,
    SpatialEndSpring,
    SpatialFix,
    SpatialNodalLoad,
    SpatialNode,
    SpatialOrphanFix,
    SpatialRequest,
    SpatialWorkerResponse,
)
from veritas.fem.spatial.protocol import Vec3 as Triple
from veritas.fem.spatial.stiffness import (
    beam_axes,
    beam_transformation,
    fixed_end_forces,
    local_beam_stiffness,
)

pytestmark = pytest.mark.solver

LENGTH = 3.0
B2_OFFSET = (0.7, -1.1, 1.9)
B2_VECXZ = (0.3, 0.2, 0.9)
W = (4e3, -3e3, 2e3)  # (Wy, Wz, Wx) local de B1.
W_NO_AXIAL = (4e3, -3e3, 0.0)  # Ux no rígido exige Wx = 0 (D5, E2-d).
PENALTY_FACTOR = 1e8
RECORD_BOUND = 1e-4  # Cota de registro (cableado), no la calibración de E2-c.
EPS = 2.2e-16
FULL_FIX = (1, 1, 1, 1, 1, 1)

# Índices locales de localForce y de la 12×12: (ux, uy, uz, rx, ry, rz) de I y luego de J.
UX, RX, RY, RZ = 0, 3, 4, 5
END_OFFSET = {"I": 0, "J": 6}
CODE_ENDS = {1: ("I",), 2: ("J",), 3: ("I", "J")}

type Extras = Mapping[int, float | None]  # Índice local → rigidez del resorte o None (libre).


def _vecxz(tip: Triple, alpha: float) -> Triple:
    return compute_local_axes(Vec3(0.0, 0.0, 0.0), Vec3(*tip), alpha=alpha).ez.components()


def _tip(direction: Triple) -> Triple:
    d = _unit(direction)
    return d[0] * LENGTH, d[1] * LENGTH, d[2] * LENGTH


type Orientation = tuple[Triple, float]  # (dirección de B1, alfa).

ORIENTATIONS = [
    pytest.param(((1.0, 0.0, 0.0), 0.0), id="+X"),
    pytest.param((OBLIQUE, 0.37), id="oblicua"),
]


class EndModel:
    """B1 (I→J, extremo no rígido) + B2 rígida (J→K); ``propped``: J empotrado y sin B2."""

    def __init__(  # noqa: PLR0913 (geometría y sección de la referencia, por nombre)
        self,
        direction: Triple,
        alpha: float,
        w: Triple = W,
        *,
        propped: bool = False,
        b2_vecxz: Triple = B2_VECXZ,
        torsion: float = J,
    ):
        self.b2_vecxz, self.torsion = b2_vecxz, torsion
        self.xj = _tip(direction)
        self.xk: Triple = (
            self.xj[0] + B2_OFFSET[0],
            self.xj[1] + B2_OFFSET[1],
            self.xj[2] + B2_OFFSET[2],
        )
        self.vecxz = _vecxz(self.xj, alpha)
        self.w = w
        self.propped = propped
        self.length, self.axes = beam_axes((0.0, 0.0, 0.0), self.xj, self.vecxz)
        self.t = beam_transformation(self.axes)
        rot = 4 * E * max(IY, IZ) / self.length
        self.alpha = rot + 4 * E * max(IY, IZ) / math.dist(self.xj, self.xk)

    # --- Referencia (Python puro, sin OpenSees) ---------------------------------------
    def reference(
        self,
        extras: Extras,
        *,
        area: float = A,
        torsion: float | None = None,
        normals: Sequence[Triple] = (),
    ) -> "Reference":
        """z = [u_J (6, global; ninguno si ``propped``), extras]; restricciones n·u_J = 0."""
        torsion = self.torsion if torsion is None else torsion
        beam = SpatialBeam(1, "b1", 1, 2, area, E, G, torsion, IY, IZ, self.vecxz)
        k = local_beam_stiffness(beam, self.length)
        p0 = fixed_end_forces(SpatialBeamLoad(1, *self.w), self.length)
        free = 0 if self.propped else 6
        order = sorted(extras)
        size = free + len(order)
        # d_local = M·z: componente del nudo (T·u_J) o GDL extra propio.
        attached: Matrix = [[0.0] * size for _ in range(12)]
        if free:
            for i in range(6, 12):
                for c in range(6):
                    attached[i][c] = self.t[i][6 + c]
        m = [row[:] for row in attached]
        for position, i in enumerate(order):
            m[i] = [0.0] * size
            m[i][free + position] = 1.0
        km = [[sum(k[i][r] * m[r][c] for r in range(12)) for c in range(size)] for i in range(12)]
        kz = [
            [sum(m[r][a] * km[r][b] for r in range(12)) for b in range(size)] for a in range(size)
        ]
        f = [-sum(m[r][a] * p0[r] for r in range(12)) for a in range(size)]
        if free:
            b2 = SpatialBeam(2, "b2", 2, 3, A, E, G, self.torsion, IY, IZ, self.b2_vecxz)
            length2, axes2 = beam_axes(self.xj, self.xk, self.b2_vecxz)
            k2, t2 = local_beam_stiffness(b2, length2), beam_transformation(axes2)
            for a in range(6):
                f[a] += LOAD[a]
                for b in range(6):
                    kz[a][b] += sum(
                        t2[r][a] * k2[r][s] * t2[s][b] for r in range(12) for s in range(12)
                    )
        for i, stiffness in extras.items():
            if stiffness is not None:  # Resorte: δ = extra − componente del nudo.
                g = [m[i][c] - attached[i][c] for c in range(size)]
                for a in range(size):
                    for b in range(size):
                        kz[a][b] += stiffness * g[a] * g[b]
        rows = [[*n, 0.0, 0.0, 0.0] + [0.0] * (size - 6) for n in normals]
        system = [row + [r[a] for r in rows] for a, row in enumerate(kz)]
        system += [r + [0.0] * len(rows) for r in rows]
        z = _solve(system, f + [0.0] * len(rows))[:size]
        d = [sum(m[i][c] * z[c] for c in range(size)) for i in range(12)]
        force = [sum(k[i][r] * d[r] for r in range(12)) + p0[i] for i in range(12)]
        nodal = [sum(attached[i][c] * z[c] for c in range(size)) for i in range(12)]
        delta = {i: d[i] - nodal[i] for i in extras}
        u = tuple(z[:6]) if free else (0.0,) * 6
        return Reference(u, d, nodal, force, delta, self.axes)

    # --- Petición del worker -------------------------------------------------------------
    def request(self, beam: SpatialBeam | None = None, **extra: object) -> SpatialRequest:
        b1 = beam or SpatialBeam(1, "b1", 1, 2, A, E, G, J, IY, IZ, self.vecxz)
        nodes = [SpatialNode(1, "n1", 0.0, 0.0, 0.0), SpatialNode(2, "n2", *self.xj)]
        beams = [b1]
        fixes = [SpatialFix(1, FULL_FIX)]
        nodal: tuple[SpatialNodalLoad, ...] = ()
        if self.propped:
            fixes.append(SpatialFix(2, FULL_FIX))
        else:
            nodes.append(SpatialNode(3, "n3", *self.xk))
            beams.append(SpatialBeam(2, "b2", 2, 3, A, E, G, J, IY, IZ, B2_VECXZ))
            fixes.append(SpatialFix(3, FULL_FIX))
            nodal = (SpatialNodalLoad(2, LOAD),)
        fields: dict[str, object] = {
            "model_revision": 1,
            "case_id": "c",
            "case_revision": 1,
            "alpha_sp": self.alpha,
            "alpha_mp": self.alpha,
            "nodes": tuple(nodes),
            "beams": tuple(beams),
            "fixes": tuple(fixes),
            "springs": (),
            "chains": (),
            "nodal_loads": nodal,
            "beam_loads": (SpatialBeamLoad(1, *self.w),),
        }
        fields.update(extra)
        return SpatialRequest(**fields)  # type: ignore[arg-type]

    def beam(self, area: float = A, torsion_constant: float = J, **fields: object) -> SpatialBeam:
        """B1 con A y J efectivos y los campos de extremo de E2-b."""
        return SpatialBeam(
            1,
            "b1",
            1,
            2,
            area,
            E,
            G,
            torsion_constant,
            IY,
            IZ,
            self.vecxz,
            **fields,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class Reference:
    """Solución de referencia: u_J global; d, componente del nudo y localForce locales de B1;
    δ de cada extra."""

    u: Sequence[float]
    d: list[float]
    nodal: list[float]
    force: list[float]
    delta: dict[int, float]
    axes: tuple[Triple, Triple, Triple]

    def end_global(self, end: str, element_released: Sequence[int] = ()) -> list[float]:
        """Desplazamiento global del aux en ese extremo: Qᵀ·d_extremo. Un giro liberado con
        ``-release*`` es interno al elemento; el aux lo tiene atado al nudo (R3)."""
        o = END_OFFSET[end]
        local = [self.nodal[i] if i in element_released else self.d[i] for i in range(12)]
        return [
            sum(self.axes[r][c] * local[o + b + r] for r in range(3))
            for b in (0, 3)
            for c in range(3)
        ]


def beam_force(response: SpatialWorkerResponse, tag: int = 1) -> tuple[float, ...]:
    return next(f for t, f in response.beam_forces if t == tag)


def assert_force(
    actual: Sequence[float], expected: Sequence[float], zero: Sequence[int] = ()
) -> None:
    """localForce frente a la referencia, por fuerzas y por momentos; ``zero`` por índice."""
    forces = [i for i in range(12) if i % 6 < 3]
    moments = [i for i in range(12) if i % 6 >= 3]
    for group in (forces, moments):
        scale = max(abs(expected[i]) for i in group)
        assert_close([actual[i] for i in group], [expected[i] for i in group], scale)
    scale = max(abs(v) for v in expected)
    for i in zero:  # Componente liberada: momento nulo (hecho 14).
        assert abs(actual[i]) <= REL * scale, (i, actual[i])


def _released(component: str, code: int) -> list[int]:
    local = {"y": [RY], "z": [RZ], "yz": [RY, RZ]}[component]
    return [END_OFFSET[end] + i for end in CODE_ENDS[code] for i in local]


# --- Hecho 14: -releasey/-releasez con -beamUniform ------------------------------------------


@pytest.mark.parametrize("orientation", ORIENTATIONS)
@pytest.mark.parametrize("code", [1, 2, 3], ids=["I", "J", "ambos"])
@pytest.mark.parametrize("component", ["y", "z", "yz"])
def test_released_bending_matches_condensation(
    orientation: Orientation, code: int, component: str
) -> None:
    """``-releasey`` libera My (índices 4/10) y ``-releasez`` Mz (5/11), sin cruzarse."""
    model = EndModel(*orientation)
    codes = {"release_y": code if "y" in component else 0}
    codes["release_z"] = code if "z" in component else 0
    released = _released(component, code)
    response = run(model.request(model.beam(**codes)))
    ref = model.reference(dict.fromkeys(released))
    assert_disp(node(response, 2)[0], ref.u, model.length)
    assert_force(beam_force(response), ref.force, zero=released)


@pytest.mark.parametrize("orientation", ORIENTATIONS)
@pytest.mark.parametrize("component", ["y", "z"])
def test_propped_cantilever_is_exact(orientation: Orientation, component: str) -> None:
    """Puerta de D5 (hecho 14): ménsula apuntalada, q uniforme y el giro liberado en J.

    R_J = 3qL/8 y M_I = qL²/8 con los signos de ADR-001 (W en −z local da pJz > 0 y
    pI_My < 0; en −y local da pJy > 0 y pI_Mz > 0) y momento en J nulo.
    """
    q = 10e3
    w = (0.0, -q, 0.0) if component == "y" else (-q, 0.0, 0.0)
    model = EndModel(*orientation, w, propped=True)
    codes = {"release_y": 2} if component == "y" else {"release_z": 2}
    force = beam_force(run(model.request(model.beam(**codes))))
    ell = model.length
    shear, moment, released = (8, 4, 10) if component == "y" else (7, 5, 11)
    sign = -1.0 if component == "y" else 1.0
    scale = q * ell**2 / 8
    assert abs(force[shear] - 3 * q * ell / 8) <= REL * scale
    assert abs(force[moment] - sign * q * ell**2 / 8) <= REL * scale
    assert abs(force[released]) <= REL * scale


# --- Hecho 15: Rx y Ux en serie (J y A efectivos) --------------------------------------------


def _series(stiffness: float, length: float, rigidity: float) -> float:
    """Propiedad efectiva de la barra con un resorte en serie: (L/M)/(L/(M·P) + 1/k)."""
    return length / (length / rigidity + 1.0 / stiffness)


SERIES_CASES = [
    pytest.param({RX: None}, {"torsion_released": True}, id="Rx libre en I"),
    pytest.param({6 + RX: None}, {"torsion_released": True}, id="Rx libre en J"),
    pytest.param({6 + RX: 2e6}, {}, id="Rx resorte en J"),
    pytest.param({UX: None}, {"axial_released": True}, id="Ux libre en I"),
    pytest.param({6 + UX: None}, {"axial_released": True}, id="Ux libre en J"),
    pytest.param({6 + UX: 5e8}, {}, id="Ux resorte en J"),
    pytest.param(
        {6 + RX: None, 6 + RY: None, 6 + RZ: None},
        {"torsion_released": True, "release_y": 2, "release_z": 2},
        id="Rx, Ry y Rz libres en J",
    ),
]


@pytest.mark.parametrize("orientation", ORIENTATIONS)
@pytest.mark.parametrize(("extras", "fields"), SERIES_CASES)
def test_axial_and_torsion_in_series_match_condensation(
    orientation: Orientation, extras: dict[int, float | None], fields: dict[str, object]
) -> None:
    """Hechos 5 y 15: A y J efectivos (0 si está liberado) frente a la condensación; Wx = 0."""
    model = EndModel(*orientation, W_NO_AXIAL)
    area, torsion = A, J
    for index, stiffness in extras.items():
        if index % 6 == RX:
            torsion = 0.0 if stiffness is None else _series(stiffness, model.length, G * J) / G
        elif index % 6 == UX:
            area = 0.0 if stiffness is None else _series(stiffness, model.length, E * A) / E
    response = run(model.request(model.beam(area=area, torsion_constant=torsion, **fields)))
    ref = model.reference(extras)
    assert_disp(node(response, 2)[0], ref.u, model.length)
    released = [i for i, k in extras.items() if k is None]
    assert_force(beam_force(response), ref.force, zero=released)


def test_both_ends_released_use_zero_torsion_and_area() -> None:
    """R7: Rx y Ux libres en los dos extremos; Kcc singular, referencia con J = 0 y A = 0."""
    model = EndModel(OBLIQUE, 0.37, W_NO_AXIAL)
    beam = model.beam(area=0.0, torsion_constant=0.0, axial_released=True, torsion_released=True)
    response = run(model.request(beam))
    ref = model.reference({}, area=0.0, torsion=0.0)
    assert_disp(node(response, 2)[0], ref.u, model.length)
    assert_force(beam_force(response), ref.force, zero=[UX, RX, 6 + UX, 6 + RX])


# --- Hecho 16: aux de extremo (equalDOF + zeroLength) ----------------------------------------


def _end_stiffness(local: int, length: float) -> float:
    """Resorte del orden de la barra: 3·EIy/L en giros y 3·12EIy/L³ en traslaciones."""
    return 3 * E * IY / length if local >= RX else 36 * E * IY / length**3


AUX_COMPONENTS = [("Uy", 1), ("Uz", 2), ("Ry", RY), ("Rz", RZ)]
AUX_STATES = ["resorte", "libre"]


def _aux_cases() -> list[tuple[str, int, str]]:
    # Ry/Rz liberados nunca usan aux: van con -releasey/-releasez (E2-a).
    return [
        (label, local, state)
        for label, local in AUX_COMPONENTS
        for state in AUX_STATES
        if not (local >= RX and state == "libre")
    ]


def _check_aux(
    model: EndModel,
    response: SpatialWorkerResponse,
    ref: Reference,
    tag: int,
    element_released: Sequence[int] = (),
) -> tuple[float, ...]:
    """u_aux (R11) y reacciones. Con Lagrange, nodeReaction del aux y del maestro llevan el
    multiplicador del equalDOF, iguales y opuestos (hecho 17): su suma es la reacción del
    nudo. Aquí J no tiene apoyo propio, así que la suma es ≈ 0 salvo otra restricción."""
    aux_disp, aux_reaction = next((d, r) for t, d, r in response.aux_results if t == tag)
    assert_disp(aux_disp, ref.end_global("J", element_released), model.length)
    total = tuple(a + b for a, b in zip(node(response, 2)[1], aux_reaction, strict=True))
    assert max(abs(v) for v in aux_reaction) > 0.0  # El multiplicador sí aparece.
    return total


@pytest.mark.parametrize(("label", "local", "state"), _aux_cases(), ids=str)
def test_aligned_aux_is_exact(label: str, local: int, state: str) -> None:
    """Base local = global (+X, alfa = 0): equalDOF en los rígidos y zeroLength exacto."""
    model = EndModel((1.0, 0.0, 0.0), 0.0)
    ex, ey, ez = model.axes
    assert (ex, ey, ez) == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    stiffness = _end_stiffness(local, model.length) if state == "resorte" else None
    springs = (
        (SpatialEndSpring(40, 10, local + 1, stiffness, ex, ey, False),)
        if stiffness is not None
        else ()
    )
    response = run(
        model.request(
            aux_nodes=(SpatialAuxNode(10, 2, 1, "J"),),
            end_equals=(SpatialEndEqual(10, tuple(d + 1 for d in range(6) if d != local)),),
            end_springs=springs,
        )
    )
    ref = model.reference({6 + local: stiffness})
    assert_disp(node(response, 2)[0], ref.u, model.length)
    assert_force(beam_force(response), ref.force)
    assert_close(_check_aux(model, response, ref, 10), [0.0] * 6, max(abs(v) for v in LOAD))
    assert [tag for tag, _f, _d in response.end_spring_results] == [s.tag for s in springs]
    for _tag, force, deformation in response.end_spring_results:
        assert stiffness is not None
        assert abs(deformation - ref.delta[6 + local]) <= REL * max(abs(v) for v in ref.u)
        assert abs(force - stiffness * deformation) <= REL * abs(force)


@pytest.mark.parametrize(("label", "local", "state"), _aux_cases(), ids=str)
def test_oblique_aux_with_penalty_is_recorded(label: str, local: int, state: str) -> None:
    """Barra oblicua: zeroLength en los ejes locales con penalización 1e8 en los rígidos.

    Registra el error (hecho 16: hasta 8,1e-7; en este montaje, hasta 4,1e-6). La cota es de
    registro; la calibración de la penalización de extremo es la puerta E2-c (pieza E2.4).
    Con los giros semirrígidos las traslaciones van con equalDOF global (E2-a).
    """
    model = EndModel(OBLIQUE, 0.37)
    ex, ey, _ez = model.axes
    stiffness = _end_stiffness(local, model.length) if state == "resorte" else None
    lengths = (model.length, math.dist(model.xj, model.xk))
    rot = PENALTY_FACTOR * sum(
        max(4 * E * IY / ell, 4 * E * IZ / ell, G * J / ell) for ell in lengths
    )
    trans = PENALTY_FACTOR * sum(
        max(E * A / ell, 12 * E * IY / ell**3, 12 * E * IZ / ell**3) for ell in lengths
    )
    rotation_only = local >= RX
    equals = (SpatialEndEqual(10, (1, 2, 3)),) if rotation_only else ()
    springs = []
    for d in range(3 if rotation_only else 0, 6):
        if d == local:
            if stiffness is not None:
                springs.append(SpatialEndSpring(40 + d, 10, d + 1, stiffness, ex, ey, False))
        else:
            penalty = rot if d >= RX else trans
            springs.append(SpatialEndSpring(40 + d, 10, d + 1, penalty, ex, ey, True))
    response = run(
        model.request(
            aux_nodes=(SpatialAuxNode(10, 2, 1, "J"),),
            end_equals=equals,
            end_springs=tuple(springs),
        )
    )
    ref = model.reference({6 + local: stiffness})
    disp = node(response, 2)[0]
    trans_scale = max(abs(v) for v in ref.u[:3])
    rot_scale = max(*(abs(v) for v in ref.u[3:]), trans_scale / model.length)
    error_t = max(abs(a - b) for a, b in zip(disp[:3], ref.u[:3], strict=True)) / trans_scale
    error_r = max(abs(a - b) for a, b in zip(disp[3:], ref.u[3:], strict=True)) / rot_scale
    print(f"hecho 16, {label} {state}: error traslación {error_t:.1e}, giro {error_r:.1e}")
    assert max(error_t, error_r) <= RECORD_BOUND


# --- Combinaciones del worker (R3) y huérfanos (R4) -------------------------------------------


def test_release_and_aux_on_the_same_end() -> None:
    """R3: -releasey en J y un aux con Rz en resorte en el mismo extremo; Ry se ata rígido."""
    model = EndModel((1.0, 0.0, 0.0), 0.0)
    ex, ey, _ez = model.axes
    stiffness = _end_stiffness(RZ, model.length)
    response = run(
        model.request(
            model.beam(release_y=2),
            aux_nodes=(SpatialAuxNode(10, 2, 1, "J"),),
            end_equals=(SpatialEndEqual(10, (1, 2, 3, 4, 5)),),
            end_springs=(SpatialEndSpring(40, 10, 6, stiffness, ex, ey, False),),
        )
    )
    ref = model.reference({6 + RY: None, 6 + RZ: stiffness})
    assert_disp(node(response, 2)[0], ref.u, model.length)
    assert_force(beam_force(response), ref.force, zero=[6 + RY])
    total = _check_aux(model, response, ref, 10, element_released=[6 + RY])
    assert_close(total, [0.0] * 6, max(abs(v) for v in LOAD))


def test_chain_node_is_master_of_an_aux() -> None:
    """R3: cadena C→N y aux N→aux encadenados con Lagrange; exacto y alias aux−N."""
    model = EndModel((1.0, 0.0, 0.0), 0.0)
    ex, ey, _ez = model.axes
    normal = _unit((0.3, -0.5, 0.81))
    tangent = _unit(_cross(normal, (1.0, 0.0, 0.0)))
    stiffness = _end_stiffness(RZ, model.length)
    chain = SpatialChain(20, "r", 2, 21, 22, 23, normal, tangent, model.length, model.alpha)
    response = run(
        model.request(
            chains=(chain,),
            aux_nodes=(SpatialAuxNode(10, 2, 1, "J"),),
            end_equals=(SpatialEndEqual(10, (1, 2, 3, 4, 5)),),
            end_springs=(SpatialEndSpring(40, 10, 6, stiffness, ex, ey, False),),
        )
    )
    ref = model.reference({6 + RZ: stiffness}, normals=(normal,))
    disp = node(response, 2)[0]
    assert_disp(disp, ref.u, model.length)
    assert_force(beam_force(response), ref.force)
    # Reacción del nudo = N + aux (hecho 17): la de la cadena, −λ·n, sin momento (hecho 8).
    total = _check_aux(model, response, ref, 10)
    force_scale = max(abs(v) for v in LOAD[:3])
    assert_close(_cross(total[:3], normal), [0.0] * 3, force_scale)
    assert_close(total[3:], [0.0] * 3, force_scale * model.length)
    aux = next(d for t, d, _r in response.aux_results if t == 10)
    trans, rot = max(abs(v) for v in disp[:3]), max(abs(v) for v in disp[3:])
    for k in range(5):  # Alias de los GDL atados con equalDOF (cota de D3).
        assert abs(aux[k] - disp[k]) <= 1e3 * EPS * (trans if k < 3 else rot)


@pytest.mark.parametrize("support", [(0, 0, 1, 0, 0, 0), None], ids=["con apoyo", "sin apoyo"])
def test_orphan_fix_merges_with_the_support_fix(support: Six | None) -> None:
    """R4: apoyo y huérfano en el mismo nudo dan un único fix; igual que el fix fusionado."""
    model = EndModel(OBLIQUE, 0.37)
    base = model.request()
    rotations = (1, 0, 1)
    fixes = base.fixes + ((SpatialFix(2, support),) if support else ())  # type: ignore[arg-type]
    merged_fixity = tuple(
        max(a, b) for a, b in zip(support or (0,) * 6, (0, 0, 0, *rotations), strict=True)
    )
    orphan = run(model.request(fixes=fixes, orphan_fixes=(SpatialOrphanFix(2, rotations),)))
    merged = run(model.request(fixes=(*base.fixes, SpatialFix(2, merged_fixity))))  # type: ignore[arg-type]
    expected = node(merged, 2)[0]
    assert_disp(node(orphan, 2)[0], expected, model.length)
    assert node(orphan, 2)[0][3] == 0.0
    assert node(orphan, 2)[0][5] == 0.0
