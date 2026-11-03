"""Sesión aceptada de ``veritas catalog`` normalizada para compararla entre versiones (T-022 E2).

Una sesión completa en un directorio temporal con rutas relativas: crear, simular, importar,
reimportar, ``--estricto``, listar, mostrar, proteger, verificar y los fallos (todos con su
código de salida). Se enmascara solo lo que cambia entre ejecuciones sin cambiar la salida:
los UUID (v4 aleatorios), la fecha UTC del lote y los SHA-256, que dependen de los bytes del
archivo de origen (CRLF o LF según ``core.autocrlf``). No se usa ``cli_golden.normalize``: ese
enmascara también los números, y aquí los recuentos son parte de lo que se acepta.

Regenerar solo con una salida aceptada (ver ``tests/golden/README.md``):

    uv run python tests/catalog_cli_golden.py
"""

import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Final

from veritas import cli

DATA_DIR: Final = Path(__file__).parent / "data"
GOLDEN_FILE: Final = Path(__file__).parent / "golden" / "catalogo_cli.txt"

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


def capture() -> str:
    """Ejecuta la sesión en un directorio temporal y devuelve su salida normalizada."""
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            return session()
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    GOLDEN_FILE.write_text(capture(), encoding="utf-8", newline="\n")
