"""Salvaguarda de singularidad previa al solver (plan T-007, D3).

Ensamblaje independiente, en Python puro, de la rigidez reducida de la MISMA petición
que se envía al worker: barras Euler–Bernoulli 2D, resortes de apoyo y restricciones
rígidas exactas. Una traslación oblicua rígida (resorte de penalización en la petición)
se aplica aquí de forma exacta: los GDL del nudo se giran a (tangente, normal) y se
elimina la normal. Después: escalado de Jacobi y LDLᵀ con pivotaje diagonal completo.

La garantía es condicionada a ``tol_pivot`` y a la batería de pruebas declarada; no es
una demostración general (revisión B1). Desde T-010 incluye el giro propio de los extremos
no rígidos y los giros huérfanos restringidos. Desde T-017 (D3), con singularidad se
calcula además el modo de mecanismo completo (espacio nulo y participación por GDL) con la
misma factorización; el GDL del pivote sigue registrado como orientativo.
"""

import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from veritas.fem.adapters.protocol import PlanarBeam, PlanarRequest

COMPONENTS: Final = ("u1", "u2", "theta")
TANGENT: Final = "t"

type Matrix = list[list[float]]


class InvalidStiffnessError(ArithmeticError):
    """Diagonal negativa o no finita: error interno, no singularidad."""


@dataclass(frozen=True, slots=True)
class DofLabel:
    """GDL reducido: nudo (tag) y componente (u1, u2, theta o t = tangente oblicua)."""

    node: int
    component: str


@dataclass(frozen=True, slots=True)
class PivotReport:
    """Resultado de la salvaguarda. ``min_pivot`` es relativo (matriz escalada)."""

    free_dofs: int
    min_pivot: float | None
    dof: DofLabel | None
    singular: bool
    labels: tuple[DofLabel, ...] = ()
    mode: "MechanismMode | None" = None


def local_beam_stiffness(length: float, area: float, modulus: float, inertia: float) -> Matrix:
    """k local Euler–Bernoulli 2D en (u₁, u₂, θ) de I y J."""
    ea, ei = modulus * area / length, modulus * inertia
    a, b, d, e = 12 * ei / length**3, 6 * ei / length**2, 4 * ei / length, 2 * ei / length
    return [
        [ea, 0.0, 0.0, -ea, 0.0, 0.0],
        [0.0, a, b, 0.0, -a, b],
        [0.0, b, d, 0.0, -b, e],
        [-ea, 0.0, 0.0, ea, 0.0, 0.0],
        [0.0, -a, -b, 0.0, a, -b],
        [0.0, b, e, 0.0, -b, d],
    ]


def beam_rotation(xi: tuple[float, float], xj: tuple[float, float]) -> tuple[float, Matrix]:
    """(L, T) con u_local = T·u_global; T es ortogonal."""
    dx, dz = xj[0] - xi[0], xj[1] - xi[1]
    length = math.hypot(dx, dz)
    c, s = dx / length, dz / length
    rotation = [[0.0] * 6 for _ in range(6)]
    for block in (0, 3):
        rotation[block][block], rotation[block][block + 1] = c, s
        rotation[block + 1][block], rotation[block + 1][block + 1] = -s, c
        rotation[block + 2][block + 2] = 1.0
    return length, rotation


def beam_stiffness(
    xi: tuple[float, float], xj: tuple[float, float], area: float, modulus: float, inertia: float
) -> Matrix:
    length, rotation = beam_rotation(xi, xj)
    local = local_beam_stiffness(length, area, modulus, inertia)
    # K_global = Tᵀ k T
    kt = [[sum(local[i][m] * rotation[m][j] for m in range(6)) for j in range(6)] for i in range(6)]
    return [[sum(rotation[m][i] * kt[m][j] for m in range(6)) for j in range(6)] for i in range(6)]


def aux_rotation_index(request: PlanarRequest) -> dict[int, int]:
    """Índice global del giro propio de cada aux de extremo (tras los 3·N GDL de nudos)."""
    first = 3 * len(request.nodes)
    return {link.aux: first + k for k, link in enumerate(request.end_links)}


