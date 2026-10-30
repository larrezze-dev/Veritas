"""Controles de la solución tras el equilibrio (plan T-017, E1: D1 y D2).

Se evalúan en el proceso padre sobre la respuesta del worker, en la base del solver
(x₁ = X, x₂ = Z, θ = −Ry), con la formulación de la salvaguarda (Euler–Bernoulli 2D):

- D1: alias del aux de extremo, esfuerzos por barra localForce ≈ k·T·u − F_eq (todas las
  barras), momento nulo en un extremo liberado, relación del resorte de extremo
  M = k·(θ maestro − θ aux) y giro libre del aux sin reacción.
- D2: residuo Tᵀ(K·u − F) por coordenada reducida, con las cargas equivalentes de
  ``beamUniform`` y ``member_end_loads`` (traslaciones al maestro, momento al θ del aux en
  un extremo no rígido).
- Cargas de barra de Veritas (T-015): su p0 no pasa por el elemento, así que el control 2
  no cambia y los controles de extremo usan el momento publicado localForce + p0.
- Asiento (T-016, D7): el desplazamiento en cada GDL asentado es el prescrito. Va primero,
  porque los demás controles usan ese u; sin asientos no hace nada.

El alias exacto (diferencia 0) depende del handler ``Transformation``, fijado en el código
del worker plano desde el protocolo v2 (vigente en v3): un cambio de handler exige una
versión nueva de protocolo y revisar este control. Los controles contrastan OpenSees con la
salvaguarda; no validan la normalización de ADR-001 ni la traducción de cargas, respaldadas
por las referencias.
Toda cota se evalúa como ``not (valor <= cota)``: un NaN da fallo. Reutilizan
``tol_equilibrium`` y ``PENALTY_EQUILIBRIUM_FACTOR``; no hay tolerancias nuevas.
"""

import dataclasses
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from veritas.core.diagnostics import ParamValue
from veritas.fem.adapters.protocol import PlanarBeam, PlanarEndLink, PlanarRequest, WorkerResponse
from veritas.fem.adapters.singularity import (
    TANGENT,
    Matrix,
    aux_rotation_index,
    beam_rotation,
    element_displacement,
    full_stiffness,
    local_beam_stiffness,
    reduced_coordinates,
)
from veritas.fem.config import AnalysisConfiguration

# Constante c de las cotas de redondeo c·eps·Σ|k_ij·u_j| (R1, R2 de T-007). Se registra en
# la procedencia y la comparten el control de equilibrio y estos controles.
PENALTY_EQUILIBRIUM_FACTOR: Final = 10.0
_ROUNDING: Final = PENALTY_EQUILIBRIUM_FACTOR * sys.float_info.epsilon

# Índice del momento del extremo en localForce (u₁, u₂, θ de I y de J).
END_MOMENT_INDEX: Final = {"I": 2, "J": 5}

# Nombres de control para el diagnóstico (parámetro ``control``).
CONTROL_ALIAS: Final = "alias de traslación del aux"
CONTROL_FORCES: Final = "esfuerzos de barra"
CONTROL_RELEASED: Final = "momento en extremo liberado"
CONTROL_SPRING: Final = "relación del resorte de extremo"
CONTROL_AUX_REACTION: Final = "reacción de giro del aux"

type Displacements = Mapping[int, Sequence[float]]


@dataclass(frozen=True, slots=True)
class CheckFailure:
    """Control no superado: clave, parámetros y entidad (id de barra o de nudo).

    El llamador añade la etiqueta de la entidad (``member`` o ``subject``) y su ubicación.
    """

    key: str
    params: dict[str, ParamValue]
    member_id: str | None = None
    node_id: str | None = None


def _exceeds(value: float, bound: float) -> bool:
    """Fallo si el valor supera la cota o si la cota no es finita (NaN o ±inf)."""
    return not (value <= bound) or not math.isfinite(bound)


