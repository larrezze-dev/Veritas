"""Worker OpenSeesPy plano (ADR-002 §4-§5, plan T-007).

Se ejecuta en un proceso nuevo por corrida: ``python -I opensees_worker.py RESPUESTA``.
Lee la petición JSON por stdin y escribe la respuesta JSON en RESPUESTA (ruta del
directorio temporal del padre). Solo usa la biblioteca estándar y OpenSeesPy; no importa
``veritas`` ni ejecuta código procedente del modelo. Interpreta la petición literalmente:
toda decisión de traducción la toma el traductor del proceso padre.

Códigos de salida: 0 = respuesta escrita (el estado del análisis va en la respuesta);
2 = uso incorrecto. Cualquier otra excepción termina con código distinto de 0.
"""

import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

PROTOCOL_ID = "veritas.opensees.planar"
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
        "aux": [],
        "beams": [],
    }


def _build(ops: Any, request: dict[str, Any]) -> None:
    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    coordinates: dict[int, tuple[float, float]] = {}
    for tag, _entity, x1, x2 in request["nodes"]:
        ops.node(tag, x1, x2)
        coordinates[tag] = (x1, x2)
    for node, fixity in request["fixes"]:
        ops.fix(node, *fixity)
    ops.geomTransf("Linear", 1)
    for tag, _entity, node_i, node_j, area, modulus, inertia, _axis in request["beams"]:
        ops.element("elasticBeamColumn", tag, node_i, node_j, area, modulus, inertia, 1)
    for tag, _support, node, aux, dof, stiffness, axis1, axis2, _penalty in request["springs"]:
        ops.node(aux, *coordinates[node])
        ops.fix(aux, 1, 1, 1)
        ops.uniaxialMaterial("Elastic", tag, stiffness)
        ops.element(
            "zeroLength",
            tag,
            aux,
            node,
            "-mat",
            tag,
            "-dir",
            dof,
            "-orient",
            axis1[0],
            axis1[1],
            0.0,
            axis2[0],
            axis2[1],
            0.0,
        )
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for node, p1, p2, moment in request["nodal_loads"]:
        ops.load(node, p1, p2, moment)
    for beam, wy, wx in request["beam_loads"]:
        ops.eleLoad("-ele", beam, "-type", "-beamUniform", wy, wx)
    ops.constraints("Transformation")
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
            for tag, _entity, _x1, _x2 in request["nodes"]
        ]
        response["aux"] = [
            [spring[3], list(ops.nodeReaction(spring[3]))] for spring in request["springs"]
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
        print("uso: opensees_worker.py RUTA_RESPUESTA", file=sys.stderr)
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
