"""Rigidez espacial y salvaguarda de singularidad previa al solver (plan T-018, pieza 4).

Ensamblaje independiente, en Python puro, de la MISMA ``SpatialRequest`` que recibe el
worker 3D: barras Euler–Bernoulli 12×12 en la base de ADR-001 (Iz en el bloque uy–rz, Iy en
el bloque uz–ry, ey = ez × ex con ez = ``vecxz``), resortes de apoyo orientados como k·b·bᵀ
y restricciones exactas. Por nudo y por terna (traslaciones, giros) las coordenadas libres
son el complemento ortonormal de {e_k fijos}, {normales de cadena} y {direcciones de
penalización}: la cadena es exactamente n·u = 0 y la penalización se aplica de forma
exacta, como en la salvaguarda plana. Los nudos C, R y las tierras no entran: el giro de R
alrededor de n es un modo desacoplado que fija el resorte kr.

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


@dataclass(frozen=True, slots=True)
class BeamMatrices:
    """Longitud, base local, k local, T y K global = Tᵀ·k·T de una barra de la petición."""

    length: float
    axes: tuple[Triple, Triple, Triple]
    local: Matrix
    transformation: Matrix
    stiffness: Matrix


def beam_matrices(request: SpatialRequest, beam: SpatialBeam) -> BeamMatrices:
    coordinates = _coordinates(request)
    length, axes = beam_axes(coordinates[beam.node_i], coordinates[beam.node_j], beam.vecxz)
    local = local_beam_stiffness(beam, length)
    t = beam_transformation(axes)
    return BeamMatrices(length, axes, local, t, _matmul(_transpose(t), _matmul(local, t)))


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


def _beam_dofs(index: dict[int, int], beam: SpatialBeam) -> list[int]:
    return [
        DOFS_PER_NODE * index[node] + k for node in (beam.node_i, beam.node_j) for k in range(6)
    ]


def _node_index(request: SpatialRequest) -> dict[int, int]:
    return {node.tag: position for position, node in enumerate(request.nodes)}


def _third_axis(axis_x: Triple, axis_yp: Triple) -> Triple:
    """Eje z de ``-orient`` de ``zeroLength``: x × y′ (normalizado)."""
    return unit(cross(axis_x, axis_yp))


def spring_direction(axis_x: Triple, axis_yp: Triple, dof: int) -> Triple:
    """Dirección del resorte en la base del apoyo: x, y = z × x, z = x × y′."""
    z = _third_axis(axis_x, axis_yp)
    axes = (unit(axis_x), cross(z, unit(axis_x)), z)
    return axes[(dof - 1) % AXES]


def full_stiffness(request: SpatialRequest) -> tuple[Matrix, dict[int, int]]:
    """K de 6 GDL por nudo de dominio, sin penalización ni restricciones; tag → índice."""
    index = _node_index(request)
    size = DOFS_PER_NODE * len(request.nodes)
    stiffness = [[0.0] * size for _ in range(size)]
    for beam in request.beams:
        element = beam_matrices(request, beam).stiffness
        dofs = _beam_dofs(index, beam)
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
    return stiffness, index


def load_vector(request: SpatialRequest) -> list[float]:
    """F global de los nudos de dominio: cargas nodales + Tᵀ·(−p0) de las cargas de barra."""
    index = _node_index(request)
    vector = [0.0] * (DOFS_PER_NODE * len(request.nodes))
    for load in request.nodal_loads:
        base = DOFS_PER_NODE * index[load.node]
        for k, value in enumerate(load.values):
            vector[base + k] += value
    beams = {beam.tag: beam for beam in request.beams}
    for beam_load in request.beam_loads:
        beam = beams[beam_load.beam]
        matrices = beam_matrices(request, beam)
        p0 = fixed_end_forces(beam_load, matrices.length)
        t = matrices.transformation
        for i, dof in enumerate(_beam_dofs(index, beam)):
            vector[dof] -= sum(t[m][i] * p0[m] for m in range(12))
    return vector


@dataclass(frozen=True, slots=True)
class SpatialDof:
    """Coordenada libre reducida: nudo (tag), componente y dirección global unitaria.

    ``component`` es Ux…Rz si la dirección es exactamente un eje global; si no, t1/t2
    (traslación) o r1/r2 (giro) por orden dentro de la terna del nudo.
    """

    node: int
    component: str
    direction: Triple


def constrained_directions(
    request: SpatialRequest, *, penalty: bool = True
) -> dict[tuple[int, int], list[Triple]]:
    """Direcciones restringidas por (nudo, terna): 0 = traslaciones, 1 = giros. Con
    ``penalty=False``, solo las exactas (``fix`` y cadena)."""
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
    return result


def _project_out(v: Sequence[float], basis: Sequence[Triple]) -> Triple:
    """v menos su proyección sobre ``basis`` (ortonormal), en dos pasadas."""
    x, y, z = v
    for _ in range(2):
        for b in basis:
            along = x * b[0] + y * b[1] + z * b[2]
            x, y, z = x - along * b[0], y - along * b[1], z - along * b[2]
    return x, y, z


def free_directions(constraints: Sequence[Triple]) -> tuple[Triple, ...]:
    """Complemento ortonormal determinista de ``constraints`` (unitarias) en R³.

    Las restricciones se ortonormalizan en orden; una que caiga en el subespacio de las
    anteriores (seno < ``REDUNDANCY_TOLERANCE``) es ``InvalidStiffnessError``. El
    complemento se toma de los ejes globales, eligiendo cada vez el de mayor residuo (empate:
    el primero), de modo que un eje no restringido de forma exacta conserva su vector exacto.
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
        residuals = [_project_out(axis, basis) for axis in UNIT_AXES]
        norms = [dot(r, r) for r in residuals]
        best = max(range(AXES), key=lambda k: (norms[k], -k))
        direction = unit(residuals[best])
        basis.append(direction)
        free.append(direction)
    return tuple(free)


def reduced_coordinates(request: SpatialRequest) -> list[tuple[SpatialDof, dict[int, float]]]:
    """Coordenadas libres por nudo de dominio, traslaciones y luego giros: (etiqueta, columna
    de T con índice global → coeficiente)."""
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
    return coordinates


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

    Defensa transitoria (plan T-018, E2.1): hasta E2.3 la salvaguarda no representa los
    extremos no rígidos del protocolo v2; se rechazan para no calcular una K rígida."""
    if (
        request.orphan_fixes
        or request.aux_nodes
        or request.end_equals
        or request.end_springs
        or any(
            beam.release_y or beam.release_z or beam.axial_released or beam.torsion_released
            for beam in request.beams
        )
    ):
        raise InvalidStiffnessError("extremos de barra no rígidos: salvaguarda pendiente (E2.3)")
    for beam in request.beams:
        values = (
            beam.area,
            beam.elastic_modulus,
            beam.shear_modulus,
            beam.torsion_constant,
            beam.inertia_y,
            beam.inertia_z,
        )
        if any(value <= 0.0 for value in values):
            raise InvalidStiffnessError(f"propiedad no positiva en la barra {beam.tag}")
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
