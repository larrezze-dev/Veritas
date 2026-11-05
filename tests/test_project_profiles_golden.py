"""Sesión aceptada de los comandos de perfiles de proyecto (T-024 E2): la salida no cambia."""

import re
from pathlib import Path

import pytest

import project_profiles_golden as golden


@pytest.fixture
def session_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.chdir(tmp_path)
    return golden.session()


def test_the_session_matches_the_accepted_output(session_text: str) -> None:
    assert session_text == golden.GOLDEN_FILE.read_text(encoding="utf-8")


def test_the_session_covers_the_exit_codes_and_both_verdicts(session_text: str) -> None:
    exits = re.findall(r"^--- exit (\d+)$", session_text, flags=re.MULTILINE)
    assert {"0", "2"} <= set(exits)
    assert "FEM: " in session_text
    assert "Norma: NO_COMPROBADO" in session_text
    assert "OMITIDA" in session_text
    assert "BLOQUEADA" in session_text
    assert "{" not in session_text  # ninguna plantilla sin rellenar