def element_displacement(
    request: PlanarRequest, beam: PlanarBeam, displacement: Mapping[int, Sequence[float]]
) -> tuple[float, ...]:
    """(u₁, u₂, θ) de los extremos I, J del elemento: traslación del nudo, giro del aux.

    ``displacement`` incluye nudos y aux de extremo (tag → (u₁, u₂, θ)).
    """
    values: list[float] = []
    for end, node in (("I", beam.node_i), ("J", beam.node_j)):
        element_node = request.element_node(beam, end)
        values += [displacement[node][0], displacement[node][1], displacement[element_node][2]]
    return tuple(values)


def _add_end_springs(
    request: PlanarRequest, stiffness: Matrix, index: dict[int, int], aux_theta: dict[int, int]
) -> None:
    """Resorte de extremo relativo: k·[[1, −1], [−1, 1]] sobre (θ maestro, θ aux)."""
    for link in request.end_links:
        if link.stiffness is None:
            continue
        master, aux = 3 * index[link.master] + 2, aux_theta[link.aux]
        stiffness[master][master] += link.stiffness
        stiffness[aux][aux] += link.stiffness
        stiffness[master][aux] -= link.stiffness
        stiffness[aux][master] -= link.stiffness


def full_stiffness(request: PlanarRequest) -> tuple[Matrix, dict[int, int]]:
    """K completa sin penalizaciones ni restricciones; mapa tag → índice del nudo.

    3 GDL por nudo y, por cada extremo no rígido (T-010), el giro propio del aux. El aux
    es un alias del maestro en u₁, u₂ (``equalDOF``): la barra usa (u₁, u₂) del maestro y
    θ del aux; un resorte de extremo es k·[[1, −1], [−1, 1]] sobre (θ maestro, θ aux).
    """
    index = {node.tag: position for position, node in enumerate(request.nodes)}
    coordinates = {node.tag: (node.x1, node.x2) for node in request.nodes}
    aux_theta = aux_rotation_index(request)
    size = 3 * len(request.nodes) + len(aux_theta)
    stiffness = [[0.0] * size for _ in range(size)]
    for beam in request.beams:
        element = beam_stiffness(
            coordinates[beam.node_i],
            coordinates[beam.node_j],
            beam.area,
            beam.elastic_modulus,
            beam.inertia,
        )
        dofs: list[int] = []
        for end, node in (("I", beam.node_i), ("J", beam.node_j)):
            base = 3 * index[node]
            element_node = request.element_node(beam, end)
            theta = aux_theta[element_node] if element_node in aux_theta else base + 2
            dofs += [base, base + 1, theta]
        for i, row in enumerate(dofs):
            for j, column in enumerate(dofs):
                stiffness[row][column] += element[i][j]
    _add_end_springs(request, stiffness, index, aux_theta)
    for spring in request.springs:
        if spring.penalty:
            continue
        base = 3 * index[spring.node]
        if spring.dof == 3:  # noqa: PLR2004 (dirección 3 = giro)
            stiffness[base + 2][base + 2] += spring.stiffness
            continue
        v = spring.axis1 if spring.dof == 1 else spring.axis2
        for i in range(2):
            for j in range(2):
                stiffness[base + i][base + j] += spring.stiffness * v[i] * v[j]
    return stiffness, index


def translational_diagonal(request: PlanarRequest) -> dict[int, float]:
    """max(K[u1,u1], K[u2,u2]) por nudo, sin penalización (revisión R1).

    Escala local del peso de penalización de D4: no mezcla giros (N·m/rad) ni rigideces
    de otros nudos, que con un resorte muy rígido ajeno anularían la rigidez tangente.
    """
    stiffness, index = full_stiffness(request)
    return {
        tag: max(stiffness[3 * i][3 * i], stiffness[3 * i + 1][3 * i + 1])
        for tag, i in index.items()
    }