def local_equivalent_loads(request: PlanarRequest) -> dict[int, list[float]]:
    """F_eq local por barra (tag): [wx·L/2, wy·L/2, wy·L²/12, wx·L/2, wy·L/2, −wy·L²/12].

    Suma las cargas ``beamUniform`` de cada barra. Son cargas nodales equivalentes: el
    esfuerzo de empotramiento es −F_eq.
    """
    coords = {node.tag: (node.x1, node.x2) for node in request.nodes}
    beams = {beam.tag: beam for beam in request.beams}
    loads: dict[int, list[float]] = {}
    for load in request.beam_loads:
        beam = beams[load.beam]
        length, _ = beam_rotation(coords[beam.node_i], coords[beam.node_j])
        axial, shear = load.wx * length / 2, load.wy * length / 2
        moment = load.wy * length**2 / 12
        current = loads.setdefault(load.beam, [0.0] * 6)
        for k, value in enumerate((axial, shear, moment, axial, shear, -moment)):
            current[k] += value
    return loads


def global_equivalent_loads(request: PlanarRequest) -> dict[int, list[float]]:
    """Tᵀ·F_eq por barra (tag), en la base global del solver."""
    coords = {node.tag: (node.x1, node.x2) for node in request.nodes}
    beams = {beam.tag: beam for beam in request.beams}
    result: dict[int, list[float]] = {}
    for tag, local in local_equivalent_loads(request).items():
        beam = beams[tag]
        _, rotation = beam_rotation(coords[beam.node_i], coords[beam.node_j])
        result[tag] = [sum(rotation[m][i] * local[m] for m in range(6)) for i in range(6)]
    return result


def member_fixed_end_forces(request: PlanarRequest) -> dict[int, list[float]]:
    """p0 local (N, V, M de I y J) de las cargas de barra de Veritas, por barra (T-015).

    Se recupera de ``member_end_loads`` como p0 = −T·[g_I; g_J]: es la única fuente de p0 en
    el padre, la misma para los controles y para la normalización. Solo aparecen las barras
    con entradas (sin sumar ceros a las demás: conserva el signo de −0,0).
    """
    coords = {node.tag: (node.x1, node.x2) for node in request.nodes}
    beams = {beam.tag: beam for beam in request.beams}
    loads: dict[int, list[float]] = {}
    for load in request.member_end_loads:
        beam = beams[load.beam]
        offset = 0 if load.node == request.element_node(beam, "I") else 3
        current = loads.setdefault(load.beam, [0.0] * 6)
        current[offset : offset + 3] = [load.p1, load.p2, load.m]
    result: dict[int, list[float]] = {}
    for tag, values in loads.items():
        beam = beams[tag]
        _, rotation = beam_rotation(coords[beam.node_i], coords[beam.node_j])
        result[tag] = [-sum(r * v for r, v in zip(row, values, strict=True)) for row in rotation]
    return result


def _matvec(matrix: Matrix, vector: Sequence[float]) -> list[float]:
    return [sum(m * v for m, v in zip(row, vector, strict=True)) for row in matrix]


def _displacements(response: WorkerResponse) -> dict[int, tuple[float, float, float]]:
    displacement = {tag: disp for tag, disp, _reaction in response.node_results}
    displacement |= {tag: disp for tag, disp, _reaction in response.end_aux}
    return displacement


@dataclass(frozen=True, slots=True)
class _BeamRows:
    """Por fila: esperado k·T·u − F_eq y escala B_i = Σ_j |k_ij|·(|T|·|u|)_j + |F_eq,i|.

    La escala usa |T|·|u| y no |T·u|: en una barra girada con traslaciones grandes casi
    normales al eje, T·u cancela y su redondeo escala con |T|·|u| (sonda de T-017, E1).
    """

    expected: list[float]
    scale: list[float]


