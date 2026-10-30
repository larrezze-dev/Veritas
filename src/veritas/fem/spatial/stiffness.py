"""Rigidez espacial y salvaguarda de singularidad previa al solver (plan T-018, pieza 4).

Ensamblaje independiente, en Python puro, de la MISMA ``SpatialRequest`` que recibe el
worker 3D: barras Euler–Bernoulli 12×12 en la base de ADR-001 (Iz en el bloque uy–rz, Iy en
el bloque uz–ry, ey = ez × ex con ez = ``vecxz``), resortes de apoyo orientados como k·b·bᵀ
y restricciones exactas. Por nudo y por terna (traslaciones, giros) las coordenadas libres
son el complemento ortonormal de {e_k fijos}, {normales de cadena} y {direcciones de
penalización}: la cadena es exactamente n·u = 0 y la penalización se aplica de forma
exacta, como en la salvaguarda plana. Los nudos C, R y las tierras no entran: el giro de R
alrededor de n es un modo desacoplado que fija el resorte kr.

Extremos no rígidos (E2.3): la k de la barra se condensa en los GDL de ``-release*`` (con su
p0) y usa A y J efectivos; cada aux añade 6 GDL con u_aux = u_N + δ, sus ``equalDOF`` y
penalizaciones son restricciones relativas exactas y sus resortes, rigideces relativas.

La factorización y el modo de mecanismo se importan de ``fem.adapters.singularity`` sin
cambios (D2-A). La garantía es condicionada a ``tol_pivot`` y a la batería declarada.
"""

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from veritas.fem.adapters.singularity import (
    InvalidStiffnessError,
    Matrix,
    MechanismMode,
    mechanism_mode,
    pivot_check,
)
from veritas.fem.spatial.protocol import (
    AXES,
    DOFS_PER_NODE,
    SpatialBeam,
    SpatialBeamLoad,
    SpatialRequest,
)
from veritas.fem.spatial.protocol import Vec3 as Triple

GLOBAL_COMPONENTS: Final = ("Ux", "Uy", "Uz", "Rx", "Ry", "Rz")
# Coordenada libre oblicua: traslación tangente (t1, t2) o giro (r1, r2), por orden en la terna.
OBLIQUE_PREFIX: Final = ("t", "r")
# Seno mínimo entre una restricción y el subespacio de las anteriores; por debajo, la
# petición es redundante (Lagrange daría una matriz singular o casi): error interno.
REDUNDANCY_TOLERANCE: Final = 1e-6
# GDL libres que añade cada cadena al modelo del solver: C (6) y los giros de R (3).
CHAIN_SOLVER_DOFS: Final = 9

UNIT_AXES: Final = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
# Índices locales (I, J) de ry y rz en la 12×12 que liberan ``-releasey``/``-releasez``.
RELEASE_Y_INDICES: Final = (4, 10)
RELEASE_Z_INDICES: Final = (5, 11)
# Bloque de flexión de cada plano: (uy, rz) con Iz para -releasez y (uz, ry) con Iy para
# -releasey, en I y J. Con el código 3 (biela en ese plano) su rigidez es nula exacta.
BENDING_Z_BLOCK: Final = (1, 5, 7, 11)
BENDING_Y_BLOCK: Final = (2, 4, 8, 10)
BOTH_ENDS: Final = 3
# Nombres locales de las coordenadas propias de un aux (E2.3-a), por terna.
LOCAL_COMPONENTS: Final = (("Ux", "Uy", "Uz"), ("Rx", "Ry", "Rz"))


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def cross(a: Sequence[float], b: Sequence[float]) -> Triple:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def unit(v: Sequence[float]) -> Triple:
    norm = math.sqrt(dot(v, v))
    if not math.isfinite(norm) or norm < sys.float_info.min:
        raise InvalidStiffnessError("dirección nula o no finita en la petición")
    return (v[0] / norm, v[1] / norm, v[2] / norm)


