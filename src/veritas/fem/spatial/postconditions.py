"""Controles de la solución espacial tras el equilibrio (plan T-018, pieza 5a; D3 y D6).

Se evalúan en el proceso padre sobre la respuesta del worker 3D, con la formulación de
``stiffness.py`` (Euler–Bernoulli 3D, restricciones exactas):

- Equilibrio global ΣR + ΣP = 0, momentos respecto al centroide de los nudos.
- Esfuerzos por barra: localForce ≈ k·T·u + p0, fila a fila.
- Alias de la cadena (D3, DEV-2): u_C = u_N, n·u_N = 0 y u_C = θ_R × (C − R), con la cota
  ``CHAIN_ALIAS_FACTOR``·eps·máx(‖u_tras‖∞, a·‖θ‖∞) del modelo; reacción de R ≈ 0
  (hecho 8) con la misma c.
- Residuo Tᵀ(K·u − F) por coordenada reducida, sin penalización (``full_stiffness``).
- Estimador de penalización (D6): |b·θ| + eps·``penalty_factor``·‖θ‖∞ ≤
  ``tol_penalty``·‖θ‖∞ en cada resorte de penalización (error de modelo y de redondeo).

Toda cota se evalúa como ``not (valor <= cota)``: un NaN da fallo. Las cotas de redondeo
c·eps·Σ|k_ij·u_j| usan la c del plano (``PENALTY_EQUILIBRIUM_FACTOR``). Los controles
contrastan OpenSees con la salvaguarda; no validan la traducción ni la normalización.
"""

import math
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from veritas.fem.postconditions import PENALTY_EQUILIBRIUM_FACTOR, CheckFailure
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import (
    AXES,
    DOFS_PER_NODE,
    Six,
    SpatialRequest,
    SpatialWorkerResponse,
)
from veritas.fem.spatial.stiffness import (
    beam_matrices,
    constrained_directions,
    cross,
    dot,
    fixed_end_forces,
    free_directions,
    full_stiffness,
    load_vector,
    reduced_coordinates,
    spring_direction,
    unit,
)

# c de la cota de alias de la cadena c·eps·‖u_tras‖∞ (D3). Batería de la pieza 5a: ≤ 1,4
# (margen de unas 700×); hecho 7, con escala de nudo: 22 (45×). Detalle en plans/T-018.md.
CHAIN_ALIAS_FACTOR: Final = 1.0e3
_EPS: Final = sys.float_info.epsilon
_ROUNDING: Final = PENALTY_EQUILIBRIUM_FACTOR * _EPS

# Nombres de control para el diagnóstico (parámetro ``control``).
CONTROL_CHAIN_ALIAS: Final = "alias de traslación del nudo C"
CONTROL_CHAIN_NORMAL: Final = "traslación según la normal"
CONTROL_CHAIN_LINK: Final = "enlace rígido R→C"
CONTROL_CHAIN_SUPPORT_FORCE: Final = "fuerza de reacción del nudo R"
CONTROL_CHAIN_SUPPORT_MOMENT: Final = "momento de reacción del nudo R"

type Triple = tuple[float, float, float]
type Displacements = Mapping[int, Sequence[float]]


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(dot(v, v))


def _exceeds(value: float, bound: float) -> bool:
    """Fallo si el valor supera la cota o si la cota no es finita (NaN o ±inf)."""
    return not (value <= bound) or not math.isfinite(bound)


def _largest(values: Sequence[float]) -> float:
    """máx|v|; NaN si alguno lo es (``max`` de Python lo perdería según el orden)."""
    if any(map(math.isnan, values)):
        return math.nan
    return max((abs(v) for v in values), default=0.0)


def displacements(response: SpatialWorkerResponse) -> dict[int, Six]:
    """Desplazamientos de los nudos de dominio por tag."""
    return {tag: disp for tag, disp, _reaction in response.node_results}


