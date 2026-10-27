"""Ejecución del worker OpenSeesPy en un proceso nuevo por corrida (ADR-002 §4-§5).

``[sys.executable, "-I", ruta_absoluta_worker, ruta_respuesta]``, sin shell, con ``cwd``
en un directorio temporal propio. La petición va por stdin; la respuesta, a un fichero
de ese directorio. stdout y stderr se capturan por separado y se truncan. Al agotar el
tiempo, ``subprocess.run`` mata el proceso y espera su fin; no se publica nada parcial.
"""

import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from veritas.fem.adapters import opensees_worker

WORKER_PATH: Final = Path(opensees_worker.__file__).resolve()
MAX_LOG_CHARS: Final = 20_000
RESPONSE_NAME: Final = "veritas-response.json"


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    """Resultado del proceso: texto de la respuesta (si existe) y registros."""

    response_text: str | None
    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str
    duration_s: float


def _truncate(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if len(text) <= MAX_LOG_CHARS:
        return text
    return text[:MAX_LOG_CHARS] + f"\n[… truncado a {MAX_LOG_CHARS} caracteres]"


def run_worker(request_json: str, timeout_s: float, worker: Path = WORKER_PATH) -> WorkerOutcome:
    """Ejecuta ``worker`` con la petición. ``worker`` es configurable para las pruebas."""
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="veritas-run-") as directory:
        response_path = Path(directory) / RESPONSE_NAME
        command = [sys.executable, "-I", str(worker.resolve()), str(response_path)]
        try:
            # Sin shell; intérprete y script con ruta absoluta (ADR-002 §4).
            completed = subprocess.run(
                command,
                input=request_json,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                cwd=directory,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as expired:
            return WorkerOutcome(
                None,
                None,
                True,
                _truncate(expired.stdout),
                _truncate(expired.stderr),
                time.perf_counter() - start,
            )
        response_text = (
            response_path.read_text(encoding="utf-8") if response_path.is_file() else None
        )
        return WorkerOutcome(
            response_text,
            completed.returncode,
            False,
            _truncate(completed.stdout),
            _truncate(completed.stderr),
            time.perf_counter() - start,
        )