def _matmul(a: Matrix, b: Matrix) -> Matrix:
    columns = list(zip(*b, strict=True))
    return [[dot(row, column) for column in columns] for row in a]


def _transpose(a: Matrix) -> Matrix:
    return [list(row) for row in zip(*a, strict=True)]


def _coordinates(request: SpatialRequest) -> dict[int, Triple]:
    return {node.tag: (node.x, node.y, node.z) for node in request.nodes}


def beam_axes(xi: Triple, xj: Triple, vecxz: Triple) -> tuple[float, tuple[Triple, Triple, Triple]]:
    """(L, (ex, ey, ez)): ex de I a J, ez = ``vecxz`` ortogonalizado, ey = ez × ex."""
    d = (xj[0] - xi[0], xj[1] - xi[1], xj[2] - xi[2])
    length = math.sqrt(dot(d, d))
    ex = unit(d)
    residual = _project_out(unit(vecxz), (ex,))  # Dos pasadas (ADR-001 §3).
    if math.sqrt(dot(residual, residual)) < REDUNDANCY_TOLERANCE:
        raise InvalidStiffnessError("vecxz paralelo al eje de la barra")
    ez = unit(residual)
    return length, (ex, cross(ez, ex), ez)


def local_beam_stiffness(beam: SpatialBeam, length: float) -> Matrix:
    """k local 12×12 en (ux, uy, uz, rx, ry, rz) de I y J, base de ADR-001."""
    e, ell = beam.elastic_modulus, length
    k = [[0.0] * 12 for _ in range(12)]

    def put(i: int, j: int, value: float) -> None:
        k[i][j] = k[j][i] = value

    axial, torsion = e * beam.area / ell, beam.shear_modulus * beam.torsion_constant / ell
    for first, second, value in ((0, 6, axial), (3, 9, torsion)):
        put(first, first, value)
        put(second, second, value)
        put(first, second, -value)
    # Flexión en el plano local xy (uy, rz), con Iz; en el xz (uz, ry), con Iy y signo
    # opuesto del acoplamiento: duz/dx = −ry, duy/dx = +rz.
    for u, r, inertia, sign in ((1, 5, beam.inertia_z, 1.0), (2, 4, beam.inertia_y, -1.0)):
        ei = e * inertia
        a, b, c, d = 12 * ei / ell**3, sign * 6 * ei / ell**2, 4 * ei / ell, 2 * ei / ell
        put(u, u, a)
        put(u + 6, u + 6, a)
        put(u, u + 6, -a)
        put(u, r, b)
        put(u, r + 6, b)
        put(u + 6, r, -b)
        put(u + 6, r + 6, -b)
        put(r, r, c)
        put(r + 6, r + 6, c)
        put(r, r + 6, d)
    return k


def beam_transformation(axes: tuple[Triple, Triple, Triple]) -> Matrix:
    """T 12×12 por bloques con u_local = T·u_global; las filas de cada bloque son ex, ey, ez."""
    t = [[0.0] * 12 for _ in range(12)]
    for block in range(0, 12, AXES):
        for row, axis in enumerate(axes):
            for column in range(AXES):
                t[block + row][block + column] = axis[column]
    return t


def released_indices(beam: SpatialBeam) -> tuple[int, ...]:
    """Índices locales liberados por ``-releasey``/``-releasez`` (bit 1 = I, bit 2 = J)."""
    indices = [
        index
        for code, pair in ((beam.release_y, RELEASE_Y_INDICES), (beam.release_z, RELEASE_Z_INDICES))
        for bit, index in zip((1, 2), pair, strict=True)
        if code & bit
    ]
    return tuple(sorted(indices))