def node_reactions(
    request: SpatialRequest, response: SpatialWorkerResponse
) -> dict[int, Six | None]:
    """``nodeReaction`` proyectada sobre las direcciones de ``fix`` y de cadena del nudo.

    Con Lagrange, la reacción del ``fix`` y la de la cadena aparecen en ``nodeReaction(N)``
    (hechos 8 y 11). En una dirección libre es el residuo del propio solver: incluirlo haría
    tautológico el equilibrio global. ``None`` si el nudo no tiene restricción exacta.
    """
    constraints = constrained_directions(request, penalty=False)
    result: dict[int, Six | None] = {}
    for tag, _disp, reaction in response.node_results:
        if (tag, 0) not in constraints and (tag, 1) not in constraints:
            result[tag] = None
            continue
        projected: list[float] = []
        for triple in (0, 1):
            vector = list(reaction[AXES * triple : AXES * (triple + 1)])
            restrained = constraints.get((tag, triple), [])
            if not restrained:
                projected += [0.0, 0.0, 0.0]
                continue
            # r menos su proyección sobre el complemento libre (ortonormal).
            for free in free_directions(restrained):
                along = dot(vector, free)
                vector = [v - along * w for v, w in zip(vector, free, strict=True)]
            projected += vector
        a, b, c, d, e, f = projected
        result[tag] = (a, b, c, d, e, f)
    return result


# --- Redondeo de K·u ----------------------------------------------------------------------


def _beam_rounding(
    request: SpatialRequest, displacement: Displacements
) -> Iterator[tuple[int, int, float]]:
    """(nudo, terna, Σ_j |k_ij·u_j|) por fila de cada barra en base global."""
    for beam in request.beams:
        element = beam_matrices(request, beam).stiffness
        u = [*displacement[beam.node_i], *displacement[beam.node_j]]
        for row, values in enumerate(element):
            node = beam.node_i if row < DOFS_PER_NODE else beam.node_j
            yield (
                node,
                (row % DOFS_PER_NODE) // AXES,
                sum(abs(k * v) for k, v in zip(values, u, strict=True)),
            )


def _spring_rounding(
    request: SpatialRequest, displacement: Displacements
) -> Iterator[tuple[int, int, float]]:
    """(nudo, terna, k·‖u de la terna‖) por resorte, incluidos los de penalización."""
    for spring in request.springs:
        triple = (spring.dof - 1) // AXES
        u = displacement[spring.node][AXES * triple : AXES * (triple + 1)]
        yield spring.node, triple, spring.stiffness * _norm(u)


def rounding_bounds(request: SpatialRequest, displacement: Displacements) -> dict[int, list[float]]:
    """c·eps·Σ|k_ij·u_j| por nudo en filas de fuerza [0] y de momento [1]."""
    bounds: dict[int, list[float]] = {}
    for node, triple, value in (
        *_beam_rounding(request, displacement),
        *_spring_rounding(request, displacement),
    ):
        bounds.setdefault(node, [0.0, 0.0])[triple] += _ROUNDING * value
    return bounds


# --- Equilibrio global --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EquilibriumResidual:
    """Residuos y cotas absolutos de fuerza y momento, con sus escalas."""

    force: float
    force_bound: float
    force_scale: float
    moment: float
    moment_bound: float
    moment_scale: float

    @property
    def ok(self) -> bool:
        return not (
            _exceeds(self.force, self.force_bound) or _exceeds(self.moment, self.moment_bound)
        )


def equilibrium_residual(
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
) -> EquilibriumResidual:
    """ΣR + ΣP = 0 en base global, fuerzas y momentos por separado (como en el plano).

    R: ``node_reactions`` y las reacciones de las tierras (fuerza sobre la estructura,
    hecho 10). P: cargas nodales y resultante q·L de cada carga de barra en su punto medio.
    """
    coords = {node.tag: (node.x, node.y, node.z) for node in request.nodes}
    actions: list[tuple[Triple, Sequence[float]]] = []  # punto, (F, M)
    for load in request.nodal_loads:
        actions.append((coords[load.node], load.values))
    beams = {beam.tag: beam for beam in request.beams}
    for beam_load in request.beam_loads:
        beam = beams[beam_load.beam]
        matrices = beam_matrices(request, beam)
        ex, ey, ez = matrices.axes
        q = [
            (beam_load.wx * ex[k] + beam_load.wy * ey[k] + beam_load.wz * ez[k]) * matrices.length
            for k in range(AXES)
        ]
        xi, xj = coords[beam.node_i], coords[beam.node_j]
        middle = ((xi[0] + xj[0]) / 2, (xi[1] + xj[1]) / 2, (xi[2] + xj[2]) / 2)
        actions.append((middle, (*q, 0.0, 0.0, 0.0)))
    for tag, reaction in node_reactions(request, response).items():
        if reaction is not None:
            actions.append((coords[tag], reaction))
    ground_node = {spring.ground: spring.node for spring in request.springs}
    for ground, reaction in response.ground_reactions:
        actions.append((coords[ground_node[ground]], reaction))
    points = list(coords.values())
    centre = tuple(sum(p[k] for p in points) / len(points) for k in range(AXES))
    force, moment = [0.0] * AXES, [0.0] * AXES
    force_scale = moment_scale = 0.0
    for point, values in actions:
        f, m = values[:AXES], values[AXES:]
        arm = [point[k] - centre[k] for k in range(AXES)]
        torque = cross(arm, f)
        for k in range(AXES):
            force[k] += f[k]
            moment[k] += torque[k] + m[k]
        force_scale += _norm(f)
        moment_scale += _norm(m) + _norm(arm) * _norm(f)
    bounds = rounding_bounds(request, displacements(response))
    force_extra = sum(value[0] for value in bounds.values())
    moment_extra = sum(value[1] for value in bounds.values())
    l_ref = max(_norm([p[k] - centre[k] for k in range(AXES)]) for p in points)
    tol = config.tol_equilibrium
    return EquilibriumResidual(
        _norm(force),
        tol * force_scale + force_extra,
        force_scale,
        _norm(moment),
        tol * moment_scale + moment_extra + force_extra * l_ref,
        moment_scale,
    )


