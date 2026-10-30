"""Salida aceptada de ``veritas analyze`` normalizada para compararla entre versiones (T-018, D7).

Se enmascara lo que cambia entre corridas sin que cambie la salida:

- run_id, UUID, hora y duración;
- versiones del entorno;
- la línea del pivote mínimo: los tags dependen de UUID aleatorios y el nudo del pivote varía
  entre corridas desde T-007;
- el orden de los diagnósticos dentro de un bloque, que también sigue a los tags;
- los números: el redondeo de los últimos dígitos varía con la plataforma y el orden de los
  tags, y los valores ya los fijan los tests de referencia. Los dígitos de los identificadores
  (T-FEM-001, T-CON-REF-01, «plan T-009») no se enmascaran.

También se colapsan los espacios y los separadores de tabla, que dependen del ancho de los
números. Queda el texto, las etiquetas, las unidades, la estructura y el código de salida.
"""

import contextlib
import io
import re
from pathlib import Path
from typing import Final

from veritas import cli

GOLDEN_DIR: Final = Path(__file__).parent / "golden"

COMMANDS: Final = {
    "demo-viga": ["analyze", "demo", "--caso", "viga"],
    "demo-mecanismo": ["analyze", "demo", "--caso", "mecanismo"],
    "demo-portico": ["analyze", "demo", "--caso", "portico"],
    "demo-tres-materiales": ["analyze", "demo", "--caso", "tres-materiales"],
    "demo-biempotrada": ["analyze", "demo", "--caso", "biempotrada"],
    "uniones": ["analyze", "uniones"],
    "referencias": ["analyze", "referencias"],
    "referencias-kN-m": ["analyze", "referencias", "--profile", "kN-m"],
    "asientos": ["analyze", "asientos"],
    "cargas_barra": ["analyze", "cargas-barra"],
}

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:]+\+00:00")
# Un número no sigue a una letra, a un punto, a «letra-» ni a «…»: así los identificadores
# (T-FEM-001…004, T-CON-REF-01, plan T-009, B1, N3) conservan sus dígitos (D-d de E1).
_NUMBER = re.compile(r"(?<![\w.…])(?<!\w[-−])[-−+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?")
_SEVERITIES: Final = {"INFO", "WARNING", "ERROR"}
_MASKED_LINES = (
    ("Versiones:", "Versiones: <entorno>"),
    ("Salvaguarda de singularidad:", "Salvaguarda de singularidad: <pivote>"),
)


def normalize(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        for prefix, masked in _MASKED_LINES:
            if line.startswith(prefix):
                line = masked
        line = _UUID.sub("<uuid>", line)
        line = _UTC.sub("<utc>", line)
        line = _NUMBER.sub("#", line)
        line = re.sub(r"-{3,}", "---", line)
        line = re.sub(r"\s+", " ", line)
        lines.append(line)
    return "\n".join(_sorted_diagnostics(lines)) + "\n"


def _sorted_diagnostics(lines: list[str]) -> list[str]:
    """Ordena cada bloque consecutivo de diagnósticos: su orden sigue a los tags (UUID)."""
    result: list[str] = []
    block: list[str] = []
    for line in [*lines, ""]:
        if line.split(" ", 1)[0] in _SEVERITIES:
            block.append(line)
            continue
        result += sorted(block)
        block = []
        result.append(line)
    return result[:-1]


def capture(argv: list[str]) -> str:
    """Salida normalizada (stdout y stderr) y código de salida de ``cli.main``."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return normalize(out.getvalue()) + "--- stderr\n" + normalize(err.getvalue()) + f"--- {code}\n"


def write_goldens() -> None:
    """Regenera los archivos golden (solo con la salida aceptada; ver ``tests/golden/README``)."""
    GOLDEN_DIR.mkdir(exist_ok=True)
    for name, argv in COMMANDS.items():
        (GOLDEN_DIR / f"{name}.txt").write_text(capture(argv), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_goldens()