def condense(
    k: Matrix, released: Sequence[int], p0: Sequence[float] | None = None
) -> tuple[Matrix, list[float] | None]:
    """Condensación estática exacta de los GDL ``released`` (hecho 14, E2.3).

    k_c = k_rr − k_rc·k_cc⁻¹·k_cr y p0_c = p0_r − k_rc·k_cc⁻¹·p0_c, por eliminación sucesiva
    de un GDL cada vez (equivalente a la de bloque); las filas y columnas liberadas quedan a
    cero. k_cc de flexión es siempre invertible ([4 2; 2 4]·EI/L o bloques independientes).
    El producto a_ic·a_cj es conmutativo en coma flotante: la simetría se conserva exacta.
    """
    work = [row[:] for row in k]
    loads = None if p0 is None else list(p0)
    size = len(work)
    for c in released:
        pivot, pivot_row = work[c][c], work[c][:]
        for i in range(size):
            a_ic = work[i][c]
            if i == c or a_ic == 0.0:
                continue
            row = work[i]
            for j in range(size):
                row[j] -= a_ic * pivot_row[j] / pivot
            if loads is not None:
                loads[i] -= a_ic * loads[c] / pivot
        work[c] = [0.0] * size
        for row in work:
            row[c] = 0.0
        if loads is not None:
            loads[c] = 0.0
    return work, loads


def condense_beam(
    beam: SpatialBeam, k: Matrix, p0: Sequence[float] | None = None
) -> tuple[Matrix, list[float] | None]:
    """``condense`` en los GDL de ``-release*`` de la barra, fuente única de k_c y p0_c.

    Con el código 3 en un plano la rigidez de ese bloque es nula en aritmética exacta, pero
    la eliminación deja un residuo de redondeo (≈ 1e-11 relativo, de signo arbitrario) que
    podría ocultar un mecanismo de biela o dar una diagonal negativa (revisión de E2.3, B1):
    el bloque se anula exacto. p0_c de ese plano: cortantes de biapoyada = los de p0 (wL/2)
    y momentos nulos.
    """
    released = released_indices(beam)
    if not released:
        return k, None if p0 is None else list(p0)
    work, loads = condense(k, released, p0)
    for code, block, shears in (
        (beam.release_z, BENDING_Z_BLOCK, (1, 7)),
        (beam.release_y, BENDING_Y_BLOCK, (2, 8)),
    ):
        if code != BOTH_ENDS:
            continue
        for i in block:
            work[i] = [0.0] * len(work)
            for row in work:
                row[i] = 0.0
        if loads is not None and p0 is not None:
            for i in block:
                loads[i] = p0[i] if i in shears else 0.0
    return work, loads


@dataclass(frozen=True, slots=True)
class BeamMatrices:
    """Longitud, base local, k local (condensada en los GDL liberados), T y K global =
    Tᵀ·k·T de una barra de la petición."""

    length: float
    axes: tuple[Triple, Triple, Triple]
    local: Matrix
    transformation: Matrix
    stiffness: Matrix


def beam_matrices(request: SpatialRequest, beam: SpatialBeam) -> BeamMatrices:
    """Sin liberaciones, la k de E1 sin cambios (bit a bit); A y J llegan efectivos."""
    coordinates = _coordinates(request)
    length, axes = beam_axes(coordinates[beam.node_i], coordinates[beam.node_j], beam.vecxz)
    local, _ = condense_beam(beam, local_beam_stiffness(beam, length))
    t = beam_transformation(axes)
    return BeamMatrices(length, axes, local, t, _matmul(_transpose(t), _matmul(local, t)))


def beam_fixed_end_forces(beam: SpatialBeam, load: SpatialBeamLoad, length: float) -> list[float]:
    """p0 de ``fixed_end_forces`` condensado con la misma k que ``beam_matrices``: fuente
    única de la salvaguarda, del residuo y de los controles de barra (E2.3, E2.5)."""
    p0 = fixed_end_forces(load, length)
    if not released_indices(beam):
        return p0
    _, condensed = condense_beam(beam, local_beam_stiffness(beam, length), p0)
    assert condensed is not None
    return condensed