# --- Esfuerzos por barra ------------------------------------------------------------------


def _matvec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [sum(m * v for m, v in zip(row, vector, strict=True)) for row in matrix]


def member_checks(
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
) -> CheckFailure | None:
    """localForce ≈ k·T·u + p0 fila a fila; primer fallo en el orden de la petición.

    Cota por fila: tol_equilibrium·máx|localForce| + c·eps·B_i, con
    B_i = Σ_j |k_ij|·(|T|·|u|)_j + |p0_i|: en una barra girada T·u cancela y su redondeo
    escala con |T|·|u| (T-017, E1).
    """
    displacement = displacements(response)
    forces = dict(response.beam_forces)
    for beam in request.beams:
        matrices = beam_matrices(request, beam)
        p0 = [0.0] * 2 * DOFS_PER_NODE
        for beam_load in request.beam_loads:
            if beam_load.beam == beam.tag:
                for k, value in enumerate(fixed_end_forces(beam_load, matrices.length)):
                    p0[k] += value
        u_global = [*displacement[beam.node_i], *displacement[beam.node_j]]
        t = matrices.transformation
        u_local = _matvec(t, u_global)
        u_size = _matvec([[abs(v) for v in row] for row in t], [abs(v) for v in u_global])
        local_force = forces[beam.tag]
        largest = max(abs(value) for value in local_force)
        for index, row in enumerate(matrices.local):
            expected = dot(row, u_local) + p0[index]
            scale = sum(abs(k) * v for k, v in zip(row, u_size, strict=True)) + abs(p0[index])
            bound = config.tol_equilibrium * largest + _ROUNDING * scale
            residual = abs(local_force[index] - expected)
            if _exceeds(residual, bound):
                return CheckFailure(
                    "fem.member_force_check_failed",
                    {"component": index + 1, "value": residual, "bound": bound},
                    member_id=beam.entity_id,
                )
    return None


# --- Cadena: alias y restricción ----------------------------------------------------------


def translation_scale(displacement: Displacements) -> float:
    """‖u_tras‖∞ de los nudos de dominio (escala de la cota de alias, D3)."""
    return max((abs(v) for u in displacement.values() for v in u[:AXES]), default=0.0)


def rotation_scale(displacement: Displacements) -> float:
    """‖θ‖∞ de los nudos de dominio (escala del estimador de penalización, D6)."""
    return max((abs(v) for u in displacement.values() for v in u[AXES:]), default=0.0)


def chain_deviations(
    request: SpatialRequest, response: SpatialWorkerResponse
) -> Iterator[tuple[int, str, float]]:
    """(tag de la cadena, control, desviación): alias C↔N, n·u_N y enlace R→C."""
    displacement = displacements(response)
    results = {item.tag: item for item in response.chain_results}
    for chain in request.chains:
        item = results[chain.tag]
        u_n = displacement[chain.node]
        alias = max(abs(item.c_disp[k] - u_n[k]) for k in range(AXES))
        normal = unit(chain.normal)
        # u_C = u_R + θ_R × (C − R), con C − R = a·n y u_R = 0 (fijo).
        arm = tuple(chain.length * v for v in normal)
        link = cross(item.r_disp[AXES:], arm)
        rigid = max(abs(item.c_disp[k] - item.r_disp[k] - link[k]) for k in range(AXES))
        yield chain.tag, CONTROL_CHAIN_ALIAS, alias
        yield chain.tag, CONTROL_CHAIN_NORMAL, abs(dot(normal, u_n[:AXES]))
        yield chain.tag, CONTROL_CHAIN_LINK, rigid


