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
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.messages_es import render_message
from veritas.persistence.errors import IntegrityError
from veritas.persistence.project import Project, open_project, save_project

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
        f"  integridad del archivo (SHA-256): {project.integrity} — CORRECTA",
        "  perfiles de usuario: " + (", ".join(p.name for p in project.user_profiles) or "ninguno"),
        "Aviso: aplicación experimental; el hash identifica el contenido analítico del modelo, "
        "no valida resultados ni normativa.",
    ]
    return "\n".join(lines)


def run_project(args: argparse.Namespace) -> tuple[str, int]:
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