def _beam_rows(
    request: PlanarRequest,
    beam: PlanarBeam,
    displacement: Displacements,
    equivalent: Sequence[float],
) -> _BeamRows:
    coords = {node.tag: (node.x1, node.x2) for node in request.nodes}
    length, rotation = beam_rotation(coords[beam.node_i], coords[beam.node_j])
    local = local_beam_stiffness(length, beam.area, beam.elastic_modulus, beam.inertia)
    u_global = element_displacement(request, beam, displacement)
    u_local = _matvec(rotation, u_global)
    u_size = _matvec([[abs(t) for t in row] for row in rotation], [abs(v) for v in u_global])
    expected, scale = [], []
    for row, f_eq in zip(local, equivalent, strict=True):
        expected.append(sum(k * v for k, v in zip(row, u_local, strict=True)) - f_eq)
        scale.append(sum(abs(k) * v for k, v in zip(row, u_size, strict=True)) + abs(f_eq))
    return _BeamRows(expected, scale)


def _end_failure(link: PlanarEndLink, control: str, value: float, bound: float) -> CheckFailure:
    return CheckFailure(
        "fem.member_end_postcondition_failed",
        {"end": link.end, "control": control, "value": value, "bound": bound},
        member_id=link.member_id,
    )


@dataclass(frozen=True, slots=True)
class _BeamState:
    """localForce de la barra, filas esperadas, cota del control 2 por fila y p0 de las
    cargas de barra de Veritas (T-015; vacío si la barra no tiene)."""

    local_force: Sequence[float]
    rows: _BeamRows
    bounds: list[float]
    fixed_end: Sequence[float] = ()


def _alias_failure(link: PlanarEndLink, displacement: Displacements) -> CheckFailure | None:
    """Control 1: u₁, u₂ del aux iguales bit a bit a los del maestro (hecho 1)."""
    master, aux = displacement[link.master], displacement[link.aux]
    difference = max(abs(master[0] - aux[0]), abs(master[1] - aux[1]))
    if master[0] != aux[0] or master[1] != aux[1]:
        value = difference if math.isfinite(difference) else math.nan
        return _end_failure(link, CONTROL_ALIAS, value, 0.0)
    return None


def _forces_failure(
    beam: PlanarBeam,
    links: Sequence[PlanarEndLink],
    local_force: Sequence[float],
    rows: _BeamRows,
    bounds: Sequence[float],
) -> CheckFailure | None:
    """Control 2: localForce ≈ k·T·u − F_eq fila a fila."""
    for index, (force, expected, bound) in enumerate(
        zip(local_force, rows.expected, bounds, strict=True)
    ):
        residual = abs(force - expected)
        if not _exceeds(residual, bound):
            continue
        # La clave depende del extremo de la fila: una fila de un extremo rígido usa
        # ``fem.member_force_check_failed`` aunque el otro extremo no sea rígido.
        end = "I" if index < 3 else "J"  # noqa: PLR2004 (filas 0-2 en I)
        link = next((item for item in links if item.end == end), None)
        if link is not None:
            return _end_failure(link, CONTROL_FORCES, residual, bound)
        return CheckFailure(
            "fem.member_force_check_failed",
            {"component": index + 1, "value": residual, "bound": bound},
            member_id=beam.entity_id,
        )
    return None


def _link_failure(
    link: PlanarEndLink,
    beam: _BeamState,
    displacement: Displacements,
    aux_reaction: Mapping[int, Sequence[float]],
    tol_equilibrium: float,
) -> CheckFailure | None:
    """Controles 3 y 4 sobre un extremo no rígido; con liberación, M = 0 dentro de la cota.

    Usan el momento publicado localForce + p0 (T-015, hecho 8): la carga equivalente de una
    carga de barra de Veritas llega al θ del aux como carga nodal, no por el elemento.
    """
    index = END_MOMENT_INDEX[link.end]
    moment, extra = beam.local_force[index], 0.0
    if beam.fixed_end:
        moment += beam.fixed_end[index]
        extra = _ROUNDING * abs(beam.fixed_end[index])
    if link.stiffness is None:
        bound = beam.bounds[index] + extra
        if _exceeds(abs(moment), bound):
            return _end_failure(link, CONTROL_RELEASED, abs(moment), bound)
    else:
        theta_master, theta_aux = displacement[link.master][2], displacement[link.aux][2]
        residual = abs(moment - link.stiffness * (theta_master - theta_aux))
        spring_scale = 2.0 * abs(link.stiffness) * (abs(theta_master) + abs(theta_aux))
        bound = (
            tol_equilibrium * abs(moment)
            + _ROUNDING * (beam.rows.scale[index] + spring_scale)
            + extra
        )
        if _exceeds(residual, bound):
            return _end_failure(link, CONTROL_SPRING, residual, bound)
    reaction = abs(aux_reaction[link.aux][2])
    if _exceeds(reaction, bound):
        return _end_failure(link, CONTROL_AUX_REACTION, reaction, bound)
    return None