def support_deviations(
    request: SpatialRequest, response: SpatialWorkerResponse
) -> Iterator[tuple[int, str, float, float]]:
    """(tag de la cadena, control, ‖nodeReaction(R)‖∞, escala) en fuerzas y en momentos.

    Con Lagrange la reacción de la cadena sale en N y R da 0 (hecho 8). En R solo actúa el
    resorte kr según n: la fuerza es nula por construcción (solo detecta NaN o un cambio del
    worker) y el momento comprueba en realidad θ_R·n ≈ 0, con el redondeo del enlace (≈ a·λ).

    Escalas: F = máx_k ‖F_k‖∞ en fuerzas, con F_k la ``nodeReaction`` de los nudos de dominio
    (N incluido), y máx(a·F, kr·‖θ_R‖∞, kr·‖θ‖∞ del modelo) en momentos. Las escalas de
    modelo son un suelo, como en DEV-2: con torsión pura la reacción de N y θ_R son ruido.
    Medido (batería, torsión pura, carga en el plano del rodillo, a = 2 y 2000 m): fuerzas
    0,0 y momentos ≤ 0,34·eps·escala local. Límite: con un ``fix`` en N casi paralelo a n,
    λ puede superar F (plan T-018, revisión de D-a).
    """
    raw = {tag: reaction for tag, _disp, reaction in response.node_results}
    results = {item.tag: item for item in response.chain_results}
    force = max(abs(v) for reaction in raw.values() for v in reaction[:AXES])
    model_rotation = rotation_scale(displacements(response))
    for chain in request.chains:
        item = results[chain.tag]
        rotation = max(model_rotation, *(abs(v) for v in item.r_disp[AXES:]))
        yield (
            chain.tag,
            CONTROL_CHAIN_SUPPORT_FORCE,
            _largest(item.r_reaction[:AXES]),
            force,
        )
        yield (
            chain.tag,
            CONTROL_CHAIN_SUPPORT_MOMENT,
            _largest(item.r_reaction[AXES:]),
            max(chain.length * force, chain.stiffness * rotation),
        )


def chain_scale(request: SpatialRequest, response: SpatialWorkerResponse, length: float) -> float:
    """máx(‖u_tras‖∞, a·‖θ‖∞) del modelo (D3 con DEV-2): u_C = θ_R × a·n, así que el
    redondeo de la cadena escala también con a·|θ|; con torsión pura ‖u_tras‖∞ es ruido."""
    displacement = displacements(response)
    return max(translation_scale(displacement), length * rotation_scale(displacement))


def chain_checks(request: SpatialRequest, response: SpatialWorkerResponse) -> CheckFailure | None:
    """Cota c_alias·eps·máx(‖u_tras‖∞, a·‖θ‖∞) (D3, DEV-2) en las tres desviaciones de
    cada cadena y c_alias·eps·escala en la reacción de R (``support_deviations``)."""
    node_ids = {node.tag: node.entity_id for node in request.nodes}
    chains = {chain.tag: chain for chain in request.chains}
    checks = [
        (tag, control, value, chain_scale(request, response, chains[tag].length))
        for tag, control, value in chain_deviations(request, response)
    ]
    checks += support_deviations(request, response)
    for tag, control, value, scale in checks:
        bound = CHAIN_ALIAS_FACTOR * _EPS * scale
        if _exceeds(value, bound):
            return CheckFailure(
                "fem3d.chain_postcondition_failed",
                {"control": control, "value": value, "bound": bound},
                node_id=node_ids[chains[tag].node],
            )
    return None


# --- Residuo reducido ---------------------------------------------------------------------


