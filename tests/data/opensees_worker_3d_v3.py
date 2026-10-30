"""COPIA CONGELADA del worker 3D v3 (commit 98ce1d2), solo para las pruebas: la igualdad
bit a bit v3 ↔ v4 sin asientos de ``test_fem3d_settlements`` (plan T-016, E2-a). No se usa
en producción ni se modifica (solo se retiran dos ``noqa`` que ruff no admite en ``tests/``).

Worker OpenSeesPy espacial de seis GDL por nudo (ADR-003, plan T-018, D2-D4).

Se ejecuta en un proceso nuevo por corrida: ``python -I opensees_worker_3d.py RESPUESTA``,
con el mismo runner que el worker plano. Lee la petición JSON por stdin y escribe la
respuesta JSON en RESPUESTA. Solo usa la biblioteca estándar y OpenSeesPy; no importa
``veritas`` ni ejecuta código procedente del modelo. Interpreta la petición literalmente:
toda decisión de traducción la toma el traductor del proceso padre. Los avisos de OpenSees
salen por stderr, que el runner captura.

Códigos de salida: 0 = respuesta escrita (el estado del análisis va en la respuesta);
2 = uso incorrecto. Cualquier otra excepción termina con código distinto de 0.
"""

import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

PROTOCOL_ID = "veritas.opensees.spatial"
PROTOCOL_VERSION = 3
# Patrón corrector de la penalización iterada (ADR-004 §1): serie y patrón propios.
CORRECTOR_TAG = 2
MIN_STAGNATION_ITERATION = 3  # Estancamiento aceptado desde la iteración 3 (ADR-004 §3).
STAGNATION_IMPROVEMENT = 10.0  # Mejora mínima del paso frente al mejor anterior.


def _version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _response(status: str, message: str = "", analyze_code: int | None = None) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_ID,
        "version": PROTOCOL_VERSION,
        "status": status,
        "analyze_code": analyze_code,
        "message": message,
        "versions": {
            "python": platform.python_version(),
            "openseespy": _version("openseespy"),
            "openseespywin": _version("openseespywin"),
        },
        "nodes": [],
        "grounds": [],
        "chains": [],
        "beams": [],
        "aux": [],
        "end_springs": [],
        "end_penalty_iterations": 0,
        "end_penalty_stop": "none",
        "end_penalty_converged": True,
    }


def _fixes(ops: Any, request: dict[str, Any]) -> None:
    """Un único fix por nudo: el giro huérfano (plan T-018, R4) se fusiona con el del apoyo."""
    fixity_by_node: dict[int, list[int]] = {node: list(f) for node, f in request["fixes"]}
    for node, rotations in request["orphan_fixes"]:
        fixity = fixity_by_node.setdefault(node, [0] * 6)
        fixity[3:] = [max(a, b) for a, b in zip(fixity[3:], rotations, strict=True)]
    for node, fixity in fixity_by_node.items():
        ops.fix(node, *fixity)


def _beams_and_ends(
    ops: Any, request: dict[str, Any], coordinates: dict[int, tuple[float, float, float]]
) -> None:
    """Barras y extremos no rígidos (E2-a): aux en el maestro, sin fix; el elemento se conecta
    a él y lo unen al maestro ``equalDOF`` y ``zeroLength`` de extremo."""
    element_node: dict[tuple[int, str], int] = {}
    master_of: dict[int, int] = {}
    for aux, master, beam, end in request["aux_nodes"]:
        ops.node(aux, *coordinates[master])
        element_node[(beam, end)] = aux
        master_of[aux] = master
    for beam in request["beams"]:
        tag, _entity, node_i, node_j, area, e, g, j, iy, iz, vecxz = beam[:11]
        release_y, release_z = beam[11], beam[12]
        # Transformación Linear con vecxz = ez de ADR-001 (hecho 1) y beamUniform (Wy, Wz, Wx)
        # (hecho 9). A y J llegan efectivos (hechos 5 y 15); -releasey/-releasez solo si el
        # código no es 0 (hecho 14). El padre contrasta localForce con k·T·u − F_eq condensados.
        ops.geomTransf("Linear", tag, *vecxz)
        start = element_node.get((tag, "I"), node_i)
        stop = element_node.get((tag, "J"), node_j)
        releases: list[Any] = []
        if release_y:
            releases += ["-releasey", release_y]
        if release_z:
            releases += ["-releasez", release_z]
        ops.element("elasticBeamColumn", tag, start, stop, area, e, g, j, iy, iz, tag, *releases)
    for aux, dofs in request["end_equals"]:
        ops.equalDOF(master_of[aux], aux, *dofs)
    for tag, aux, dof, stiffness, axis_x, axis_yp, _penalty in request["end_springs"]:
        ops.uniaxialMaterial("Elastic", tag, stiffness)
        ops.element(
            "zeroLength",
            tag,
            master_of[aux],
            aux,
            "-mat",
            tag,
            "-dir",
            dof,
            "-orient",
            *axis_x,
            *axis_yp,
        )


