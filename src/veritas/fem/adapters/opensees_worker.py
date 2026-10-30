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
PROTOCOL_VERSION = 3


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
        "end_aux": [],
        "beams": [],
    }


def _fix(ops: Any, request: dict[str, Any]) -> None:
    # Un único fix por nudo: el giro huérfano (T-010, D3) se fusiona con el del apoyo.
    fixity_by_node: dict[int, list[int]] = {node: list(f) for node, f in request["fixes"]}
    for node in request["orphan_fixes"]:
        fixity_by_node.setdefault(node, [0, 0, 0])[2] = 1
    # Asiento (T-016): el GDL sale del fix (fix y sp chocan, hecho 5) y lleva solo el sp.
    for node, dof, _value in request["settlements"]:
        fixity_by_node[node][dof - 1] = 0
    for node, fixity in fixity_by_node.items():
        if any(fixity):
            ops.fix(node, *fixity)


def _build(ops: Any, request: dict[str, Any]) -> None:
    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    coordinates: dict[int, tuple[float, float]] = {}
    for tag, _entity, x1, x2 in request["nodes"]:
        ops.node(tag, x1, x2)
        coordinates[tag] = (x1, x2)
    _fix(ops, request)
    # Extremos no rígidos (T-010, D2): aux en el nudo, equalDOF en u1, u2 y giro propio.
    element_node: dict[tuple[int, str], int] = {}
    for _tag, _member, beam, end, master, aux, _stiffness in request["end_links"]:
        ops.node(aux, *coordinates[master])
        ops.equalDOF(master, aux, 1, 2)
        element_node[(beam, end)] = aux
    ops.geomTransf("Linear", 1)
    for tag, _entity, node_i, node_j, area, modulus, inertia, _axis in request["beams"]:
        start = element_node.get((tag, "I"), node_i)
        stop = element_node.get((tag, "J"), node_j)
        # Sin ``-release``, transformación Linear y beamUniform (Wy, Wx): el padre comprueba
        # localForce = k·T·u − F_eq (T-017, D1). Cambiarlo exige nueva versión de protocolo.
        ops.element("elasticBeamColumn", tag, start, stop, area, modulus, inertia, 1)
    for tag, _member, _beam, _end, master, aux, stiffness in request["end_links"]:
        if stiffness is not None:
            ops.uniaxialMaterial("Elastic", tag, stiffness)
            ops.element("zeroLength", tag, master, aux, "-mat", tag, "-dir", 3)
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
    # sp dentro del patrón 1 (hecho 6): valor prescrito con factor 1 en el único paso.
    for node, dof, value in request["settlements"]:
        ops.sp(node, dof, value)
    # Transformation hace que nodeDisp(aux)[0:2] sea igual bit a bit al maestro: el control
    # de alias del padre (T-017, D1) depende de ello. Cambiarlo exige nueva versión de protocolo.
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.algorithm("Linear")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")


def _solve(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("protocol") != PROTOCOL_ID or request.get("version") != PROTOCOL_VERSION:
        return _response("PROTOCOL_ERROR", "protocolo o versión no admitidos")
    fixed = {(node, dof) for node, f in request["fixes"] for dof in (1, 2, 3) if f[dof - 1]}
    if any((node, dof) not in fixed for node, dof, _value in request["settlements"]):
        return _response("PROTOCOL_ERROR", "asiento fuera del fix del nudo")
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
        response["end_aux"] = [
            [link[5], list(ops.nodeDisp(link[5])), list(ops.nodeReaction(link[5]))]
            for link in request["end_links"]
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
