"""Sesión aceptada de ``veritas catalog`` normalizada para compararla entre versiones (T-022 E2).

Una sesión completa en un directorio temporal con rutas relativas: crear, simular, importar,
reimportar, ``--estricto``, listar, mostrar, proteger, verificar y los fallos (todos con su
código de salida). Se enmascara solo lo que cambia entre ejecuciones sin cambiar la salida:
los UUID (v4 aleatorios), la fecha UTC del lote y los SHA-256, que dependen de los bytes del
archivo de origen (CRLF o LF según ``core.autocrlf``). No se usa ``cli_golden.normalize``: ese
enmascara también los números, y aquí los recuentos son parte de lo que se acepta.

Regenerar solo con una salida aceptada (ver ``tests/golden/README.md``):

    uv run python tests/catalog_cli_golden.py            # ambas sesiones
    uv run python tests/catalog_cli_golden.py consulta   # solo la de consulta (T-023 E1)
    uv run python tests/catalog_cli_golden.py gestion    # solo la de gestión (T-023 E2)

La sesión de consulta (``search``, ``ficha`` y ``comparar``; T-023 E1) vive en su propio
archivo para no tocar la salida ya aceptada de T-022; la de gestión (``clone``, ``favorito`` y
``exportar``; T-023 E2) también.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

from veritas import cli

DATA_DIR: Final = Path(__file__).parent / "data"
GOLDEN_FILE: Final = Path(__file__).parent / "golden" / "catalogo_cli.txt"
QUERY_GOLDEN_FILE: Final = Path(__file__).parent / "golden" / "catalogo_consulta.txt"
MANAGE_GOLDEN_FILE: Final = Path(__file__).parent / "golden" / "catalogo_gestion.txt"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA = re.compile(r"\b[0-9a-f]{64}\b")
_SHA_PREFIX = re.compile(r"\b[0-9a-f]{12}\b")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

MAPPING: Final = "catalog_mapping_example.json"
SYNTHETIC_ARGS: Final = ["--mapeo", MAPPING, "--sep", ";", "--decimal", ","]

NATIVE_JSON: Final = [
    {
        "series": "N",
        "designation": "A-1",
        "family": "I",
        "area": 0.003,
        "iy": 2e-6,
        "iz": 3e-6,
        "linear_mass": 23.55,
    },
    {"series": "N", "designation": "A-2", "family": "I", "area": 0.003, "iy": 4e-6, "iz": 3e-6},
    {"series": "N", "designation": "A-3", "family": "I", "area": True, "iy": 4e-6, "iz": 3e-6},
    {"series": "N", "designation": "A-2", "family": "SHS", "area": 0.004, "iy": 5e-6, "iz": 5e-6},
]

QUERY_NATIVE: Final = [
    {"series": "N", "designation": "A-1", "family": "I", "area": 0.003, "iy": 2e-6, "iz": 3e-6,
     "linear_mass": 23.55, "dim_h": 0.2},
    {"series": "N", "designation": "A-2", "family": "SHS", "area": 0.004, "iy": 5e-6, "iz": 5e-6},
    {"series": "N", "designation": "A-3", "family": "I", "area": 0.0035, "iy": 4e-6, "iz": 3e-6,
     "linear_mass": 27.475, "dim_h": 0.25},
]  # fmt: skip
"""Catálogo nativo de la sesión de consulta: con y sin masa, con y sin dimensión h."""


def normalize(text: str) -> str:
    text = _UUID.sub("<uuid>", text)
    text = _SHA.sub("<sha256>", text)
    text = _SHA_PREFIX.sub("<sha12>", text)
    return _UTC.sub("<utc>", text)


def _run(argv: list[str]) -> str:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["catalog", *argv])
    return (
        f"$ veritas catalog {' '.join(argv)}\n"
        f"--- stdout\n{normalize(out.getvalue())}"
        f"--- stderr\n{normalize(err.getvalue())}"
        f"--- exit {code}\n\n"
    )


def _prepare(work: Path) -> None:
    for name in ("catalog_synthetic_100.csv", "catalog_synthetic_bad.csv", MAPPING):
        shutil.copy(DATA_DIR / name, work / name)
    bad = (work / "catalog_synthetic_bad.csv").read_text(encoding="utf-8-sig").splitlines()
    good = (work / "catalog_synthetic_100.csv").read_text(encoding="utf-8-sig").splitlines()
    both = "\n".join([*good, *bad[1:]]) + "\n"
    (work / "lote105.csv").write_text(both, encoding="utf-8-sig", newline="")
    (work / "nativo.json").write_text(json.dumps(NATIVE_JSON, indent=1), encoding="utf-8")
    (work / "catalog_synthetic_100.csv").unlink()


def session() -> str:
    """Salida normalizada de la sesión, ejecutada en el directorio actual (vacío)."""
    work = Path.cwd()
    _prepare(work)
    synthetic = ["--db", "c.sqlite", "--catalogo", "Sintetico", "lote105.csv", *SYNTHETIC_ARGS]
    steps: list[list[str]] = [
        [
            "create",
            "--db",
            "c.sqlite",
            "--nombre",
            "Sintetico",
            "--fuente",
            "SINTETICO",
            "--licencia",
            "Sin valor comercial",
            "--edicion",
            "2026",
        ],
        ["import", *synthetic, "--simular"],
        ["import", *synthetic],
        ["import", *synthetic],
        ["import", *synthetic, "--estricto"],
        [
            "create",
            "--db",
            "c.sqlite",
            "--nombre",
            "Nativo",
            "--fuente",
            "SINTETICO",
            "--licencia",
            "Sin valor comercial",
            "--edicion",
            "2026",
        ],
        ["import", "--db", "c.sqlite", "--catalogo", "Nativo", "nativo.json"],
        ["list", "--db", "c.sqlite"],
        ["list", "--db", "c.sqlite", "--catalogo", "Nativo"],
        ["list", "--db", "c.sqlite", "--catalogo", "Sintetico", "--lotes"],
        ["show", "--db", "c.sqlite", "--catalogo", "Sintetico", "SINTETICO-I", "SINT-100x50-4-6"],
        ["show", "--db", "c.sqlite", "--catalogo", "Nativo", "N", "A-1"],
        ["lock", "--db", "c.sqlite", "--catalogo", "Sintetico"],
        ["import", *synthetic],
        ["import", "--db", "c.sqlite", "--catalogo", "Nativo", "falta.csv"],
        ["import", "--db", "c.sqlite", "--catalogo", "Nativo", "nativo.json", "--decimal", ","],
        ["verify", "--db", "c.sqlite"],
    ]
    log = [_run(step) for step in steps]
    shutil.copy("c.sqlite", "t.sqlite")
    connection = sqlite3.connect("t.sqlite")
    connection.execute(
        "UPDATE profile_revisions SET content_sha256 = 'x' WHERE designation = 'A-1'"
    )
    connection.execute(
        "UPDATE import_batches SET created = created + 1 WHERE file_name = 'nativo.json'"
    )
    connection.commit()
    connection.close()
    log.append(_run(["verify", "--db", "t.sqlite"]))
    return "".join(log)


def query_session() -> str:
    """Sesión de consulta (T-023 E1) sobre dos catálogos sintéticos (preparación sin registrar)."""
    work = Path.cwd()
    _prepare(work)
    db = ["--db", "c.sqlite"]
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        for name in ("Sintetico", "Nativo"):
            cli.main(["catalog", "create", *db, "--nombre", name, "--fuente", "SINTETICO",
                      "--licencia", "Sin valor comercial", "--edicion", "2026"])  # fmt: skip
        cli.main(["catalog", "import", *db, "--catalogo", "Sintetico", "lote105.csv",
                  *SYNTHETIC_ARGS])  # fmt: skip
        (work / "q1.json").write_text(json.dumps(QUERY_NATIVE), encoding="utf-8")
        revised = [dict(QUERY_NATIVE[0], area=0.0031), *QUERY_NATIVE[1:]]
        (work / "q2.json").write_text(json.dumps(revised), encoding="utf-8")
        cli.main(["catalog", "import", *db, "--catalogo", "Nativo", "q1.json"])
        cli.main(["catalog", "import", *db, "--catalogo", "Nativo", "q2.json"])
    first = "Sintetico/SINTETICO-I/SINT-100x50-4-6"
    second = "Sintetico/SINTETICO-I/SINT-260x95-6-8"
    steps: list[list[str]] = [
        ["search", *db, "--limite", "5"],
        ["search", *db, "--area", "9..11 cm2", "--orden", "area:desc"],
        ["search", *db, "--serie", "sintet", "--iy", ">=500 cm4", "--masa", "..12 kg/m",
         "--dim", "h", "100..200 mm", "--limite", "4"],
        ["search", *db, "--catalogo", "Nativo", "--orden", "masa:desc"],
        ["search", *db, "--catalogo", "Nativo", "--masa", "..30 kg/m", "--dim", "h", "0.2..0.3 m"],
        ["search", *db, "--j", ">=0 cm4"],
        ["search", *db, "--familia", "SHS", "--orden", "area:desc"],
        ["search", *db, "--designacion", "zzz"],
        ["ficha", *db, first],
        ["ficha", *db, "Nativo/N/A-1"],
        ["ficha", *db, "Nativo/N/A-1@1"],
        ["comparar", *db, first, second],
        ["comparar", *db, "Nativo/N/A-1@1", "Nativo/N/A-1", "Nativo/N/A-2", first],
        ["search", *db, "--area", "20 cm2"],
        ["search", *db, "--iy", "1..2 mm"],
        ["search", *db, "--orden", "peso"],
        ["search", *db, "--limite", "0"],
        ["ficha", *db, "Sintetico"],
        ["ficha", *db, "Sintetico/SINTETICO-I/NO-EXISTE"],
        ["comparar", *db, first],
        ["comparar", *db, first, first],
    ]  # fmt: skip
    return "".join(_run(step) for step in steps)


def management_session() -> str:
    """Sesión de gestión (T-023 E2): clon, favoritos y recientes, exportación y verify de clones."""
    work = Path.cwd()
    _prepare(work)
    db = ["--db", "c.sqlite"]
    user = ["--usuario", "u.json"]
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        cli.main(["catalog", "create", *db, "--nombre", "Sintetico", "--fuente", "SINTETICO",
                  "--licencia", "CC BY 4.0", "--edicion", "2026"])  # fmt: skip
        cli.main(["catalog", "import", *db, "--catalogo", "Sintetico", "lote105.csv",
                  *SYNTHETIC_ARGS])  # fmt: skip
    first = "Sintetico/SINTETICO-I/SINT-100x50-4-6"
    second = "Sintetico/SINTETICO-I/SINT-100x55-5-6"
    log = [
        _run(["lock", *db, "--catalogo", "Sintetico"]),
        _run(["clone", *db, "--catalogo", "Sintetico", "--nombre", "Copia"]),
        _run(["clone", *db, "--catalogo", "Sintetico", "--nombre", "copia"]),
        _run(["list", *db]),
        _run(["ficha", *db, "Copia/SINTETICO-I/SINT-100x50-4-6"]),
        _run(["favorito", "agregar", *db, *user, first]),
        _run(["favorito", "agregar", *db, *user, second]),
        _run(["favorito", "agregar", *db, *user, second]),
        _run(["ficha", *db, first, *user]),
        _run(["comparar", *db, first, second, *user]),
        _run(["favorito", "listar", *db, *user]),
        _run(["favorito", "recientes", *db, *user]),
        _run(["favorito", "quitar", *db, *user, second]),
        _run(["favorito", "quitar", *db, *user, second]),
        _run(["exportar", *db, "--catalogo", "Copia", "--formato", "csv", "--salida", "e.csv"]),
        _run(["exportar", *db, "--catalogo", "Copia", "--formato", "csv", "--salida", "e.csv"]),
        _run(["exportar", *db, "--catalogo", "Copia", "--formato", "csv", "--salida", "e.csv",
              "--forzar"]),
        _run(["exportar", *db, "--catalogo", "Sintetico", "--formato", "json",
              "--salida", "e.json"]),
        _run(["exportar", *db, "--catalogo", "Sintetico", "--formato", "json",
              "--salida", "no_existe/e.json"]),
        _run(["create", "--db", "d.sqlite", "--nombre", "Re", "--fuente", "S",
              "--licencia", "L", "--edicion", "1"]),
        _run(["import", "--db", "d.sqlite", "--catalogo", "Re", "e.csv"]),
        _run(["verify", *db]),
    ]  # fmt: skip
    Path("mal.json").write_text('{"format": "veritas-catalog-user/1"}', encoding="utf-8")
    log.append(_run(["favorito", "listar", *db, "--usuario", "mal.json"]))
    log.append(_run(["ficha", *db, first, "--usuario", "mal.json"]))
    shutil.copy("c.sqlite", "t.sqlite")
    connection = sqlite3.connect("t.sqlite")
    connection.execute(
        "UPDATE profile_revisions SET cloned_from_revision = 99"
        " WHERE designation = 'SINT-100x50-4-6' AND cloned_from_revision IS NOT NULL"
    )
    connection.commit()
    connection.close()
    log.append(_run(["verify", "--db", "t.sqlite"]))
    return "".join(log).replace("\\", "/")  # rutas iguales en Windows y Linux


def capture(run: Callable[[], str] | None = None) -> str:
    """Ejecuta una sesión en un directorio temporal y devuelve su salida normalizada."""
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            return (run or session)()
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    only = sys.argv[1:]
    if not only:
        GOLDEN_FILE.write_text(capture(), encoding="utf-8", newline="\n")
    if only in ([], ["consulta"]):
        QUERY_GOLDEN_FILE.write_text(capture(query_session), encoding="utf-8", newline="\n")
    if only in ([], ["gestion"]):
        MANAGE_GOLDEN_FILE.write_text(capture(management_session), encoding="utf-8", newline="\n")