# Componente de dominio del GDL asentado en la base del solver (θ = −Ry).
SETTLEMENT_COMPONENT: Final = {1: "Ux", 2: "Uz", 3: "Ry"}


def settlement_check(request: PlanarRequest, response: WorkerResponse) -> CheckFailure | None:
    """D7: |u − valor| ≤ c·eps·|valor| en cada GDL asentado (Transformation es exacto, hecho 2)."""
    displacement = _displacements(response)
    node_ids = {node.tag: node.entity_id for node in request.nodes}
    for settlement in request.settlements:
        value = displacement[settlement.node][settlement.dof - 1]
        difference = abs(value - settlement.value)
        bound = _ROUNDING * abs(settlement.value)
        if _exceeds(difference, bound):
            # El diagnóstico va en base de dominio: Ry = −θ (revisión E2.1, M4).
            sign = -1.0 if settlement.dof == 3 else 1.0  # noqa: PLR2004 (GDL 3 = giro)
            return CheckFailure(
                "fem.settlement_mismatch",
                {
                    "component": SETTLEMENT_COMPONENT[settlement.dof],
                    "value": sign * value,
                    "prescribed": sign * settlement.value,
                    "difference": difference if math.isfinite(difference) else math.nan,
                    "bound": bound,
                },
                node_id=node_ids[settlement.node],
            )
    return None


def member_checks(
    request: PlanarRequest, response: WorkerResponse, config: AnalysisConfiguration
) -> CheckFailure | None:
    """D1: primer control no superado, en el orden de la petición (barra, control)."""
    displacement = _displacements(response)
    aux_reaction = {tag: reaction for tag, _disp, reaction in response.end_aux}
    forces = dict(response.beam_forces)
    equivalent = local_equivalent_loads(request)
    fixed_end = member_fixed_end_forces(request)
    for beam in request.beams:
        links = sorted(
            (link for link in request.end_links if link.beam == beam.tag),
            key=lambda link: link.end,
        )
        for link in links:
            failure = _alias_failure(link, displacement)
            if failure is not None:
                return failure
        local_force = forces[beam.tag]
        rows = _beam_rows(request, beam, displacement, equivalent.get(beam.tag, [0.0] * 6))
        largest = max(abs(value) for value in local_force)
        bounds = [config.tol_equilibrium * largest + _ROUNDING * scale for scale in rows.scale]
        failure = _forces_failure(beam, links, local_force, rows, bounds)
        if failure is not None:
            return failure
        state = _BeamState(local_force, rows, bounds, fixed_end.get(beam.tag, ()))
        for link in links:
            failure = _link_failure(link, state, displacement, aux_reaction, config.tol_equilibrium)
            if failure is not None:
                return failure
    return None


