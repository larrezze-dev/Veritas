"""Worker OpenSeesPy espacial de seis GDL por nudo (ADR-003, plan T-018, D2-D4).

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
PROTOCOL_VERSION = 1


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
    }


def _build(ops: Any, request: dict[str, Any]) -> None:
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    coordinates: dict[int, tuple[float, float, float]] = {}
    for tag, _entity, x, y, z in request["nodes"]:
        ops.node(tag, x, y, z)
        coordinates[tag] = (x, y, z)
    for node, fixity in request["fixes"]:
        ops.fix(node, *fixity)
    for tag, _entity, node_i, node_j, area, e, g, j, iy, iz, vecxz in request["beams"]:
        # Sin ``-release`` en E1, transformación Linear con vecxz = ez de ADR-001 (hecho 1) y
        # beamUniform (Wy, Wz, Wx) (hecho 9): el padre contrasta localForce con k·T·u − F_eq.
        ops.geomTransf("Linear", tag, *vecxz)
        ops.element("elasticBeamColumn", tag, node_i, node_j, area, e, g, j, iy, iz, tag)
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


def _solve(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("protocol") != PROTOCOL_ID or request.get("version") != PROTOCOL_VERSION:
        return _response("PROTOCOL_ERROR", "protocolo o versión no admitidos")
    # Solo tras validar la petición. OpenSeesPy no publica tipos.
    import openseespy.opensees as ops  # type: ignore[import-untyped]  # noqa: PLC0415

    try:
        _build(ops, request)
        code = int(ops.analyze(1))
        if code != 0:
            return _response("ANALYZE_FAILED", "analyze() distinto de 0", code)
        ops.reactions()
        response = _response("OK", "", code)
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
    except Exception as error:  # Todo error de OpenSees es un fallo de la corrida.
        return _response("SOLVER_ERROR", f"{type(error).__name__}: {error}")
    finally:
        ops.wipe()
    return response


def main(argv: list[str]) -> int:
    """Punto de entrada del worker."""
    if len(argv) != 2:  # noqa: PLR2004 (programa y ruta de respuesta)
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
