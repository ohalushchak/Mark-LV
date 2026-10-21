"""Who "Olya" is: the user's contacts refuse to guess between people, show
numbers as their last digits, keep emergency numbers out, and every change by
voice can be undone (core/contacts.py, actions/contacts_book.py)."""
import pytest

from actions import contacts_book as book
from core import contacts as cb, undo


@pytest.fixture(autouse=True)
def _file(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "PATH", tmp_path / "contacts.json")
    undo.clear()
    yield tmp_path / "contacts.json"


PEOPLE = [
    cb.Contact("Оля Петренко", ["Оля"], "+380671234567", {"telegram": "Olya Petrenko"}),
    cb.Contact("Тарас Коваль", ["Тарас"], "+380501112233"),
    cb.Contact("Тарас Шевчук", [], ""),
]


def test_a_name_or_alias_finds_the_one_person():
    assert cb.resolve("Оля", PEOPLE).contact.name == "Оля Петренко"
    assert cb.resolve("моя Оля Петренко", PEOPLE).contact.name == "Оля Петренко"
    assert cb.resolve("Olya Petrenko", PEOPLE).contact.name == "Оля Петренко"   # the Telegram name
    assert cb.resolve("Петренко", PEOPLE).contact.name == "Оля Петренко"        # a last name


def test_two_people_under_one_name_is_a_question():
    f = cb.resolve("Тарас Коваль", PEOPLE)
    assert f.contact.name == "Тарас Коваль"
    two = [*PEOPLE, cb.Contact("Тарас Коваль", [], "+380631234500")]
    f = cb.resolve("Тарас Коваль", two)
    assert f.contact is None and f.ambiguous and len(f.candidates) == 2
    # "Тарас" alone is an alias of one and the first name of another
    f = cb.resolve("Тарас", [cb.Contact("Тарас Коваль"), cb.Contact("Тарас Шевчук")])
    assert f.contact is None and f.ambiguous
    assert "1) Тарас Коваль" in cb.describe(f.candidates)


def test_a_near_miss_must_lead_clearly():
    assert cb.resolve("Олья Петренко", PEOPLE).contact.name == "Оля Петренко"
    f = cb.resolve("Ivan", PEOPLE)
    assert f.contact is None and not f.ambiguous


def test_numbers_are_masked_normalised_and_emergency_refused():
    assert cb.normalize_phone("+38 (067) 123-45-67") == "+380671234567"
    assert cb.normalize_phone("0038 067 123 45 67") == "+380671234567"
    assert cb.normalize_phone("067 123 45 67") == "0671234567"          # no country guessed
    assert cb.international("0671234567") == ""
    assert cb.masked("+380671234567") == "…4567"
    assert cb.is_emergency("112") and cb.is_emergency("1-0-3") and not cb.is_emergency("+380671234567")
    assert cb.resolve("+380 67 123 45 67", PEOPLE).contact.name == "Оля Петренко"


def test_the_tool_never_says_a_full_number():
    cb.save(PEOPLE)
    out = book.contacts({"action": "find", "name": "Оля"})
    assert "…4567" in out and "671234567" not in out and "Olya Petrenko" in out


def test_add_change_remove_and_undo(_file):
    out = book.contacts({"action": "add", "name": "Оля Петренко", "phone": "+380 67 123 45 67",
                         "aliases": "Оля, Olya", "app": "telegram", "app_name": "Olya Petrenko"})
    assert out.startswith("Added") and "…4567" in out
    assert cb.load()[0].apps == {"telegram": "Olya Petrenko"}
    out = book.contacts({"action": "add", "name": "оля петренко", "app": "slack", "app_name": "Olha P."})
    assert out.startswith("Updated") and cb.load()[0].apps["slack"] == "Olha P."
    undo.undo_last()
    assert "slack" not in cb.load()[0].apps
    assert book.contacts({"action": "remove", "name": "Оля"}).startswith("Removed")
    assert cb.load() == []
    undo.undo_last()
    assert cb.load()[0].name == "Оля Петренко"


def test_an_emergency_number_is_not_stored():
    assert "emergency" in book.contacts({"action": "add", "name": "Police", "phone": "112"})
    assert cb.load() == []


def test_rename_and_undo():
    cb.save([cb.Contact("Остап", [], "+380671112233", {"viber": "Остапчик"})])
    assert book.contacts({"action": "rename", "name": "Остап", "new_name": "Остапчик"}).startswith("Renamed")
    assert cb.load()[0].name == "Остапчик" and cb.load()[0].phone == "+380671112233"
    undo.undo_last()
    assert cb.load()[0].name == "Остап"