def reduced_coordinates(request: PlanarRequest) -> list[tuple[DofLabel, dict[int, float]]]:
    """Coordenadas libres: GDL no fijados y, en nudos con traslación oblicua rígida, la
    tangente t = (−n₂, n₁) en lugar de (u1, u2)."""
    index = {node.tag: position for position, node in enumerate(request.nodes)}
    fixed = {(fix.node, k) for fix in request.fixes for k in range(3) if fix.fixity[k]}
    fixed |= {(node, 2) for node in request.orphan_fixes}  # Giro huérfano (T-010, D3).
    oblique: dict[int, tuple[float, float]] = {}
    for spring in request.springs:
        if spring.penalty:
            oblique[spring.node] = spring.axis1 if spring.dof == 1 else spring.axis2
    coordinates: list[tuple[DofLabel, dict[int, float]]] = []
    for node in request.nodes:
        base = 3 * index[node.tag]
        if node.tag in oblique:
            n1, n2 = oblique[node.tag]
            coordinates.append((DofLabel(node.tag, TANGENT), {base: -n2, base + 1: n1}))
            translations: tuple[int, ...] = ()
        else:
            translations = (0, 1)
        for k in (*translations, 2):
            if (node.tag, k) not in fixed:
                coordinates.append((DofLabel(node.tag, COMPONENTS[k]), {base + k: 1.0}))
    for aux, position in aux_rotation_index(request).items():  # Nunca fijado.
        coordinates.append((DofLabel(aux, COMPONENTS[2]), {position: 1.0}))
    return coordinates


def reduced_stiffness(request: PlanarRequest) -> tuple[Matrix, list[DofLabel]]:
    """K reducida = Tᵀ K T con las restricciones aplicadas de forma exacta."""
    stiffness, _ = full_stiffness(request)
    coordinates = reduced_coordinates(request)
    reduced = [
        [
            sum(ta * stiffness[i][j] * tb for i, ta in col_a.items() for j, tb in col_b.items())
            for _, col_b in coordinates
        ]
        for _, col_a in coordinates
    ]
    return reduced, [label for label, _ in coordinates]


def pivot_check(matrix: Matrix, tol_pivot: float) -> tuple[float | None, int | None, bool]:
    """(pivote mínimo relativo, índice original, singular) de una K simétrica.

    Diagonal negativa o no finita → ``InvalidStiffnessError``. Diagonal nula o subnormal
    (GDL sin rigidez) → singular con pivote 0. Si no, escalado de Jacobi y LDLᵀ con pivotaje
    diagonal simétrico completo. No se compara la diagonal con max diag: mezclaría N/m con
    N·m/rad y un resorte muy rígido haría pasar por singular un GDL ajeno (revisión P10).
    """
    pivot, position, singular, _ = pivot_factorization(matrix, tol_pivot)
    return pivot, position, singular


def pivot_factorization(
    matrix: Matrix, tol_pivot: float
) -> tuple[float | None, int | None, bool, "_Factorization | None"]:
    """``pivot_check`` y la factorización completa si la matriz no es singular (para
    ``solve_factored``; contraste espacial, T-018 E2.5); ``None`` si no la hay."""
    size = len(matrix)
    if size == 0:
        return None, None, False, None
    diagonal = _checked_diagonal(matrix)
    smallest = min(range(size), key=lambda i: diagonal[i])
    if diagonal[smallest] < sys.float_info.min:
        return 0.0, smallest, True, None
    factor = _factor(matrix, tol_pivot)
    return factor.pivot, factor.position, factor.singular, None if factor.singular else factor


def _checked_diagonal(matrix: Matrix) -> list[float]:
    diagonal = [matrix[i][i] for i in range(len(matrix))]
    if any(not math.isfinite(value) or value < 0.0 for value in diagonal):
        raise InvalidStiffnessError("diagonal de rigidez negativa o no finita")
    return diagonal


@dataclass(frozen=True, slots=True)
class _Factorization:
    """LDLᵀ con escalado de Jacobi y pivotaje diagonal completo, detenida en el primer
    pivote ≤ tol. ``work`` queda permutado por ``order``: sus ``steps`` primeras filas, de
    la diagonal a la derecha, son U = D·Lᵀ."""

    pivot: float
    position: int
    singular: bool
    scaled: Matrix
    work: Matrix
    order: list[int]
    steps: int


