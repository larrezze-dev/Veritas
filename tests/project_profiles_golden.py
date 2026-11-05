"""Sesión aceptada de ``veritas project assign-profile|replace-profile|sections`` (T-024 E2).

Directorio temporal con rutas relativas: catálogo sintético de 100 perfiles, un proyecto con dos
barras de acero y una de madera, asignación estricta (rechazada), parcial, repetida, listado de
secciones con sus dos dictámenes, vigencia y los fallos con su código de salida. Se enmascaran
solo los UUID y los SHA-256 (como en ``catalog_cli_golden``). Regenerar solo con una salida
aceptada (``tests/golden/README.md``):

    uv run python tests/project_profiles_golden.py
"""

import contextlib
import io
import os
import tempfile
from pathlib import Path
from typing import Final

from catalog_cli_golden import DATA_DIR, MAPPING, SYNTHETIC_ARGS, normalize
from test_catalog_assign import mixed_model
from veritas import cli
from veritas.persistence.project import save_project

GOLDEN_FILE: Final = Path(__file__).parent / "golden" / "proyecto_perfiles.txt"
DB: Final = ["--db", "c.sqlite"]
REF: Final = "Sint/SINTETICO-I/SINT-100x50-4-6"


def _run(argv: list[str], *, group: str = "project") -> str:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([group, *argv])
    return (
        f"$ veritas {group} {' '.join(argv)}\n"
        f"--- stdout\n{normalize(out.getvalue())}"
        f"--- stderr\n{normalize(err.getvalue())}"
        f"--- exit {code}\n\n"
    )


def session() -> str:
    work = Path.cwd()
    (work / "lote.csv").write_bytes((DATA_DIR / "catalog_synthetic_100.csv").read_bytes())
    (work / MAPPING).write_bytes((DATA_DIR / MAPPING).read_bytes())
    prepare = [
        cli.main(["catalog", "create", *DB, "--nombre", "Sint", "--fuente", "SINTÉTICO",
                  "--licencia", "Sin valor comercial", "--edicion", "2026"]),
        cli.main(["catalog", "import", *DB, "--catalogo", "Sint", "lote.csv", *SYNTHETIC_ARGS]),
    ]  # fmt: skip
    assert prepare == [0, 0]
    model, _, _ = mixed_model()
    save_project(work / "p.json", model)
    log = [
        _run(["sections", "p.json"]),
        _run(["assign-profile", "p.json", "--todas", REF, *DB]),
        _run(["assign-profile", "p.json", "--todas", REF, *DB, "--parcial"]),
        _run(["assign-profile", "p.json", "A", REF, *DB]),
        _run(["replace-profile", "p.json", REF, "--barras", "T", *DB, "--parcial"]),
        _run(["sections", "p.json", *DB]),
        _run(["verify", "p.json", *DB]),
        _run(["verify", "p.json"]),
        _run(["assign-profile", "p.json", "A", "Sint/SINTETICO-I/NO-EXISTE", *DB]),
        _run(["assign-profile", "p.json", "Z", REF, *DB]),
        _run(["assign-profile", "p.json", REF, *DB]),
        _run(["replace-profile", "p.json", REF, *DB]),
    ]  # fmt: skip
    return "".join(log).replace("\\", "/")


def capture() -> str:
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            return session()
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    GOLDEN_FILE.write_text(capture(), encoding="utf-8", newline="\n")