def load_vector(request: PlanarRequest) -> list[float]:
    """F completo en los GDL de ``full_stiffness``: cargas nodales, ``member_end_loads``
    (T-015) y Tᵀ·F_eq de barra.

    En un extremo no rígido las traslaciones van al maestro y el momento al θ del aux.
    """
    index = {node.tag: position for position, node in enumerate(request.nodes)}
    aux_theta = aux_rotation_index(request)
    master = {link.aux: link.master for link in request.end_links}
    load = [0.0] * (3 * len(request.nodes) + len(aux_theta))
    for nodal in request.nodal_loads:
        base = 3 * index[nodal.node]
        load[base] += nodal.p1
        load[base + 1] += nodal.p2
        load[base + 2] += nodal.m
    for end_load in request.member_end_loads:
        base = 3 * index[master.get(end_load.node, end_load.node)]
        theta = aux_theta[end_load.node] if end_load.node in aux_theta else base + 2
        load[base] += end_load.p1
        load[base + 1] += end_load.p2
        load[theta] += end_load.m
    beams = {beam.tag: beam for beam in request.beams}
    for tag, values in global_equivalent_loads(request).items():
        beam = beams[tag]
        for offset, (end, node) in enumerate((("I", beam.node_i), ("J", beam.node_j))):
            base = 3 * index[node]
            element_node = request.element_node(beam, end)
            theta = aux_theta[element_node] if element_node in aux_theta else base + 2
            for k, dof in enumerate((base, base + 1, theta)):
                load[dof] += values[3 * offset + k]
    return load


def residual_check(
    request: PlanarRequest, response: WorkerResponse, config: AnalysisConfiguration
) -> CheckFailure | None:
    """D2: |Tᵀ(K·u − F)| por coordenada reducida, K sin penalización (``full_stiffness``).

    Cota: tol_equilibrium·(Σ|K°·u| + |F|) + c·eps·Σ|K·u|, y en la tangente de un nudo con
    penalización además c·eps·k_pen·‖u del nudo‖ (redondeo a escala k_pen; hecho 5).
    K° es K sin los resortes de extremo (desviación 4 de E1): con un resorte muy rígido,
    k·|θm| + k·|θa| ≫ |M| y el término relativo dejaría la fila de giro sin control; esas
    entradas solo entran en el término de redondeo, como en ``_rounding_bound``.
    """
    stiffness, index = full_stiffness(request)
    springless = dataclasses.replace(
        request,
        end_links=tuple(dataclasses.replace(link, stiffness=None) for link in request.end_links),
    )
    relative, _ = full_stiffness(springless)
    aux_theta = aux_rotation_index(request)
    u = [0.0] * len(stiffness)
    for tag, disp, _reaction in response.node_results:
        base = 3 * index[tag]
        u[base : base + 3] = disp
    for tag, disp, _reaction in response.end_aux:
        u[aux_theta[tag]] = disp[2]
    load = load_vector(request)
    ku, ku_scale, relative_scale = [], [], []
    for row, row_relative in zip(stiffness, relative, strict=True):
        terms = [k * v for k, v in zip(row, u, strict=True) if k != 0.0]
        ku.append(sum(terms))
        ku_scale.append(sum(abs(term) for term in terms))
        relative_scale.append(
            sum(abs(k * v) for k, v in zip(row_relative, u, strict=True) if k != 0.0)
        )
    penalty = {spring.node: spring.stiffness for spring in request.springs if spring.penalty}
    links = {link.aux: link for link in request.end_links}
    node_ids = {node.tag: node.entity_id for node in request.nodes}
    for label, column in reduced_coordinates(request):
        residual = sum(t * (ku[i] - load[i]) for i, t in column.items())
        k_scale = sum(abs(t) * ku_scale[i] for i, t in column.items())
        r_scale = sum(abs(t) * relative_scale[i] for i, t in column.items())
        f_scale = sum(abs(t * load[i]) for i, t in column.items())
        bound = config.tol_equilibrium * (r_scale + f_scale) + _ROUNDING * k_scale
        if label.component == TANGENT:
            base = 3 * index[label.node]
            bound += _ROUNDING * penalty[label.node] * math.hypot(u[base], u[base + 1])
        if not _exceeds(abs(residual), bound):
            continue
        params: dict[str, ParamValue] = {
            "component": label.component,
            "value": abs(residual),
            "bound": bound,
        }
        if label.node in links:
            link = links[label.node]
            return CheckFailure(
                "fem.residual_failed", {**params, "end": link.end}, member_id=link.member_id
            )
        return CheckFailure("fem.residual_failed", params, node_id=node_ids[label.node])
    return None