def _factor(matrix: Matrix, tol_pivot: float) -> _Factorization:
    """Diagonal estrictamente positiva (la comprueba el llamador)."""
    size = len(matrix)
    scale = [1.0 / math.sqrt(matrix[i][i]) for i in range(size)]
    scaled = [[matrix[i][j] * scale[i] * scale[j] for j in range(size)] for i in range(size)]
    work = [row[:] for row in scaled]
    order = list(range(size))
    min_pivot, min_index = math.inf, order[0]
    for k in range(size):
        p = max(range(k, size), key=lambda i: work[i][i])
        if p != k:
            work[k], work[p] = work[p], work[k]
            for row in work:
                row[k], row[p] = row[p], row[k]
            order[k], order[p] = order[p], order[k]
        pivot = work[k][k]
        if pivot < min_pivot:
            min_pivot, min_index = pivot, order[k]
        if not math.isfinite(pivot) or pivot <= tol_pivot:
            return _Factorization(pivot, order[k], True, scaled, work, order, k)
        row_k = work[k]
        for i in range(k + 1, size):
            factor = work[i][k] / pivot
            if factor != 0.0:
                row_i = work[i]
                row_i[k + 1 :] = [
                    a - factor * b for a, b in zip(row_i[k + 1 :], row_k[k + 1 :], strict=True)
                ]
    return _Factorization(min_pivot, min_index, False, scaled, work, order, size)


def solve_factored(matrix: Matrix, factor: _Factorization, rhs: Sequence[float]) -> list[float]:
    """x con K·x = rhs a partir de la factorización completa de K = ``matrix`` (T-018 E2.5).

    K = S⁻¹·Ks·S⁻¹ con S el escalado de Jacobi (``scaled`` = Ks) y P·Ks·Pᵀ = L·D·Lᵀ, con
    U = D·Lᵀ en las filas de ``work`` (P según ``order``): sustitución O(n²).
    """
    size = len(factor.order)
    if factor.singular or factor.steps != size:
        raise ValueError("factorización incompleta")
    # Ks·y = S·rhs con x = S·y y S_ii = 1/√K_ii, el mismo escalado que ``_factor``.
    scale = [1.0 / math.sqrt(matrix[i][i]) for i in range(size)]
    u = factor.work
    w = [rhs[factor.order[i]] * scale[factor.order[i]] for i in range(size)]
    for i in range(size):  # L·w = c (L unitaria, L_ik = U_ki / U_kk).
        w[i] -= sum(u[k][i] / u[k][k] * w[k] for k in range(i))
    z = [0.0] * size
    for i in reversed(range(size)):  # U·z = w.
        z[i] = (w[i] - sum(u[i][j] * z[j] for j in range(i + 1, size))) / u[i][i]
    x = [0.0] * size
    for i in range(size):
        x[factor.order[i]] = z[i] * scale[factor.order[i]]
    return x


# Modo de mecanismo (plan T-017, D3). Constantes declaradas, registradas en la procedencia.
MECHANISM_PARTICIPATION: Final = 1e-6
MECHANISM_GUARD_BAND: Final = (1e-8, 1e-4)
MECHANISM_RESIDUAL_FACTOR: Final = 100.0


@dataclass(frozen=True, slots=True)
class MechanismMode:
    """Espacio nulo de la K reducida escalada: nulidad y participación p_i = Σ_q Q_iq².

    ``conclusive`` es False si la comprobación a posteriori no se cumple (‖K̃·q‖ mayor que
    c·tol_pivot o algún p_i dentro de la banda de guarda): el modo no se publica.
    """

    nullity: int
    participation: tuple[float, ...]
    conclusive: bool
    residual: float


def _null_vectors(factor: _Factorization) -> list[list[float]]:
    """Base N = [−A⁻¹B; I] del bloque restante, en el orden original de la matriz.

    A⁻¹B se obtiene de las filas U ya eliminadas: U_A·X = U_B por sustitución hacia atrás.
    """
    size, k = len(factor.work), factor.steps
    work = factor.work
    vectors: list[list[float]] = []
    for q in range(k, size):
        x = [0.0] * k
        for r in range(k - 1, -1, -1):
            value = work[r][q] - sum(work[r][c] * x[c] for c in range(r + 1, k))
            x[r] = value / work[r][r]
        vector = [0.0] * size
        for j in range(k):
            vector[factor.order[j]] = -x[j]
        vector[factor.order[q]] = 1.0
        vectors.append(vector)
    return vectors