def fixed_end_forces(load: SpatialBeamLoad, length: float) -> list[float]:
    """p0 local 12: fuerzas de extremo con u = 0 por ``-beamUniform`` (Wy, Wz, Wx) (hecho 9).

    localForce = k·T·u + p0; la carga nodal equivalente global es Tᵀ·(−p0).
    """
    ell = length
    p0 = [0.0] * 12
    p0[0] = p0[6] = -load.wx * ell / 2
    p0[1] = p0[7] = -load.wy * ell / 2
    p0[2] = p0[8] = -load.wz * ell / 2
    p0[5], p0[11] = -load.wy * ell**2 / 12, load.wy * ell**2 / 12
    p0[4], p0[10] = load.wz * ell**2 / 12, -load.wz * ell**2 / 12
    return p0


def _beam_dofs(request: SpatialRequest, index: dict[int, int], beam: SpatialBeam) -> list[int]:
    """GDL de los nudos del elemento: el aux en un extremo que lo tenga (E2-a)."""
    return [
        DOFS_PER_NODE * index[request.element_node(beam, end)] + k
        for end in ("I", "J")
        for k in range(6)
    ]


def _node_index(request: SpatialRequest) -> dict[int, int]:
    """Nudos de dominio y luego aux, en el orden de la petición: tag → posición."""
    tags = [node.tag for node in request.nodes] + [aux.tag for aux in request.aux_nodes]
    return {tag: position for position, tag in enumerate(tags)}


def _third_axis(axis_x: Triple, axis_yp: Triple) -> Triple:
    """Eje z de ``-orient`` de ``zeroLength``: x × y′ (normalizado)."""
    return unit(cross(axis_x, axis_yp))


def spring_direction(axis_x: Triple, axis_yp: Triple, dof: int) -> Triple:
    """Dirección del resorte en la base del apoyo: x, y = z × x, z = x × y′."""
    z = _third_axis(axis_x, axis_yp)
    axes = (unit(axis_x), cross(z, unit(axis_x)), z)
    return axes[(dof - 1) % AXES]


def full_stiffness(request: SpatialRequest) -> tuple[Matrix, dict[int, int]]:
    """K de 6 GDL por nudo de dominio y por aux, sin penalización ni restricciones; tag →
    posición. Los resortes de extremo son relativos maestro−aux: k·g·gᵀ con g = (−d, +d)."""
    index = _node_index(request)
    size = DOFS_PER_NODE * len(index)
    stiffness = [[0.0] * size for _ in range(size)]
    for beam in request.beams:
        element = beam_matrices(request, beam).stiffness
        dofs = _beam_dofs(request, index, beam)
        for i, row in enumerate(dofs):
            target, source = stiffness[row], element[i]
            for j, column in enumerate(dofs):
                target[column] += source[j]
    for spring in request.springs:
        if spring.penalty:  # Se aplica como restricción exacta.
            continue
        b = spring_direction(spring.axis_x, spring.axis_yp, spring.dof)
        base = DOFS_PER_NODE * index[spring.node] + (0 if spring.dof <= AXES else AXES)
        for i in range(AXES):
            for j in range(AXES):
                stiffness[base + i][base + j] += spring.stiffness * b[i] * b[j]
    masters = {aux.tag: aux.master for aux in request.aux_nodes}
    for end_spring in request.end_springs:
        if end_spring.penalty:  # Restricción relativa exacta (``reduced_coordinates``).
            continue
        d = spring_direction(end_spring.axis_x, end_spring.axis_yp, end_spring.dof)
        offset = 0 if end_spring.dof <= AXES else AXES
        master = DOFS_PER_NODE * index[masters[end_spring.aux]] + offset
        aux = DOFS_PER_NODE * index[end_spring.aux] + offset
        for i in range(AXES):
            for j in range(AXES):
                value = end_spring.stiffness * d[i] * d[j]
                stiffness[master + i][master + j] += value
                stiffness[aux + i][aux + j] += value
                stiffness[master + i][aux + j] -= value
                stiffness[aux + i][master + j] -= value
    return stiffness, index


