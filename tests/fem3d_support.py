"""Referencia exacta de las pruebas espaciales: resolución de la K reducida de
``fem.spatial.stiffness`` (plan T-018, pieza 4; DEV-1).

Usa la misma LDLᵀ con escalado de Jacobi y pivotaje diagonal completo que la salvaguarda
(``_factor``) y añade solo la sustitución, para que la referencia no sea peor condicionada
que el worker.
"""

from collections.abc import Sequence

from veritas.fem.adapters.singularity import _factor
from veritas.fem.spatial.protocol import DOFS_PER_NODE, Six, SpatialRequest
from veritas.fem.spatial.stiffness import load_vector, reduced_coordinates, reduced_stiffness


def solve_symmetric(matrix: list[list[float]], rhs: Sequence[float]) -> list[float]:
    """x de K·x = b con K simétrica definida positiva (``_factor`` completa)."""
    factor = _factor(matrix, 0.0)
    assert not factor.singular
    assert factor.steps == len(matrix)
    size, u, order = len(matrix), factor.work, factor.order
    scale = [1.0 / matrix[i][i] ** 0.5 for i in range(size)]
    b = [rhs[order[i]] * scale[order[i]] for i in range(size)]
    # Ŝ = Uᵀ·D⁻¹·U con D = diag(U): Uᵀ·y = b̂ y después U·z = D·y.
    y = [0.0] * size
    for i in range(size):
        y[i] = (b[i] - sum(u[k][i] * y[k] for k in range(i))) / u[i][i]
    z = [0.0] * size
    for i in range(size - 1, -1, -1):
        z[i] = (u[i][i] * y[i] - sum(u[i][j] * z[j] for j in range(i + 1, size))) / u[i][i]
    x = [0.0] * size
    for i in range(size):
        x[order[i]] = z[i] * scale[order[i]]
    return x


def reference_displacements(request: SpatialRequest) -> dict[int, Six]:
    """Desplazamientos globales exactos de los nudos de dominio (tag → seis valores)."""
    reduced, _ = reduced_stiffness(request)
    coordinates = reduced_coordinates(request)
    force = load_vector(request)
    rhs = [sum(t * force[i] for i, t in column.items()) for _, column in coordinates]
    solution = solve_symmetric(reduced, rhs)
    full = [0.0] * len(force)
    for value, (_, column) in zip(solution, coordinates, strict=True):
        for i, t in column.items():
            full[i] += t * value
    result: dict[int, Six] = {}
    for position, node in enumerate(request.nodes):
        a, b, c, d, e, f = full[DOFS_PER_NODE * position : DOFS_PER_NODE * (position + 1)]
        result[node.tag] = (a, b, c, d, e, f)
    return result
