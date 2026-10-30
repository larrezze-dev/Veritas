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
  ``tol_penalty``·‖θ‖∞ en cada resorte de penalización de apoyo D4 (error de modelo y de
  redondeo).
- Enlaces de extremo (E2.5): alias de los ``equalDOF`` aux−N con la cota de D3, coherencia
  de cada ``zeroLength`` de extremo (F = k·Δ, Δ = b·(u_aux − u_N)) y equilibrio de cada aux
  con la fuerza del enlace y las cargas correctoras (ADR-004 §6).
- Contraste exacto de la penalización iterada de extremo (ADR-004 §4): desplazamientos y
  fuerzas de barra frente a la solución de la K reducida de la salvaguarda.
- Asiento (T-016, D7): el desplazamiento en cada GDL asentado es el prescrito, con la cota
  de alias ``CHAIN_ALIAS_FACTOR``·eps·s_u (s_θ en giros), porque Lagrange no es exacto
  (hecho 1). Va antes del equilibrio; sin asientos no hace nada.

Toda cota se evalúa como ``not (valor <= cota)``: un NaN da fallo. Las cotas de redondeo
c·eps·Σ|k_ij·u_j| usan la c del plano (``PENALTY_EQUILIBRIUM_FACTOR``). Los controles
contrastan OpenSees con la salvaguarda; no validan la traducción ni la normalización.
"""

import math
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from veritas.fem.adapters.singularity import solve_factored
from veritas.fem.postconditions import PENALTY_EQUILIBRIUM_FACTOR, CheckFailure
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import (
    AXES,
    DOFS_PER_NODE,
    Six,
    SpatialBeam,
    SpatialRequest,
    SpatialWorkerResponse,
)
from veritas.fem.spatial.stiffness import (
    GLOBAL_COMPONENTS,
    SpatialPivotReport,
    beam_fixed_end_forces,
    beam_matrices,
    constrained_directions,
    cross,
    dot,
    force_scale,
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
# Margen de fiabilidad de la referencia del contraste (E2.5-c): la corrección de un paso de
# refinamiento debe ser ≤ REFERENCE_MARGIN·tol_penalty·escala (medido ≤ 8e-10 relativo).
REFERENCE_MARGIN: Final = 1.0e-2
_EPS: Final = sys.float_info.epsilon
_ROUNDING: Final = PENALTY_EQUILIBRIUM_FACTOR * _EPS

# Nombres de control para el diagnóstico (parámetro ``control``).
CONTROL_CHAIN_ALIAS: Final = "alias de traslación del nudo C"
CONTROL_CHAIN_NORMAL: Final = "traslación según la normal"
CONTROL_CHAIN_LINK: Final = "enlace rígido R→C"
CONTROL_CHAIN_SUPPORT_FORCE: Final = "fuerza de reacción del nudo R"
CONTROL_CHAIN_SUPPORT_MOMENT: Final = "momento de reacción del nudo R"
CONTROL_END_ALIAS: Final = "alias del equalDOF aux−nudo"
CONTROL_END_SPRING_FORCE: Final = "basicForce = k·basicDeformation"
CONTROL_END_SPRING_GAP: Final = "basicDeformation = b·(u_aux − u_nudo)"
CONTROL_AUX_FORCE: Final = "equilibrio de fuerzas del aux"
CONTROL_AUX_MOMENT: Final = "equilibrio de momentos del aux"
CONTROL_CONTRAST_DISPLACEMENT: Final = "desplazamiento"
CONTROL_CONTRAST_FORCE: Final = "fuerza de barra"

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
    """Desplazamientos de los nudos de dominio por tag (escalas de los controles)."""
    return {tag: disp for tag, disp, _reaction in response.node_results}


def all_displacements(response: SpatialWorkerResponse) -> dict[int, Six]:
    """Desplazamientos de los nudos de dominio y de los aux por tag (E2.5)."""
    return displacements(response) | {tag: disp for tag, disp, _r in response.aux_results}


def element_displacement(
    request: SpatialRequest, beam: SpatialBeam, displacement: Displacements
) -> list[float]:
    """u global 12 del elemento: el del aux en un extremo que lo tenga (E2-a), como
    ``element_displacement`` del plano. Si ``displacement`` no tiene el aux (normalización,
    que no publica los aux; E2.5-f), el del maestro."""
    values: list[float] = []
    for end, node in (("I", beam.node_i), ("J", beam.node_j)):
        values += displacement.get(request.element_node(beam, end), displacement[node])
    return values


def summed_reactions(
    request: SpatialRequest, response: SpatialWorkerResponse
) -> dict[int, list[float]]:
    """``nodeReaction(N)`` + Σ ``nodeReaction(aux de N)`` por nudo de dominio (hecho 17): con
    Lagrange el multiplicador del ``equalDOF`` aux−N aparece en ambos y se anula en la suma."""
    result = {tag: list(reaction) for tag, _disp, reaction in response.node_results}
    masters = {aux.tag: aux.master for aux in request.aux_nodes}
    for tag, _disp, reaction in response.aux_results:
        total = result[masters[tag]]
        for k, value in enumerate(reaction):
            total[k] += value
    return result


def node_reactions(
    request: SpatialRequest, response: SpatialWorkerResponse
) -> dict[int, Six | None]:
    """``nodeReaction`` proyectada sobre las direcciones de ``fix`` y de cadena del nudo.

    Con Lagrange, la reacción del ``fix`` y la de la cadena aparecen en ``nodeReaction(N)``
    (hechos 8 y 11). En una dirección libre es el residuo del propio solver: incluirlo haría
    tautológico el equilibrio global. ``None`` si el nudo no tiene restricción exacta. Con
    aux, se proyecta la suma con sus aux (``summed_reactions``, hecho 17).
    """
    constraints = constrained_directions(request, penalty=False)
    result: dict[int, Six | None] = {}
    for tag, reaction in summed_reactions(request, response).items():
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
    """(nudo, terna, Σ_j |k_ij·u_j|) por fila de cada barra en base global, con el u del
    elemento (aux incluidos); las filas del aux cuentan en su maestro (``rounding_bounds``)."""
    for beam in request.beams:
        element = beam_matrices(request, beam).stiffness
        u = element_displacement(request, beam, displacement)
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
    """(nudo, terna, k·‖u de la terna‖) por resorte, incluidos los de penalización; en los
    de extremo, k·‖u_aux − u_N‖ en el maestro (se omite sin el u del aux: normalización).

    Sin término |λ_s| de la penalización iterada: donde λ cuenta (equilibrio global y del
    normalizador), la reacción es del orden de λ y tol_equilibrium·Σ|R| ≫ c·eps·|λ|."""
    for spring in request.springs:
        triple = (spring.dof - 1) // AXES
        u = displacement[spring.node][AXES * triple : AXES * (triple + 1)]
        yield spring.node, triple, spring.stiffness * _norm(u)
    masters = {aux.tag: aux.master for aux in request.aux_nodes}
    for end_spring in request.end_springs:
        if end_spring.aux not in displacement:
            continue
        master = masters[end_spring.aux]
        part = slice(
            AXES * ((end_spring.dof - 1) // AXES), AXES * ((end_spring.dof - 1) // AXES + 1)
        )
        delta = [
            a - n
            for a, n in zip(
                displacement[end_spring.aux][part], displacement[master][part], strict=True
            )
        ]
        yield master, (end_spring.dof - 1) // AXES, end_spring.stiffness * _norm(delta)


def rounding_bounds(request: SpatialRequest, displacement: Displacements) -> dict[int, list[float]]:
    """c·eps·Σ|k_ij·u_j| por nudo de dominio en filas de fuerza [0] y de momento [1]. Las
    filas de un aux cuentan en su maestro: el extremo actúa sobre el nudo publicado (E2-f)."""
    masters = {aux.tag: aux.master for aux in request.aux_nodes}
    bounds: dict[int, list[float]] = {}
    for node, triple, value in (
        *_beam_rounding(request, displacement),
        *_spring_rounding(request, displacement),
    ):
        bounds.setdefault(masters.get(node, node), [0.0, 0.0])[triple] += _ROUNDING * value
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
    bounds = rounding_bounds(request, all_displacements(response))
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


def beam_load_forces(request: SpatialRequest, beam: SpatialBeam, length: float) -> list[float]:
    """Σ p0 condensado de las cargas de la barra (``beam_fixed_end_forces``)."""
    p0 = [0.0] * 2 * DOFS_PER_NODE
    for beam_load in request.beam_loads:
        if beam_load.beam == beam.tag:
            for k, value in enumerate(beam_fixed_end_forces(beam, beam_load, length)):
                p0[k] += value
    return p0


def member_checks(
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
) -> CheckFailure | None:
    """localForce ≈ k·T·u + p0 fila a fila; primer fallo en el orden de la petición.

    Cota por fila: tol_equilibrium·máx|localForce| + c·eps·B_i, con
    B_i = Σ_j |k_ij|·(|T|·|u|)_j + |p0_i|: en una barra girada T·u cancela y su redondeo
    escala con |T|·|u| (T-017, E1). k y p0 condensados en los GDL liberados, A y J
    efectivos y el u del elemento, aux incluidos (E2.5).
    """
    displacement = all_displacements(response)
    forces = dict(response.beam_forces)
    for beam in request.beams:
        matrices = beam_matrices(request, beam)
        p0 = beam_load_forces(request, beam, matrices.length)
        u_global = element_displacement(request, beam, displacement)
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
    """‖u_tras‖∞ de los nudos dados (escala de la cota de alias, D3)."""
    return max((abs(v) for u in displacement.values() for v in u[:AXES]), default=0.0)


def rotation_scale(displacement: Displacements) -> float:
    """‖θ‖∞ de los nudos dados (escala del estimador de penalización, D6)."""
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
    raw = summed_reactions(request, response)
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


# --- Enlaces de extremo (E2.5) ------------------------------------------------------------


def link_scales(request: SpatialRequest, response: SpatialWorkerResponse) -> tuple[float, float]:
    """(s_u, s_θ) = (máx(‖u_tras‖∞, L_máx·‖θ‖∞), s_u / L_máx) de los nudos de dominio y
    de los aux (precedente DEV-2: con torsión pura o traslación pura una sola escala sería
    ruido). Los aux cuentan: con todos los nudos de dominio fijos, solo el aux se mueve
    (revisión de E2.5, M1)."""
    return coupled_scales(request, all_displacements(response))


def coupled_scales(request: SpatialRequest, displacement: Displacements) -> tuple[float, float]:
    """(s_u, s_θ) = (máx(‖u_tras‖∞, L_máx·‖θ‖∞), s_u / L_máx) de ``displacement``."""
    _, longest = force_scale(request)
    scale = max(translation_scale(displacement), longest * rotation_scale(displacement))
    return scale, scale / longest


# Componente de dominio de cada GDL global (1-6) en el diagnóstico del asiento.
SETTLEMENT_COMPONENT: Final = ("Ux", "Uy", "Uz", "Rx", "Ry", "Rz")


def settlement_check(
    request: SpatialRequest, response: SpatialWorkerResponse
) -> CheckFailure | None:
    """D7: |u − valor| ≤ ``CHAIN_ALIAS_FACTOR``·eps·s en cada GDL asentado, con (s_u, s_θ) de
    ``link_scales`` (nudos de dominio y aux), como ``end_link_deviations``."""
    if not request.settlements:
        return None
    displacement = displacements(response)
    scales = link_scales(request, response)
    node_ids = {node.tag: node.entity_id for node in request.nodes}
    for settlement in request.settlements:
        value = displacement[settlement.node][settlement.dof - 1]
        difference = abs(value - settlement.value)
        bound = CHAIN_ALIAS_FACTOR * _EPS * scales[(settlement.dof - 1) // AXES]
        if _exceeds(difference, bound):
            return CheckFailure(
                "fem.settlement_mismatch",
                {
                    "component": SETTLEMENT_COMPONENT[settlement.dof - 1],
                    "value": value,
                    "prescribed": settlement.value,
                    "difference": difference if math.isfinite(difference) else math.nan,
                    "bound": bound,
                },
                node_id=node_ids[settlement.node],
            )
    return None


def _triple(values: Sequence[float], triple: int) -> list[float]:
    return list(values[AXES * triple : AXES * (triple + 1)])


def _aux_actions(
    request: SpatialRequest, response: SpatialWorkerResponse, aux_tag: int
) -> tuple[list[float], list[float], list[float]]:
    """(F_int, P, escala por terna) del aux: F_int = Tᵀ·localForce del extremo + Σ basicForce·b
    de sus resortes; P = −λ_s·b_s (carga correctora, ADR-004 §1). Escala: Σ de normas."""
    aux = next(item for item in request.aux_nodes if item.tag == aux_tag)
    beam = next(item for item in request.beams if item.tag == aux.beam)
    matrices = beam_matrices(request, beam)
    offset = 0 if aux.end == "I" else DOFS_PER_NODE
    local_force = dict(response.beam_forces)[beam.tag]
    t = matrices.transformation
    internal = [
        sum(t[r][offset + c] * local_force[r] for r in range(offset, offset + DOFS_PER_NODE))
        for c in range(DOFS_PER_NODE)
    ]
    scale = [_norm(_triple(internal, 0)), _norm(_triple(internal, 1))]
    applied = [0.0] * DOFS_PER_NODE
    results = {tag: (force, lam) for tag, force, _deformation, lam in response.end_spring_results}
    for spring in request.end_springs:
        if spring.aux != aux_tag:
            continue
        b = spring_direction(spring.axis_x, spring.axis_yp, spring.dof)
        triple = (spring.dof - 1) // AXES
        force, lam = results[spring.tag]
        for k in range(AXES):
            internal[AXES * triple + k] += force * b[k]
            applied[AXES * triple + k] -= lam * b[k]
        scale[triple] += abs(force) + abs(lam)
    return internal, applied, scale


def end_link_deviations(
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
) -> Iterator[tuple[int, str, float, float]]:
    """(aux, control, desviación, cota) de los enlaces de extremo.

    - Alias de cada ``equalDOF``: |u_aux − u_N| ≤ c_alias·eps·s (D3), s = s_u o s_θ.
    - Por ``zeroLength`` de extremo: |basicForce − k·Δ| ≤ c·eps·|k·Δ| y
      |Δ − b·(u_aux − u_N)| con la cota del alias.
    - Equilibrio del aux: F_int − P − ``nodeReaction(aux)`` = 0 (``nodeReaction`` es el
      desequilibrio F_int − P: resta las cargas aplicadas, hecho 24, e incluye el
      multiplicador del ``equalDOF``, hecho 17), con cota tol_equilibrium·Σ|términos| +
      c·eps·Σ|k_ij·u_j| de las filas del aux. Comprueba la coherencia de lo que devuelve el
      worker (localForce, basicForce, λ y reacción); el equilibrio físico lo cubren el
      residuo reducido y el contraste (revisión de E2.5, o1).
    """
    displacement = all_displacements(response)
    scales = link_scales(request, response)
    masters = {aux.tag: aux.master for aux in request.aux_nodes}
    for equal in request.end_equals:
        u_aux, u_n = displacement[equal.aux], displacement[masters[equal.aux]]
        for dof in equal.dofs:
            bound = CHAIN_ALIAS_FACTOR * _EPS * scales[(dof - 1) // AXES]
            yield equal.aux, CONTROL_END_ALIAS, abs(u_aux[dof - 1] - u_n[dof - 1]), bound
    results = {
        tag: (force, deformation) for tag, force, deformation, _l in response.end_spring_results
    }
    for spring in request.end_springs:
        force, deformation = results[spring.tag]
        triple = (spring.dof - 1) // AXES
        expected = spring.stiffness * deformation
        yield spring.aux, CONTROL_END_SPRING_FORCE, abs(force - expected), _ROUNDING * abs(expected)
        b = spring_direction(spring.axis_x, spring.axis_yp, spring.dof)
        gap = dot(
            b,
            [
                a - n
                for a, n in zip(
                    _triple(displacement[spring.aux], triple),
                    _triple(displacement[masters[spring.aux]], triple),
                    strict=True,
                )
            ],
        )
        bound = CHAIN_ALIAS_FACTOR * _EPS * scales[triple]
        yield spring.aux, CONTROL_END_SPRING_GAP, abs(deformation - gap), bound
    reactions = {tag: reaction for tag, _disp, reaction in response.aux_results}
    rounding = _aux_rounding(request, displacement)
    for aux in request.aux_nodes:
        internal, applied, scale = _aux_actions(request, response, aux.tag)
        reaction = reactions[aux.tag]
        residual = [f - p - r for f, p, r in zip(internal, applied, reaction, strict=True)]
        for triple, control in ((0, CONTROL_AUX_FORCE), (1, CONTROL_AUX_MOMENT)):
            terms = scale[triple] + _norm(_triple(reaction, triple))
            bound = config.tol_equilibrium * terms + rounding[aux.tag][triple]
            yield aux.tag, control, _norm(_triple(residual, triple)), bound


def _aux_rounding(request: SpatialRequest, displacement: Displacements) -> dict[int, list[float]]:
    """c·eps·Σ|k_ij·u_j| de las filas del elemento en cada aux (fuerza, momento)."""
    result = {aux.tag: [0.0, 0.0] for aux in request.aux_nodes}
    beams = {beam.tag: beam for beam in request.beams}
    for aux in request.aux_nodes:
        beam = beams[aux.beam]
        element = beam_matrices(request, beam).stiffness
        u = element_displacement(request, beam, displacement)
        offset = 0 if aux.end == "I" else DOFS_PER_NODE
        for row in range(offset, offset + DOFS_PER_NODE):
            value = sum(abs(k * v) for k, v in zip(element[row], u, strict=True))
            result[aux.tag][(row % DOFS_PER_NODE) // AXES] += _ROUNDING * value
    return result


def end_link_checks(
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
) -> CheckFailure | None:
    """Primer enlace de extremo que incumple su cota; ubicación en la barra y su extremo."""
    if not request.aux_nodes:
        return None
    auxes = {aux.tag: aux for aux in request.aux_nodes}
    beams = {beam.tag: beam.entity_id for beam in request.beams}
    for tag, control, value, bound in end_link_deviations(request, response, config):
        if _exceeds(value, bound):
            aux = auxes[tag]
            return CheckFailure(
                "fem3d.end_link_check_failed",
                {"control": control, "end": aux.end, "value": value, "bound": bound},
                member_id=beams[aux.beam],
            )
    return None


# --- Contraste exacto de la penalización de extremo (ADR-004 §4, E2.5) ---------------------


@dataclass(frozen=True, slots=True)
class ContrastReference:
    """u_ref = T·q con K_red·q = Tᵀ·F y un paso de refinamiento; ``correction``: T·δ de
    ese paso (estimación del error de la referencia). Por tag de nudo de dominio y de aux."""

    displacement: dict[int, list[float]]
    correction: dict[int, list[float]]


def contrast_reference(request: SpatialRequest, report: SpatialPivotReport) -> ContrastReference:
    """Referencia de la K reducida con la factorización de la salvaguarda (E2.5-b): F de
    ``load_vector`` sin cargas correctoras, que se anulan en las coordenadas reducidas."""
    if report.reduced is None or report.factor is None:
        raise ValueError("la salvaguarda no conservó la factorización")
    reduced, factor = report.reduced, report.factor
    coordinates = reduced_coordinates(request)
    load = load_vector(request)
    rhs = [sum(t * load[i] for i, t in column.items()) for _, column in coordinates]
    q = solve_factored(reduced, factor, rhs)
    residual = [
        f - sum(k * v for k, v in zip(row, q, strict=True))
        for f, row in zip(rhs, reduced, strict=True)
    ]
    delta = solve_factored(reduced, factor, residual)
    q = [a + b for a, b in zip(q, delta, strict=True)]
    tags = [node.tag for node in request.nodes] + [aux.tag for aux in request.aux_nodes]

    def expand(values: Sequence[float]) -> dict[int, list[float]]:
        full = [0.0] * DOFS_PER_NODE * len(tags)
        for value, (_, column) in zip(values, coordinates, strict=True):
            for i, t in column.items():
                full[i] += t * value
        return {
            tag: full[DOFS_PER_NODE * k : DOFS_PER_NODE * (k + 1)] for k, tag in enumerate(tags)
        }

    return ContrastReference(expand(q), expand(delta))


def end_penalty_contrast(
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
    report: SpatialPivotReport,
) -> CheckFailure | None:
    """Contraste exacto (ADR-004 §4) de una corrida con penalización de extremo.

    - Fiabilidad: |T·δ| ≤ ``REFERENCE_MARGIN``·tol_penalty·s, o FAILED.
    - Desplazamientos de nudos y aux: |u − u_ref| ≤ tol_penalty·s, con s = s_u en
      traslaciones y s_θ en giros (E2.5-a).
    - Fuerzas de barra: |localForce − (k·T·u_ref + p0)| ≤ tol_penalty·máx(‖f_ref‖∞, F) en
      filas de fuerza y tol_penalty·máx(‖m_ref‖∞, F·L_máx) en las de momento; F y L_máx de
      ``force_scale`` (suelo de las barras descargadas).

    Solo comprueba: se publican los resultados de OpenSees.
    """
    node_ids = {node.tag: node.entity_id for node in request.nodes}
    node_ids |= {aux.tag: node_ids[aux.master] for aux in request.aux_nodes}
    reference = contrast_reference(request, report)
    scales = coupled_scales(request, reference.displacement)  # Como ``link_scales`` (E2.5-a).
    tol = config.tol_penalty
    for values in reference.correction.values():
        for k, value in enumerate(values):
            bound = REFERENCE_MARGIN * tol * scales[k // AXES]
            if _exceeds(abs(value), bound):
                return CheckFailure(
                    "fem3d.end_penalty_reference_unreliable", {"value": abs(value), "bound": bound}
                )
    displacement = all_displacements(response)
    for tag, expected in reference.displacement.items():
        for k, value in enumerate(expected):
            deviation = abs(displacement[tag][k] - value)
            bound = tol * scales[k // AXES]
            if _exceeds(deviation, bound):
                return CheckFailure(
                    "fem3d.end_penalty_contrast_failed",
                    {
                        "control": CONTROL_CONTRAST_DISPLACEMENT,
                        "component": GLOBAL_COMPONENTS[k],
                        "value": deviation,
                        "bound": bound,
                    },
                    node_id=node_ids[tag],
                )
    force, longest = force_scale(request)
    forces = dict(response.beam_forces)
    for beam in request.beams:
        matrices = beam_matrices(request, beam)
        p0 = beam_load_forces(request, beam, matrices.length)
        u_local = _matvec(
            matrices.transformation, element_displacement(request, beam, reference.displacement)
        )
        expected = [dot(row, u_local) + p0[i] for i, row in enumerate(matrices.local)]
        rows = [i for i in range(2 * DOFS_PER_NODE) if (i % DOFS_PER_NODE) < AXES]
        moments = [i for i in range(2 * DOFS_PER_NODE) if i not in rows]
        floors = (
            (rows, max(force, _largest([expected[i] for i in rows]))),
            (moments, max(force * longest, _largest([expected[i] for i in moments]))),
        )
        for indices, floor in floors:
            for i in indices:
                deviation = abs(forces[beam.tag][i] - expected[i])
                if _exceeds(deviation, tol * floor):
                    return CheckFailure(
                        "fem3d.end_penalty_contrast_failed",
                        {
                            "control": CONTROL_CONTRAST_FORCE,
                            "component": str(i + 1),
                            "value": deviation,
                            "bound": tol * floor,
                        },
                        member_id=beam.entity_id,
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
    displacement = all_displacements(response)
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
    report: SpatialPivotReport | None = None,
) -> CheckFailure | None:
    """Controles tras el equilibrio, en orden: enlaces de extremo, contraste exacto (solo con
    penalización de extremo; ``report`` con la factorización de la salvaguarda), barras,
    cadenas, residuo y penalización D4. El contraste va antes de los controles genéricos
    para que un error de penalización dé su clave propia (E2.5)."""
    failure = end_link_checks(request, response, config)
    if failure is None and request.end_penalties:
        if report is None:
            raise ValueError("el contraste exige el informe de la salvaguarda")
        failure = end_penalty_contrast(request, response, config, report)
    return (
        failure
        or member_checks(request, response, config)
        or chain_checks(request, response)
        or residual_check(request, response, config)
        or penalty_check(request, response, config)
    )