def residual_terms(
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
) -> Iterator[tuple[int, str, float, float]]:
    """(nudo, componente, |Tᵀ(K·u − F)|, cota) por coordenada reducida.

    K sin penalización ni restricciones (``full_stiffness``): la penalización y las cadenas
    actúan en direcciones que las coordenadas libres excluyen. Cota:
    tol_equilibrium·(Σ|K·u| + |F|) + c·eps·Σ|K·u|, y en los giros de un nudo con
    penalización además c·eps·k_pen·‖θ del nudo‖ (redondeo a escala k_pen; precedente del
    plano).
    """
    stiffness, index = full_stiffness(request)
    displacement = displacements(response)
    u = [0.0] * len(stiffness)
    for tag, values in displacement.items():
        base = DOFS_PER_NODE * index[tag]
        u[base : base + DOFS_PER_NODE] = values
    load = load_vector(request)
    ku, ku_scale = [], []
    for row in stiffness:
        terms = [k * v for k, v in zip(row, u, strict=True) if k != 0.0]
        ku.append(sum(terms))
        ku_scale.append(sum(abs(term) for term in terms))
    penalty: dict[tuple[int, int], float] = {}
    for spring in request.springs:
        if spring.penalty:
            key = (spring.node, (spring.dof - 1) // AXES)
            penalty[key] = penalty.get(key, 0.0) + spring.stiffness
    for label, column in reduced_coordinates(request):
        residual = sum(t * (ku[i] - load[i]) for i, t in column.items())
        k_scale = sum(abs(t) * ku_scale[i] for i, t in column.items())
        f_scale = sum(abs(t * load[i]) for i, t in column.items())
        bound = config.tol_equilibrium * (k_scale + f_scale) + _ROUNDING * k_scale
        triple = 0 if label.component[0] in "Ut" else 1
        if (label.node, triple) in penalty:
            node_u = displacement[label.node][AXES * triple : AXES * (triple + 1)]
            bound += _ROUNDING * penalty[label.node, triple] * _norm(node_u)
        yield label.node, label.component, abs(residual), bound


def residual_check(
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
) -> CheckFailure | None:
    node_ids = {node.tag: node.entity_id for node in request.nodes}
    for node, component, value, bound in residual_terms(request, response, config):
        if _exceeds(value, bound):
            return CheckFailure(
                "fem.residual_failed",
                {"component": component, "value": value, "bound": bound},
                node_id=node_ids[node],
            )
    return None


# --- Estimador de penalización (D6) -------------------------------------------------------


def penalty_deviations(
    request: SpatialRequest, response: SpatialWorkerResponse
) -> Iterator[tuple[int, float, float]]:
    """(nudo, |b·u| en la dirección penalizada, escala ‖·‖∞ del modelo en esa terna)."""
    displacement = displacements(response)
    scales = (translation_scale(displacement), rotation_scale(displacement))
    for spring in request.springs:
        if not spring.penalty:
            continue
        triple = (spring.dof - 1) // AXES
        b = spring_direction(spring.axis_x, spring.axis_yp, spring.dof)
        values = displacement[spring.node][AXES * triple : AXES * (triple + 1)]
        yield spring.node, abs(dot(b, values)), scales[triple]


def penalty_check(
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
) -> CheckFailure | None:
    """|b·θ| + eps·penalty_factor·‖θ‖∞ ≤ tol_penalty·‖θ‖∞ en cada resorte de penalización;
    si no, la corrida es FAILED (D6).

    El primer término (a posteriori) sigue el error de modelo de la penalización; el
    segundo (a priori) el de redondeo, que crece con k_pen y que |b·θ| no ve. Medido en la
    batería con penalización (pieza 5a): error real ≈ eps·penalty_factor cuando domina el
    redondeo, y ≤ 1,4·|b·θ|/‖θ‖∞ cuando domina el modelo.
    """
    node_ids = {node.tag: node.entity_id for node in request.nodes}
    for node, value, scale in penalty_deviations(request, response):
        estimate = value + _EPS * config.penalty_factor * scale
        bound = config.tol_penalty * scale
        if _exceeds(estimate, bound):
            return CheckFailure(
                "fem3d.penalty_estimate_failed",
                {"value": estimate, "bound": bound},
                node_id=node_ids[node],
            )
    return None


def solution_checks(
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
) -> CheckFailure | None:
    """Controles tras el equilibrio, en orden: barras, cadenas, residuo y penalización."""
    return (
        member_checks(request, response, config)
        or chain_checks(request, response)
        or residual_check(request, response, config)
        or penalty_check(request, response, config)
    )