def load_vector(request: SpatialRequest) -> list[float]:
    """F global en los GDL de ``full_stiffness``: cargas nodales + Tᵀ·(−p0_c) de las cargas
    de barra, en los nudos del elemento (el aux si lo hay)."""
    index = _node_index(request)
    vector = [0.0] * (DOFS_PER_NODE * len(index))
    for load in request.nodal_loads:
        base = DOFS_PER_NODE * index[load.node]
        for k, value in enumerate(load.values):
            vector[base + k] += value
    beams = {beam.tag: beam for beam in request.beams}
    for beam_load in request.beam_loads:
        beam = beams[beam_load.beam]
        matrices = beam_matrices(request, beam)
        p0 = beam_fixed_end_forces(beam, beam_load, matrices.length)
        t = matrices.transformation
        for i, dof in enumerate(_beam_dofs(request, index, beam)):
            vector[dof] -= sum(t[m][i] * p0[m] for m in range(12))
    return vector


@dataclass(frozen=True, slots=True)
class SpatialDof:
    """Coordenada libre reducida: nudo (tag), componente y dirección global unitaria.

    ``component`` es Ux…Rz si la dirección es exactamente un eje global; si no, t1/t2
    (traslación) o r1/r2 (giro) por orden dentro de la terna del nudo.

    Coordenada propia de un aux (E2.3-a): ``node`` es el nudo maestro, ``member`` la barra
    (tag) y ``end`` su extremo; ``component`` es el nombre local (Uy, Uz, Ry, Rz) si la
    dirección es paralela a un eje local de la barra, y si no t1/r1… por orden en la terna.
    """

    node: int
    component: str
    direction: Triple
    member: int | None = None
    end: str | None = None