def _orthonormal(vectors: list[list[float]]) -> list[list[float]]:
    """Gram–Schmidt modificado con reortogonalización (dos pasadas)."""
    basis: list[list[float]] = []
    for vector in vectors:
        v = vector[:]
        for _ in range(2):
            for b in basis:
                dot = sum(x * y for x, y in zip(v, b, strict=True))
                v = [x - dot * y for x, y in zip(v, b, strict=True)]
        norm = math.sqrt(sum(x * x for x in v))
        if norm > 0.0:
            basis.append([x / norm for x in v])
    return basis


def mechanism_mode(matrix: Matrix, tol_pivot: float) -> MechanismMode:
    """Modo de mecanismo de una K simétrica singular según ``pivot_check``.

    Las coordenadas con diagonal nula son vectores aislados del espacio nulo y se separan
    antes de escalar; el resto usa la misma factorización que decide la singularidad.
    """
    size = len(matrix)
    diagonal = _checked_diagonal(matrix)
    zero = [i for i in range(size) if diagonal[i] < sys.float_info.min]
    rest = [i for i in range(size) if diagonal[i] >= sys.float_info.min]
    vectors: list[list[float]] = []
    for i in zero:
        vector = [0.0] * size
        vector[i] = 1.0
        vectors.append(vector)
    scaled_rows: dict[int, list[float]] = {}
    if rest:
        sub = [[matrix[i][j] for j in rest] for i in rest]
        factor = _factor(sub, tol_pivot)
        if factor.singular:
            for local in _null_vectors(factor):
                vector = [0.0] * size
                for position, value in zip(rest, local, strict=True):
                    vector[position] = value
                vectors.append(vector)
        for a, i in enumerate(rest):
            row = [0.0] * size
            for b, j in enumerate(rest):
                row[j] = factor.scaled[a][b]
            scaled_rows[i] = row
    # Con K semidefinida positiva, la fila y la columna de una diagonal nula son nulas. Si
    # no lo son (K indefinida por error de ensamblaje), el modo no es concluyente.
    coupled = any(matrix[i][j] != 0.0 or matrix[j][i] != 0.0 for i in zero for j in rest)
    for i in zero:  # Fila escalada con escala 1.
        scaled_rows[i] = [0.0 if j in rest else matrix[i][j] for j in range(size)]
    basis = _orthonormal(vectors)
    participation = tuple(sum(q[i] ** 2 for q in basis) for i in range(size))
    residual = max(
        (
            math.sqrt(
                sum(
                    sum(a * b for a, b in zip(scaled_rows[i], q, strict=True)) ** 2
                    for i in range(size)
                )
            )
            for q in basis
        ),
        default=0.0,
    )
    low, high = MECHANISM_GUARD_BAND
    # len(basis) == len(vectors): defensa ante vectores NaN; las columnas [−X; I] y los e_i
    # son independientes por construcción (revisión E2, 7).
    conclusive = (
        not coupled
        and len(basis) == len(vectors) > 0
        and residual <= MECHANISM_RESIDUAL_FACTOR * tol_pivot
        and not any(low <= p <= high for p in participation)
    )
    return MechanismMode(len(basis), participation, conclusive, residual)


def check_singularity(request: PlanarRequest, tol_pivot: float) -> PivotReport:
    """Salvaguarda completa sobre la petición; con singularidad, también el modo (D3)."""
    reduced, labels = reduced_stiffness(request)
    pivot, position, singular = pivot_check(reduced, tol_pivot)
    return PivotReport(
        free_dofs=len(labels),
        min_pivot=pivot,
        dof=None if position is None else labels[position],
        singular=singular,
        labels=tuple(labels),
        mode=mechanism_mode(reduced, tol_pivot) if singular else None,
    )