def _build(ops: Any, request: dict[str, Any]) -> None:
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    coordinates: dict[int, tuple[float, float, float]] = {}
    for tag, _entity, x, y, z in request["nodes"]:
        ops.node(tag, x, y, z)
        coordinates[tag] = (x, y, z)
    _fixes(ops, request)
    _beams_and_ends(ops, request, coordinates)
    grounds: set[int] = set()
    for tag, _support, node, ground, dof, stiffness, axis_x, axis_yp, _pen in request["springs"]:
        if ground not in grounds:
            ops.node(ground, *coordinates[node])
            ops.fix(ground, 1, 1, 1, 1, 1, 1)
            grounds.add(ground)
        ops.uniaxialMaterial("Elastic", tag, stiffness)
        ops.element(
            "zeroLength", tag, ground, node, "-mat", tag, "-dir", dof, "-orient", *axis_x, *axis_yp
        )
    for chain in request["chains"]:
        tag, _support, node, c_node, r_node, ground, normal, tangent, length, stiffness = chain
        # Rodillo exacto (hechos 6, 8 y 11): u_N = length·θ_R × n, luego n·u_N = 0.
        x, y, z = coordinates[node]
        r_point = (x - length * normal[0], y - length * normal[1], z - length * normal[2])
        ops.node(r_node, *r_point)
        ops.fix(r_node, 1, 1, 1, 0, 0, 0)
        ops.node(c_node, x, y, z)
        ops.rigidLink("beam", r_node, c_node)
        ops.equalDOF(c_node, node, 1, 2, 3)
        # El giro de R alrededor de n es un modo desacoplado: lo fija un resorte.
        ops.node(ground, *r_point)
        ops.fix(ground, 1, 1, 1, 1, 1, 1)
        ops.uniaxialMaterial("Elastic", tag, stiffness)
        ops.element(
            "zeroLength", tag, ground, r_node, "-mat", tag, "-dir", 4, "-orient", *normal, *tangent
        )
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for node, values in request["nodal_loads"]:
        ops.load(node, *values)
    for beam, wy, wz, wx in request["beam_loads"]:
        ops.eleLoad("-ele", beam, "-type", "-beamUniform", wy, wz, wx)
    # Lagrange en todas las corridas (D3-A): Transformation falla en silencio con las
    # restricciones encadenadas de la cadena (hecho 6). Alphas escalados (hecho 13).
    ops.constraints("Lagrange", request["alpha_sp"], request["alpha_mp"])
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.algorithm("Linear")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")


def _step(force: float, scale: float) -> float:
    """p_s = |k_s·g_s| / S_s; con S_s = 0, 0 si la fuerza es 0 e ∞ si no (ADR-004 §3)."""
    if scale > 0.0:
        return abs(force) / scale
    return 0.0 if force == 0.0 else float("inf")


