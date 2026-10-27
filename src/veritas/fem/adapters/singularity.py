"""Salvaguarda de singularidad previa al solver (plan T-007, D3).

Ensamblaje independiente, en Python puro, de la rigidez reducida de la MISMA petición
que se envía al worker: barras Euler–Bernoulli 2D, resortes de apoyo y restricciones
rígidas exactas. Una traslación oblicua rígida (resorte de penalización en la petición)
se aplica aquí de forma exacta: los GDL del nudo se giran a (tangente, normal) y se
elimina la normal. Después: escalado de Jacobi y LDLᵀ con pivotaje diagonal completo.

La garantía es condicionada a ``tol_pivot`` y a la batería de pruebas declarada; no es
una demostración general (revisión B1). No localiza mecanismos (T-010).
"""

import math
import sys
from dataclasses import dataclass
from typing import Final

from veritas.fem.adapters.protocol import PlanarRequest

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


def beam_stiffness(
    xi: tuple[float, float], xj: tuple[float, float], area: float, modulus: float, inertia: float
) -> Matrix:
    dx, dz = xj[0] - xi[0], xj[1] - xi[1]
    length = math.hypot(dx, dz)
    c, s = dx / length, dz / length
    ea, ei = modulus * area / length, modulus * inertia
    a, b, d, e = 12 * ei / length**3, 6 * ei / length**2, 4 * ei / length, 2 * ei / length
    local = [
        [ea, 0.0, 0.0, -ea, 0.0, 0.0],
        [0.0, a, b, 0.0, -a, b],
        [0.0, b, d, 0.0, -b, e],
        [-ea, 0.0, 0.0, ea, 0.0, 0.0],
        [0.0, -a, -b, 0.0, a, -b],
        [0.0, b, e, 0.0, -b, d],
    ]
    rotation = [[0.0] * 6 for _ in range(6)]
    for block in (0, 3):
        rotation[block][block], rotation[block][block + 1] = c, s
        rotation[block + 1][block], rotation[block + 1][block + 1] = -s, c
        rotation[block + 2][block + 2] = 1.0
    # K_global = Tᵀ k T
    kt = [[sum(local[i][m] * rotation[m][j] for m in range(6)) for j in range(6)] for i in range(6)]
    return [[sum(rotation[m][i] * kt[m][j] for m in range(6)) for j in range(6)] for i in range(6)]


def full_stiffness(request: PlanarRequest) -> tuple[Matrix, dict[int, int]]:
    """K completa (3 GDL por nudo) sin penalizaciones ni restricciones; mapa tag → índice."""
    index = {node.tag: position for position, node in enumerate(request.nodes)}
    coordinates = {node.tag: (node.x1, node.x2) for node in request.nodes}
    size = 3 * len(request.nodes)
    stiffness = [[0.0] * size for _ in range(size)]
    for beam in request.beams:
        element = beam_stiffness(
            coordinates[beam.node_i],
            coordinates[beam.node_j],
            beam.area,
            beam.elastic_modulus,
            beam.inertia,
        )
        dofs = [3 * index[beam.node_i] + k for k in range(3)]
        dofs += [3 * index[beam.node_j] + k for k in range(3)]
        for i, row in enumerate(dofs):
            for j, column in enumerate(dofs):
                stiffness[row][column] += element[i][j]
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
    size = len(matrix)
    if size == 0:
        return None, None, False
    diagonal = [matrix[i][i] for i in range(size)]
    if any(not math.isfinite(value) or value < 0.0 for value in diagonal):
        raise InvalidStiffnessError("diagonal de rigidez negativa o no finita")
    smallest = min(range(size), key=lambda i: diagonal[i])
    if diagonal[smallest] < sys.float_info.min:
        return 0.0, smallest, True
    scale = [1.0 / math.sqrt(value) for value in diagonal]
    work = [[matrix[i][j] * scale[i] * scale[j] for j in range(size)] for i in range(size)]
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
            return pivot, order[k], True
        row_k = work[k]
        for i in range(k + 1, size):
            factor = work[i][k] / pivot
            if factor != 0.0:
                row_i = work[i]
                row_i[k + 1 :] = [
                    a - factor * b for a, b in zip(row_i[k + 1 :], row_k[k + 1 :], strict=True)
                ]
    return min_pivot, min_index, False


def check_singularity(request: PlanarRequest, tol_pivot: float) -> PivotReport:
    """Salvaguarda completa sobre la petición."""
    reduced, labels = reduced_stiffness(request)
    pivot, position, singular = pivot_check(reduced, tol_pivot)
    return PivotReport(
        free_dofs=len(labels),
        min_pivot=pivot,
        dof=None if position is None else labels[position],
        singular=singular,
    )