def constrained_directions(
    request: SpatialRequest, *, penalty: bool = True
) -> dict[tuple[int, int], list[Triple]]:
    """Direcciones restringidas por (nudo, terna): 0 = traslaciones, 1 = giros. Con
    ``penalty=False``, solo las exactas (``fix``, cadena, huérfano y ``equalDOF``).

    En un aux son relativas al maestro (u_aux − u_N): ejes de ``equalDOF`` y direcciones de
    los ``zeroLength`` de penalización de extremo."""
    result: dict[tuple[int, int], list[Triple]] = {}
    for fix in request.fixes:
        for k, bit in enumerate(fix.fixity):
            if bit:
                result.setdefault((fix.node, k // AXES), []).append(UNIT_AXES[k % AXES])
    for chain in request.chains:
        result.setdefault((chain.node, 0), []).append(unit(chain.normal))
    for spring in request.springs if penalty else ():
        if spring.penalty:
            direction = spring_direction(spring.axis_x, spring.axis_yp, spring.dof)
            result.setdefault((spring.node, (spring.dof - 1) // AXES), []).append(direction)
    for orphan in request.orphan_fixes:
        for k, bit in enumerate(orphan.rotations):
            if bit:
                result.setdefault((orphan.node, 1), []).append(UNIT_AXES[k])
    _end_constraints(request, result, penalty=penalty)
    return result


def _end_constraints(
    request: SpatialRequest, result: dict[tuple[int, int], list[Triple]], *, penalty: bool
) -> None:
    for equal in request.end_equals:
        for dof in equal.dofs:
            result.setdefault((equal.aux, (dof - 1) // AXES), []).append(
                UNIT_AXES[(dof - 1) % AXES]
            )
    for end_spring in request.end_springs if penalty else ():
        if end_spring.penalty:
            direction = spring_direction(end_spring.axis_x, end_spring.axis_yp, end_spring.dof)
            result.setdefault((end_spring.aux, (end_spring.dof - 1) // AXES), []).append(direction)


def _project_out(v: Sequence[float], basis: Sequence[Triple]) -> Triple:
    """v menos su proyección sobre ``basis`` (ortonormal), en dos pasadas."""
    x, y, z = v
    for _ in range(2):
        for b in basis:
            along = x * b[0] + y * b[1] + z * b[2]
            x, y, z = x - along * b[0], y - along * b[1], z - along * b[2]
    return x, y, z


def free_directions(
    constraints: Sequence[Triple], candidates: Sequence[Triple] = UNIT_AXES
) -> tuple[Triple, ...]:
    """Complemento ortonormal determinista de ``constraints`` (unitarias) en R³.

    Las restricciones se ortonormalizan en orden; una que caiga en el subespacio de las
    anteriores (seno < ``REDUNDANCY_TOLERANCE``) es ``InvalidStiffnessError``. El
    complemento se toma de ``candidates`` (base ortonormal; por defecto los ejes globales),
    eligiendo cada vez el de mayor residuo (empate: el primero), de modo que un candidato no
    restringido de forma exacta conserva su vector exacto. Un aux usa la base local de su
    barra (E2.3-a): su complemento es exactamente ortogonal a las restricciones.
    """
    basis: list[Triple] = []
    for constraint in constraints:
        residual = _project_out(constraint, basis)
        norm = math.sqrt(dot(residual, residual))
        if norm < REDUNDANCY_TOLERANCE:
            raise InvalidStiffnessError("restricciones de apoyo redundantes en un nudo")
        basis.append((residual[0] / norm, residual[1] / norm, residual[2] / norm))
    free: list[Triple] = []
    while len(basis) < AXES:
        residuals = [_project_out(axis, basis) for axis in candidates]
        norms = [dot(r, r) for r in residuals]
        best = max(range(AXES), key=lambda k: (norms[k], -k))
        direction = unit(residuals[best])
        basis.append(direction)
        free.append(direction)
    return tuple(free)


def reduced_coordinates(request: SpatialRequest) -> list[tuple[SpatialDof, dict[int, float]]]:
    """Coordenadas libres por nudo de dominio, traslaciones y luego giros, y después las
    propias de cada aux: (etiqueta, columna de T con índice global → coeficiente).

    u_aux = u_N + Σ δ_k·d_k: cada columna del maestro N se replica en las componentes del
    aux (la cadena C→N→aux queda exacta) y las δ_k propias del aux son el complemento de sus
    restricciones relativas (``equalDOF`` y penalización, tratadas como exactas, como D4).
    """
    constraints = constrained_directions(request)
    coordinates: list[tuple[SpatialDof, dict[int, float]]] = []
    for position, node in enumerate(request.nodes):
        for triple in (0, 1):
            base = DOFS_PER_NODE * position + AXES * triple
            oblique = 0
            for direction in free_directions(constraints.get((node.tag, triple), [])):
                if direction in UNIT_AXES:
                    component = GLOBAL_COMPONENTS[AXES * triple + UNIT_AXES.index(direction)]
                else:
                    oblique += 1
                    component = f"{OBLIQUE_PREFIX[triple]}{oblique}"
                column = {base + k: v for k, v in enumerate(direction) if v != 0.0}
                coordinates.append((SpatialDof(node.tag, component, direction), column))
    if request.aux_nodes:
        _aux_coordinates(request, constraints, coordinates)
    return coordinates


def _aux_coordinates(
    request: SpatialRequest,
    constraints: dict[tuple[int, int], list[Triple]],
    coordinates: list[tuple[SpatialDof, dict[int, float]]],
) -> None:
    index = _node_index(request)
    for aux in request.aux_nodes:  # Réplica del maestro en el aux.
        shift = DOFS_PER_NODE * (index[aux.tag] - index[aux.master])
        master = range(DOFS_PER_NODE * index[aux.master], DOFS_PER_NODE * (index[aux.master] + 1))
        for _, column in coordinates:
            column.update({i + shift: t for i, t in list(column.items()) if i in master})
    beams = {beam.tag: beam for beam in request.beams}
    nodes = _coordinates(request)
    for aux in request.aux_nodes:
        beam = beams[aux.beam]
        _, axes = beam_axes(nodes[beam.node_i], nodes[beam.node_j], beam.vecxz)
        for triple in (0, 1):
            base = DOFS_PER_NODE * index[aux.tag] + AXES * triple
            oblique = 0
            for direction in free_directions(constraints.get((aux.tag, triple), []), axes):
                local = _parallel_axis(direction, axes)
                if local is None:
                    oblique += 1
                    component = f"{OBLIQUE_PREFIX[triple]}{oblique}"
                else:
                    component = LOCAL_COMPONENTS[triple][local]
                column = {base + k: v for k, v in enumerate(direction) if v != 0.0}
                label = SpatialDof(aux.master, component, direction, aux.beam, aux.end)
                coordinates.append((label, column))


def _parallel_axis(direction: Triple, axes: Sequence[Triple]) -> int | None:
    """Eje local (0-2) paralelo a ``direction`` en forma seno (≤ ``REDUNDANCY_TOLERANCE``,
    que acota ``tol_axis``: el caso coordenado de E2-a cuenta como paralelo), o ``None``."""
    for k, axis in enumerate(axes):
        sine = cross(direction, axis)
        if math.sqrt(dot(sine, sine)) <= REDUNDANCY_TOLERANCE:
            return k
    return None


def reduced_stiffness(request: SpatialRequest) -> tuple[Matrix, list[SpatialDof]]:
    """K reducida = Tᵀ·K·T con las restricciones aplicadas de forma exacta."""
    stiffness, _ = full_stiffness(request)
    coordinates = reduced_coordinates(request)
    # K·T por columnas dispersas y luego Tᵀ·(K·T).
    products = [
        [sum(row[i] * t for i, t in column.items()) for row in stiffness]
        for _, column in coordinates
    ]
    reduced = [
        [sum(t * product[i] for i, t in column_a.items()) for product in products]
        for _, column_a in coordinates
    ]
    return reduced, [label for label, _ in coordinates]


def validate_request(request: SpatialRequest) -> None:
    """Defensa ante una petición que el traductor nunca emite: propiedades de barra no
    positivas, rigidez de resorte negativa (o nula si es de penalización), cadena con a ≤ 0,
    kr ≤ 0 o tangente paralela a la normal. Se rechaza como error interno, no como
    mecanismo (revisión de la pieza 4, m5); las direcciones nulas las rechaza ``unit``.

    Extremos (E2.3, R10): A y J ≥ 0, con 0 si y solo si su marca de liberación; referencias
    de aux, ``equalDOF`` y resortes de extremo coherentes; huérfanos en nudos de dominio.
    Va antes de ``reduced_coordinates``: una referencia rota es FAILED, no ``KeyError``."""
    for beam in request.beams:
        values = (beam.elastic_modulus, beam.shear_modulus, beam.inertia_y, beam.inertia_z)
        if (
            any(value <= 0.0 for value in values)
            or beam.area < 0.0
            or beam.torsion_constant < 0.0
            or (beam.area == 0.0) != beam.axial_released
            or (beam.torsion_constant == 0.0) != beam.torsion_released
        ):
            raise InvalidStiffnessError(f"propiedad no positiva en la barra {beam.tag}")
    _validate_ends(request)
    for spring in request.springs:
        if spring.stiffness < 0.0 or (spring.penalty and spring.stiffness == 0.0):
            raise InvalidStiffnessError(f"rigidez no válida en el resorte {spring.tag}")
        spring_direction(spring.axis_x, spring.axis_yp, spring.dof)
    for chain in request.chains:
        if chain.length <= 0.0 or chain.stiffness <= 0.0:
            raise InvalidStiffnessError(f"longitud o kr no positivos en la cadena {chain.tag}")
        sine = cross(unit(chain.normal), unit(chain.tangent))
        if math.sqrt(dot(sine, sine)) < REDUNDANCY_TOLERANCE:
            raise InvalidStiffnessError(f"tangente paralela a la normal en la cadena {chain.tag}")


def _validate_ends(request: SpatialRequest) -> None:
    """Referencias de los extremos no rígidos (E2.3). La redundancia entre un ``equalDOF`` y
    una penalización en la misma dirección la rechaza ``free_directions``."""
    domain = {node.tag for node in request.nodes}
    support_tags = {spring.ground for spring in request.springs} | {
        tag for chain in request.chains for tag in (chain.c_node, chain.r_node, chain.ground)
    }
    beams = {beam.tag: beam for beam in request.beams}
    aux_tags: set[int] = set()
    ends: set[tuple[int, str]] = set()
    for aux in request.aux_nodes:
        beam = beams.get(aux.beam)
        if (
            beam is None
            or aux.master != (beam.node_i if aux.end == "I" else beam.node_j)
            or aux.tag in domain
            or aux.tag in support_tags
            or aux.tag in aux_tags
            or (aux.beam, aux.end) in ends
        ):
            raise InvalidStiffnessError(f"nudo aux {aux.tag} incoherente con la petición")
        aux_tags.add(aux.tag)
        ends.add((aux.beam, aux.end))
    equals = [equal.aux for equal in request.end_equals]
    if len(set(equals)) != len(equals) or not set(equals) <= aux_tags:
        raise InvalidStiffnessError("equalDOF de extremo repetido o sin aux")
    for end_spring in request.end_springs:
        if end_spring.aux not in aux_tags or not end_spring.stiffness > 0.0:
            raise InvalidStiffnessError(f"resorte de extremo {end_spring.tag} no válido")
        spring_direction(end_spring.axis_x, end_spring.axis_yp, end_spring.dof)
    if any(orphan.node not in domain for orphan in request.orphan_fixes):
        raise InvalidStiffnessError("giro huérfano fijado fuera de los nudos de dominio")


def solver_free_dofs(request: SpatialRequest) -> int:
    """GDL libres del modelo del solver (D6): nudos de dominio y aux no fijados por ``fix``
    (apoyo o huérfano) y ``CHAIN_SOLVER_DOFS`` por cadena; las tierras están fijas y los
    multiplicadores no cuentan. Cota de ``max_free_dofs``; la reducida nunca es mayor."""
    fixed: dict[int, list[int]] = {fix.node: list(fix.fixity) for fix in request.fixes}
    for orphan in request.orphan_fixes:
        bits = fixed.setdefault(orphan.node, [0] * DOFS_PER_NODE)
        bits[AXES:] = [max(a, b) for a, b in zip(bits[AXES:], orphan.rotations, strict=True)]
    nodes = len(request.nodes) + len(request.aux_nodes)
    blocked = sum(sum(bits) for bits in fixed.values())
    return DOFS_PER_NODE * nodes - blocked + CHAIN_SOLVER_DOFS * len(request.chains)


@dataclass(frozen=True, slots=True)
class SpatialPivotReport:
    """Resultado de la salvaguarda espacial. ``min_pivot`` es relativo (matriz escalada)."""

    free_dofs: int
    min_pivot: float | None
    dof: SpatialDof | None
    singular: bool
    labels: tuple[SpatialDof, ...] = ()
    mode: MechanismMode | None = None


def check_singularity(request: SpatialRequest, tol_pivot: float) -> SpatialPivotReport:
    """Salvaguarda completa sobre la petición; con singularidad, también el modo (T-017 D3)."""
    validate_request(request)
    reduced, labels = reduced_stiffness(request)
    pivot, position, singular = pivot_check(reduced, tol_pivot)
    return SpatialPivotReport(
        free_dofs=len(labels),
        min_pivot=pivot,
        dof=None if position is None else labels[position],
        singular=singular,
        labels=tuple(labels),
        mode=mechanism_mode(reduced, tol_pivot) if singular else None,
    )
