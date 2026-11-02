"""Grupo de comandos ``veritas project`` (plan T-012, D11-A; E1).

- ``project demo --out ARCHIVO [--caso viga|portico|uniones]``: guarda un modelo de las demos.
- ``project open ARCHIVO``: abre, valida y resume (esquema, entidades, ``model_revision``, hash
  del modelo e integridad).
- ``project verify ARCHIVO``: solo verifica. Código 0 si el archivo es íntegro y válido; 2 si
  está bien formado como JSON pero es inválido (diagnóstico de dominio); 3 si no supera la
  integridad (alterado o incompleto). ``open`` usa los mismos códigos.

Ningún comando cambia las salidas de los comandos aceptados. El archivo es solo datos.
"""

import argparse
from pathlib import Path
from typing import Final

from veritas.core import units as u
from veritas.core.connections.end import Dof, EndConnection
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.loads.load_case import LoadCase
from veritas.core.messages_es import render_message
from veritas.fem import analyze_planar_xz
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.results.normalized import PlanarResults, normalize_planar_xz
from veritas.fem.results.run import AnalysisRun, RunStatus
from veritas.fem.spatial import analyze_spatial
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.results import SpatialAnalysisRun, SpatialResults, normalize_spatial
from veritas.persistence.errors import IntegrityError, value_error
from veritas.persistence.project import Project, append_runs, open_project, save_project
from veritas.persistence.runs import Validity, make_record, validity

EXIT_OK: Final = 0
EXIT_DIAGNOSTIC: Final = 2
EXIT_INTEGRITY: Final = 3

DEMO_CASES: Final = ("viga", "portico", "uniones")