def _iterate(ops: Any, request: dict[str, Any]) -> tuple[int, int, str, dict[int, float]]:
    """Penalización iterada (Uzawa) sobre los resortes de penalización de extremo (ADR-004).

    En la iteración i se reconstruye el modelo (``_build`` hace ``wipe``) con un patrón
    ``Constant`` corrector: −λ_s·b_s en el aux y +λ_s·b_s en el maestro (fuerzas o momentos
    según la terna), y tras resolver λ_s ← λ_s + k_s·g_s. λ⁽¹⁾ = 0 es la penalización simple.
    Devuelve el código de ``analyze``, las iteraciones, el motivo de parada y los λ de la
    última resolución; el modelo de esa resolución queda construido para leer resultados.
    """
    springs = {s[0]: s for s in request["end_springs"]}
    master_of = {aux: master for aux, master, _beam, _end in request["aux_nodes"]}
    penalties = [
        (tag, springs[tag][1], springs[tag][2], springs[tag][3], direction, scale)
        for tag, direction, scale in request["end_penalties"]
    ]
    multipliers = {tag: 0.0 for tag, *_rest in penalties}
    best = float("inf")
    for iteration in range(1, request["end_penalty_max_iterations"] + 1):
        _build(ops, request)
        ops.timeSeries("Constant", CORRECTOR_TAG)
        ops.pattern("Plain", CORRECTOR_TAG, CORRECTOR_TAG)
        for tag, aux, dof, _k, direction, _scale in penalties:
            offset = 0 if dof <= 3 else 3  # GDL 1-3 traslaciones
            values = [0.0] * 6
            for i in range(3):
                values[offset + i] = multipliers[tag] * direction[i]
            ops.load(aux, *[-v for v in values])
            ops.load(master_of[aux], *values)
        code = int(ops.analyze(1))
        if code != 0:
            return code, iteration, "none", multipliers
        forces = {
            tag: stiffness * ops.eleResponse(tag, "basicDeformation")[0]
            for tag, _aux, _dof, stiffness, _d, _s in penalties
        }
        step = max(_step(forces[tag], scale) for tag, *_rest, scale in penalties)
        if step <= request["end_penalty_tolerance"]:
            return code, iteration, "tolerance", multipliers
        if (
            iteration >= MIN_STAGNATION_ITERATION
            and step <= request["end_penalty_stagnation"]
            and step > best / STAGNATION_IMPROVEMENT
        ):
            return code, iteration, "stagnation", multipliers
        if iteration == request["end_penalty_max_iterations"]:
            return code, iteration, "max_iterations", multipliers
        best = min(best, step)
        multipliers = {tag: multipliers[tag] + forces[tag] for tag in multipliers}
    raise AssertionError("inalcanzable: end_penalty_max_iterations ≥ 1")


def _solve(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("protocol") != PROTOCOL_ID or request.get("version") != PROTOCOL_VERSION:
        return _response("PROTOCOL_ERROR", "protocolo o versión no admitidos")
    # Solo tras validar la petición. OpenSeesPy no publica tipos.
    import openseespy.opensees as ops  # type: ignore[import-untyped]  # noqa: PLC0415

    try:
        iterations, stop = 0, "none"
        multipliers: dict[int, float] = {}
        if request["end_penalties"]:
            code, iterations, stop, multipliers = _iterate(ops, request)
        else:  # Sin penalización de extremo: una sola resolución, sin patrón corrector.
            _build(ops, request)
            code = int(ops.analyze(1))
        if code != 0:
            return _response("ANALYZE_FAILED", "analyze() distinto de 0", code)
        ops.reactions()
        response = _response("OK", "", code)
        response["end_penalty_iterations"] = iterations
        response["end_penalty_stop"] = stop
        response["end_penalty_converged"] = stop != "max_iterations"
        response["nodes"] = [
            [tag, list(ops.nodeDisp(tag)), list(ops.nodeReaction(tag))]
            for tag, *_rest in request["nodes"]
        ]
        grounds = sorted({spring[3] for spring in request["springs"]})
        response["grounds"] = [[tag, list(ops.nodeReaction(tag))] for tag in grounds]
        response["chains"] = [
            [
                chain[0],
                list(ops.nodeDisp(chain[3])),
                list(ops.nodeDisp(chain[4])),
                list(ops.nodeReaction(chain[4])),
            ]
            for chain in request["chains"]
        ]
        response["beams"] = [
            [beam[0], list(ops.eleResponse(beam[0], "localForce"))] for beam in request["beams"]
        ]
        response["aux"] = [
            [aux[0], list(ops.nodeDisp(aux[0])), list(ops.nodeReaction(aux[0]))]
            for aux in request["aux_nodes"]
        ]
        response["end_springs"] = [
            [
                spring[0],
                ops.eleResponse(spring[0], "basicForce")[0],
                ops.eleResponse(spring[0], "basicDeformation")[0],
                multipliers.get(spring[0], 0.0),
            ]
            for spring in request["end_springs"]
        ]
    except Exception as error:  # Todo error de OpenSees es un fallo de la corrida.
        return _response("SOLVER_ERROR", f"{type(error).__name__}: {error}")
    finally:
        ops.wipe()
    return response


def main(argv: list[str]) -> int:
    """Punto de entrada del worker."""
    if len(argv) != 2:
        print("uso: opensees_worker_3d.py RUTA_RESPUESTA", file=sys.stderr)
        return 2
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError as error:
        response = _response("PROTOCOL_ERROR", f"JSON no válido: {error.msg}")
    else:
        response = (
            _solve(request)
            if isinstance(request, dict)
            else _response("PROTOCOL_ERROR", "la petición no es un objeto")
        )
    # allow_nan: un resultado no finito llega tal cual y el padre lo rechaza.
    with open(argv[1], "w", encoding="utf-8") as stream:
        json.dump(response, stream, allow_nan=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
