"""Favoritos y recientes del usuario (T-023 E2): modelo, archivo estricto y guardado atómico."""

import json
from pathlib import Path
from uuid import UUID

import pytest

from catalog_support import assert_renders
from veritas.catalogs.errors import CatalogFileError
from veritas.catalogs.userdata import (
    MAX_RECENT,
    USER_FORMAT,
    UserData,
    UserKey,
    load_user_data,
    parse_user_data,
    save_user_data,
    user_data_text,
)

CAT = UUID(int=11)
OTHER = UUID(int=12)


def key(designation: str, *, catalog: UUID = CAT, series: str = "S1", name: str = "Cat") -> UserKey:
    return UserKey(catalog, name, series, designation)


# --- Modelo --------------------------------------------------------------------------------


def test_favorites_keep_insertion_order_and_adding_twice_changes_nothing() -> None:
    data, added = UserData().add_favorite(key("A"))
    assert added
    data, added = data.add_favorite(key("B"))
    again, repeated = data.add_favorite(key("A", name="Otro nombre"))
    assert (added, repeated) == (True, False)
    assert again is data
    assert [k.designation for k in data.favorites] == ["A", "B"]


def test_the_identity_is_catalog_id_series_and_designation_not_the_catalog_name() -> None:
    data, _ = UserData().add_favorite(key("A", name="Uno"))
    assert data.is_favorite(key("A", name="Renombrado"))
    assert not data.is_favorite(key("A", catalog=OTHER))
    assert not data.is_favorite(key("A", series="S2"))
    _, was = data.remove_favorite(key("A", catalog=OTHER))
    assert not was


def test_removing_a_favorite() -> None:
    data = UserData((key("A"), key("B")))
    after, was = data.remove_favorite(key("A"))
    assert was
    assert [k.designation for k in after.favorites] == ["B"]
    assert data.remove_favorite(key("Z")) == (data, False)


def test_recents_put_the_last_consulted_first_without_repeats() -> None:
    data = UserData().touch([key("A")]).touch([key("B")]).touch([key("A")])
    assert [k.designation for k in data.recent] == ["A", "B"]
    data = data.touch([key("C"), key("D")])  # se consultan en este orden: D es el último
    assert [k.designation for k in data.recent] == ["D", "C", "A", "B"]


def test_recents_are_capped_and_drop_the_oldest() -> None:
    data = UserData()
    for n in range(MAX_RECENT + 5):
        data = data.touch([key(f"P{n}")])
    assert len(data.recent) == MAX_RECENT
    assert data.recent[0].designation == f"P{MAX_RECENT + 4}"
    assert data.recent[-1].designation == "P5"


def test_recents_do_not_change_favorites_and_vice_versa() -> None:
    data = UserData((key("F"),)).touch([key("R")])
    assert [k.designation for k in data.favorites] == ["F"]
    after, _ = data.add_favorite(key("G"))
    assert after.recent == data.recent


# --- Archivo -------------------------------------------------------------------------------


def test_the_document_round_trips_exactly_and_is_deterministic() -> None:
    data = UserData((key("A"), key("B", series="Ñ")), (key("C"), key("A")))
    text = user_data_text(data)
    assert text == user_data_text(data)
    assert text.endswith("\n")
    assert json.loads(text)["format"] == USER_FORMAT
    assert parse_user_data(text, Path("u.json")) == data
    assert "Ñ" in text  # UTF-8 sin escapes


def test_a_missing_file_is_an_empty_user_and_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "u.json"
    assert load_user_data(path) == UserData()
    assert not path.exists()


def test_saving_creates_the_file_and_replaces_it_without_leftovers(tmp_path: Path) -> None:
    path = tmp_path / "u.json"
    save_user_data(path, UserData((key("A"),)))
    first = path.read_bytes()
    save_user_data(path, UserData((key("A"), key("B"))))
    assert path.read_bytes() != first
    assert load_user_data(path).favorites[1].designation == "B"
    assert [p.name for p in tmp_path.iterdir()] == ["u.json"]  # sin temporales


def entry(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "catalog_id": str(CAT),
        "catalog_name": "Cat",
        "series": "S1",
        "designation": "A",
    }
    return {**base, **changes}


def document(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "format": USER_FORMAT,
        "favorites": [entry()],
        "recent": [entry()],
    }
    return {**base, **changes}


def corrupted() -> list[tuple[str, str]]:
    cases: list[tuple[str, object]] = [
        ("formato", document(format="otro/9")),
        ("sobra", document(extra=1)),
        ("falta", {"format": USER_FORMAT, "favorites": []}),
        ("lista", document(favorites={})),
        ("clave", document(favorites=[{"catalog_id": str(CAT)}])),
        ("uuid", document(favorites=[entry(catalog_id="no")])),
        ("vacio", document(favorites=[entry(series=" ")])),
        ("tipo", document(favorites=[entry(designation=5)])),
        ("repetido", document(favorites=[entry(), entry()])),
        (
            "demasiados",
            document(recent=[entry(designation=f"P{i}") for i in range(MAX_RECENT + 1)]),
        ),
        ("raiz", [1, 2]),
    ]
    return [(name, json.dumps(value)) for name, value in cases]


@pytest.mark.parametrize(("name", "text"), corrupted(), ids=lambda v: v if len(v) < 20 else "")
def test_a_defective_document_is_rejected_with_a_file_error(name: str, text: str) -> None:
    with pytest.raises(CatalogFileError) as error:
        parse_user_data(text, Path("u.json"))
    assert error.value.diagnostic.message_key == "catalog.user_file", name
    assert_renders(error.value.diagnostic)


@pytest.mark.parametrize(
    "text", ["", "{", "[1,", '{"format": NaN}', '{"a": 1, "a": 2}', "no es json"]
)
def test_unparseable_json_is_rejected(text: str) -> None:
    with pytest.raises(CatalogFileError) as error:
        parse_user_data(text, Path("u.json"))
    assert error.value.diagnostic.message_key == "catalog.user_file"


def test_an_unreadable_or_foreign_file_is_an_error_and_is_never_modified(tmp_path: Path) -> None:
    path = tmp_path / "u.json"
    for content in (b"\xff\xfe\x00 no utf8", b"[1, 2, 3]", b'{"format": "x"}'):
        path.write_bytes(content)
        with pytest.raises(CatalogFileError):
            load_user_data(path)
        assert path.read_bytes() == content
    with pytest.raises(CatalogFileError):
        load_user_data(tmp_path)  # un directorio no es un archivo de usuario
