"""Sesión aceptada de ``veritas catalog`` (T-022 E2): la salida no cambia entre versiones."""

import re
from pathlib import Path

import pytest

import catalog_cli_golden as golden
from veritas.core.messages_es import MESSAGES_ES


@pytest.fixture
def session_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.chdir(tmp_path)
    return golden.session()


def test_the_session_matches_the_accepted_output(session_text: str) -> None:
    assert session_text == golden.GOLDEN_FILE.read_text(encoding="utf-8")


def test_the_session_covers_every_exit_code_and_stream_rule(session_text: str) -> None:
    exits = re.findall(r"^--- exit (\d+)$", session_text, flags=re.MULTILINE)
    assert {"0", "2", "3"} <= set(exits)
    assert "<sha256>" in session_text
    assert "<uuid>" in session_text
    assert "<utc>" in session_text
    assert "{" not in session_text  # ninguna plantilla sin rellenar


FRAGMENTS = {
    "catalog.row_not_number": "no es un número válido",
    "catalog.row_not_positive": "debe ser mayor que 0",
    "catalog.row_required": "Falta el valor obligatorio de",
    "catalog.row_family_unknown": "desconocida. Familias:",
    "catalog.row_value_type": "Valor de tipo no admitido en",
    "catalog.row_duplicate_key": "Clave repetida en el archivo",
    "catalog.warn_iy_less_than_iz": "Iy < Iz en un perfil I",
    "catalog.protected": "está protegido",
    "catalog.import_missing": "No existe el archivo de origen",
    "catalog.import_options": "no válida: «",
    "catalog.verify_hash": "El hash de contenido de la revisión",
    "catalog.verify_batch": "es incoherente",
}


@pytest.mark.parametrize(("key", "fragment"), sorted(FRAGMENTS.items()))
def test_the_session_renders_each_key_with_its_real_parameters(
    session_text: str, key: str, fragment: str
) -> None:
    """Cada clave aparece en la sesión con un fragmento estable de su texto (no una plantilla)."""
    assert key in MESSAGES_ES
    assert fragment in MESSAGES_ES[key]  # el fragmento pertenece de verdad a esa plantilla
    assert fragment in session_text