def add_project_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Registra ``veritas project`` y sus acciones."""
    parser = subparsers.add_parser("project", help="Archivo de proyecto versionado (T-012).")
    sub = parser.add_subparsers(dest="project_command", metavar="ACCIÓN", required=True)
    demo = sub.add_parser("demo", help="Guarda un modelo de demostración como archivo de proyecto.")
    demo.add_argument("--out", required=True, metavar="ARCHIVO", help="Archivo de proyecto.")
    demo.add_argument("--caso", choices=DEMO_CASES, default="viga", help="Modelo (viga).")
    open_parser = sub.add_parser("open", help="Abre un proyecto y resume su contenido.")
    open_parser.add_argument("file", metavar="ARCHIVO")
    verify = sub.add_parser("verify", help="Verifica la integridad y la validez de un proyecto.")
    verify.add_argument("file", metavar="ARCHIVO")
    analyze = sub.add_parser("analyze", help="Analiza un caso del proyecto; --guardar lo archiva.")
    analyze.add_argument("file", metavar="ARCHIVO")
    analyze.add_argument("--carga", metavar="NOMBRE", help="Caso de carga (si hay más de uno).")
    analyze.add_argument("--tipo", choices=("espacial", "plano"), default="espacial")
    analyze.add_argument("--timeout", default="60", help="Tiempo máximo de la corrida en s (60).")
    analyze.add_argument(
        "--guardar", action="store_true", help="Guarda la corrida en el proyecto (inmutable)."
    )
    runs = sub.add_parser("runs", help="Lista las ejecuciones guardadas con su vigencia.")
    runs.add_argument("file", metavar="ARCHIVO")
    move = sub.add_parser("move-node", help="Mueve un nudo (las corridas guardadas quedan STALE).")
    move.add_argument("file", metavar="ARCHIVO")
    move.add_argument("--nodo", required=True, metavar="ETIQUETA", help="Etiqueta del nudo.")
    move.add_argument(
        "--a", required=True, nargs=3, metavar=("X", "Y", "Z"), help="Nueva posición en m."
    )


def _demo_model(case: str) -> StructuralModel:
    from veritas.cli import (  # noqa: PLC0415 (evita ciclo)
        ANALYZE_DEMO_E,
        UNIONS_L,
        UNIONS_SPRING_RATIO,
        _analyze_demo_model,
        _unions_variant,
    )

    if case == "uniones":
        ei = ANALYZE_DEMO_E * 1e6 * 0.2 * 0.4**3 / 12  # Igual que ``analyze uniones``.
        stiffness = u.Quantity(UNIONS_SPRING_RATIO * ei / UNIONS_L, Dof.RY.stiffness_dimension)
        return _unions_variant("Resorte", EndConnection.semi_rigid(Dof.RY, stiffness)).model
    return _analyze_demo_model(case)[0]


def _counts(model: StructuralModel) -> list[str]:
    snapshot = model.snapshot()
    return [
        f"  nudos: {len(snapshot.nodes)} · barras: {len(snapshot.members)} · "
        f"apoyos: {len(snapshot.supports)}",
        f"  materiales (revisiones): {len(snapshot.materials)} · "
        f"secciones (revisiones): {len(snapshot.sections)}",
        f"  casos de carga: {len(snapshot.load_cases)} · cargas: {len(snapshot.loads)}",
        f"  model_revision: {snapshot.model_revision}",
    ]


def _summary(path: str, project: Project) -> str:
    lines = [
        f"Proyecto: {path}",
        f"  esquema v{project.schema_version} · aplicación {project.app_version} · "
        f"codificación {project.canon_version}",
        *_counts(project.model),
        f"  hash del modelo (SHA-256): {project.model_hash}",
        f"  ejecuciones guardadas: {len(project.runs)}",
        f"  integridad del archivo (SHA-256): {project.integrity} — CORRECTA",
        "  perfiles de usuario: " + (", ".join(p.name for p in project.user_profiles) or "ninguno"),
        "Aviso: aplicación experimental; el hash identifica el contenido analítico del modelo, "
        "no valida resultados ni normativa.",
    ]
    return "\n".join(lines)


def run_project(args: argparse.Namespace) -> tuple[str, int]:  # noqa: PLR0911 (un retorno por acción)
    """Ejecuta una acción de ``veritas project``; devuelve (texto, código de salida)."""
    if args.project_command == "demo":
        model = _demo_model(args.caso)
        saved = save_project(args.out, model)
        return (
            "\n".join(
                [
                    f"Proyecto guardado: {args.out} (modelo «{args.caso}»)",
                    *_counts(model),
                    f"  hash del modelo (SHA-256): {saved.model_hash}",
                    f"  integridad del archivo (SHA-256): {saved.integrity}",
                    "Si el archivo ya existía, la versión anterior queda en «.bak».",
                ]
            ),
            EXIT_OK,
        )
    try:
        project = open_project(Path(args.file))
    except IntegrityError as error:
        return f"{error.code}: {render_message(error.diagnostic)}", EXIT_INTEGRITY
    if args.project_command == "analyze":
        return _analyze(args, project)
    if args.project_command == "runs":
        return _runs_table(args.file, project), EXIT_OK
    if args.project_command == "move-node":
        return _move_node(args, project), EXIT_OK
    if args.project_command == "verify":
        return (
            f"Proyecto íntegro y válido: {args.file}\n"
            f"  hash del modelo (SHA-256): {project.model_hash}",
            EXIT_OK,
        )
    return _summary(args.file, project), EXIT_OK


def format_error(error: DiagnosticError) -> str:
    """Texto ``CÓDIGO: mensaje`` de un diagnóstico (frontera de la CLI)."""
    return f"{error.code}: {render_message(error.diagnostic)}"


def _case_of(project: Project, name: str | None) -> LoadCase:
    cases = list(project.model.load_cases.values())
    names = ", ".join(c.name for c in cases) or "ninguno"
    if name is None and len(cases) == 1:
        return cases[0]
    matches = [c for c in cases if c.name == name]
    if len(matches) != 1:
        raise value_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "project.case_required", {"cases": names}
        )
    return matches[0]


def _short(value: object) -> str:
    return str(value)[:8]


def _solve(
    args: argparse.Namespace, model: StructuralModel, case: LoadCase
) -> tuple[AnalysisRun | SpatialAnalysisRun, PlanarResults | SpatialResults | None, str]:
    timeout = float(args.timeout)
    results: PlanarResults | SpatialResults | None = None
    note = ""
    if args.tipo == "plano":
        planar = analyze_planar_xz(model, AnalysisConfiguration(case.id, timeout_s=timeout))
        run: AnalysisRun | SpatialAnalysisRun = planar
    else:
        spatial = analyze_spatial(model, SpatialAnalysisConfiguration(case.id, timeout_s=timeout))
        run = spatial
    if run.status is RunStatus.SUCCEEDED:
        try:
            if isinstance(run, AnalysisRun):
                results = normalize_planar_xz(run, model)
            else:
                results = normalize_spatial(run, model)
        except DiagnosticError as error:
            note = f"  la normalización falló ({format_error(error)}); la corrida no es válida"
    return run, results, note


def _analyze(args: argparse.Namespace, project: Project) -> tuple[str, int]:
    model = project.model
    case = _case_of(project, args.carga)
    run, results, note = _solve(args, model, case)
    record = make_record(model, run, results)
    lines = [
        f"Análisis {args.tipo} del caso «{case.name}» — ejecución {record.run_id}",
        f"  estado: {run.status}" + ("" if record.usable else " (sin resultados válidos)"),
        f"  hash del modelo: {record.model_hash}",
        *(f"  {d.code}: {render_message(d)}" for d in run.diagnostics),
        *([note] if note else []),
    ]
    if results is not None:
        lines.append(f"  resultados: {len(results.nodes)} nudos, {len(results.members)} barras")
    if args.guardar:
        append_runs(args.file, [record])
        lines.append("Ejecución guardada en el proyecto (inmutable; el archivo previo en «.bak»).")
    return "\n".join(lines), EXIT_OK if record.usable else EXIT_INTEGRITY


def _runs_table(path: str, project: Project) -> str:
    if not project.runs:
        return f"Proyecto: {path}\n  sin ejecuciones guardadas"
    lines = [f"Proyecto: {path} — {len(project.runs)} ejecución(es)"]
    for record in project.runs:
        case = project.model.load_cases.get(record.run.case_id)
        state = validity(record, project.model)
        usable = "" if record.usable else " · SIN RESULTADOS VÁLIDOS"
        lines.append(
            f"  {_short(record.run_id)} {record.kind} {record.run.status} "
            f"caso «{case.name if case else _short(record.run.case_id)}» "
            f"rev.modelo {record.run.model_revision} hash {record.model_hash[:12]} "
            f"{state}{usable}"
        )
    lines.append("CURRENT: el modelo actual tiene el mismo hash; STALE: el modelo cambió.")
    return "\n".join(lines)


def _move_node(args: argparse.Namespace, project: Project) -> str:
    model = project.model
    matches = [n for n in model.nodes.values() if n.label == args.nodo]
    if len(matches) != 1:
        raise value_error(
            DiagnosticCode.REFERENCE_NOT_FOUND,
            "model.reference_not_found",
            {"kind": "nudo", "entity_id": f"{args.nodo} ({len(matches)} coincidencias)"},
        )
    x, y, z = (u.Quantity.of(float(v), "m") for v in args.a)
    model.move_node(matches[0].id, x, y, z)
    saved = save_project(args.file, model, project.user_profiles, runs=project.runs)
    states = [validity(r, model) for r in saved.runs]
    return "\n".join(
        [
            f"Nudo «{args.nodo}» movido a ({', '.join(args.a)}) m; proyecto guardado.",
            f"  hash del modelo: {saved.model_hash}",
            f"  ejecuciones: {states.count(Validity.CURRENT)} CURRENT, "
            f"{states.count(Validity.STALE)} STALE",
        ]
    )
