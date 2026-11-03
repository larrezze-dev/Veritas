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


# --- Sesión de consulta (T-023 E1) -------------------------------------------------------------


@pytest.fixture
def query_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.chdir(tmp_path)
    return golden.query_session()


def test_the_query_session_matches_the_accepted_output(query_text: str) -> None:
    assert query_text == golden.QUERY_GOLDEN_FILE.read_text(encoding="utf-8")


def test_the_query_session_covers_success_and_failure_with_no_unfilled_template(
    query_text: str,
) -> None:
    exits = re.findall(r"^--- exit (\d+)$", query_text, flags=re.MULTILINE)
    assert set(exits) == {"0", "2"}
    assert "{" not in query_text
    assert "<uuid>" in query_text
    assert query_text.count("ficha") >= 3


QUERY_FRAGMENTS = {
    # clave: (fragmento fijo de la plantilla, texto que aparece ya con sus parámetros)
    "catalog.search_range": ("Rango no válido en", "Rango no válido en --area"),
    "catalog.search_order": ("Orden no válido", "Orden no válido: «peso»"),
    "catalog.reference_invalid": ("Referencia de perfil no válida", "no válida: «Sintetico»"),
    "catalog.compare_count": ("Se comparan de", "Se comparan de 2 a 8 perfiles; se recibieron 1."),
    "catalog.compare_repeated": ("aparece más de una vez", "El perfil «Sintetico/SINTETICO-I/"),
    "catalog.profile_not_found": ("No existe el perfil", "No existe el perfil «SINTETICO-I / NO-"),
    "catalog.import_options": ("no válida: «", "Opción --limite no válida: «0»"),
}


@pytest.mark.parametrize(("key", "texts"), sorted(QUERY_FRAGMENTS.items()))
def test_the_query_session_renders_each_key_with_its_real_parameters(
    query_text: str, key: str, texts: tuple[str, str]
) -> None:
    template_fragment, rendered = texts
    assert template_fragment in MESSAGES_ES[key]  # el fragmento pertenece a esa plantilla
    assert rendered in query_text


# --- Sesión de gestión (T-023 E2) --------------------------------------------------------------


@pytest.fixture
def manage_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.chdir(tmp_path)
    return golden.management_session()


def test_the_management_session_matches_the_accepted_output(manage_text: str) -> None:
    assert manage_text == golden.MANAGE_GOLDEN_FILE.read_text(encoding="utf-8")


def test_the_management_session_covers_every_exit_code_with_no_unfilled_template(
    manage_text: str,
) -> None:
    exits = re.findall(r"^--- exit (\d+)$", manage_text, flags=re.MULTILINE)
    assert set(exits) == {"0", "2", "3"}
    assert "{" not in manage_text
    assert "\\" not in manage_text  # rutas iguales en Windows y Linux
    assert "<sha256>" in manage_text


MANAGE_FRAGMENTS = {
    "catalog.name_taken": ("Ya existe un catálogo llamado", "llamado «copia»"),
    "catalog.user_file": ("no es válido (", "mal.json no es válido ("),
    "catalog.output_exists": ("use --forzar", "Ya existe e.csv: use --forzar"),
    "catalog.output_error": ("No se pudo escribir", "No se pudo escribir no_existe/e.json"),
    "catalog.verify_clone": (
        "trazabilidad de clonación",
        "trazabilidad de clonación de revisión 1 es incoherente (revisión de origen inexistente)",
    ),
}


@pytest.mark.parametrize(("key", "texts"), sorted(MANAGE_FRAGMENTS.items()))
def test_the_management_session_renders_each_key_with_its_real_parameters(
    manage_text: str, key: str, texts: tuple[str, str]
) -> None:
    template_fragment, rendered = texts
    assert template_fragment in MESSAGES_ES[key]
    assert rendered in manage_text
