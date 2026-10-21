"""Is a fact the model saves backed by what the user said? (memory/trust.py,
memory_manager.save_from_model, core/activity.recent_user_text)."""

import pytest

from core import activity
from memory import embedder
from memory import memory_manager as mm
from memory import trust
from memory_fakes import FakeEmbedder


@pytest.fixture(autouse=True)
def _clean_activity():
    activity.reset()
    yield
    activity.reset()


def test_words_meet_across_inflection_and_alphabets():
    assert trust.word_support("Олена, живе у Львові", "сестра Олена живе у Львові") == 1.0
    assert trust.word_support("Bohdan", "Мене звати Богдан") == 1.0
    assert trust.word_support("Andriy Kovalenko", "мій керівник Андрій Коваленко") == 1.0
    assert trust.word_support("вівторок і четвер", "по вівторках і четвергах") == 1.0
    assert trust.word_support("Skoda Octavia", "відкрий блокнот") == 0.0


def test_backed_by_words_is_active_with_evidence():
    status, evidence = trust.judge("relationships", "sister", "Олена",
                                   ["відкрий блокнот", "У мене є сестра Олена"])
    assert status == "active" and evidence == "У мене є сестра Олена"


def test_nothing_said_means_proposed():
    assert trust.judge("notes", "car", "Skoda", []) == ("proposed", "")
    assert trust.judge("notes", "car", "Skoda", ["відкрий блокнот"]) == ("proposed", "")


def test_language_observation_is_never_checked():
    assert trust.judge("identity", "language", "Ukrainian", []) == ("active", "")


def test_meaning_backs_a_translation():
    embedder.use(FakeEmbedder())
    status, evidence = trust.judge("notes", "allergy", "nuts",
                                   ["відкрий блокнот", "алергія на горіхи"])
    assert status == "active" and evidence == "алергія на горіхи"
    embedder.use(None)


def test_save_from_model_reads_what_the_user_said():
    activity.note_user_input("Мене звати")
    activity.note_user_input("Богдан")              # transcription pieces glue together
    assert activity.recent_user_text() == ["Мене звати Богдан"]
    s = mm.save_from_model("identity", "name", "Bohdan")
    assert s.status == "active"
    row = mm.all_entries_for_ui()[0]
    assert row["source"] == "user" and row["evidence"] == "Мене звати Богдан"


def test_an_inferred_fact_waits_for_the_user():
    activity.note_user_input("відкрий налаштування")
    s = mm.save_from_model("identity", "job", "QA engineer")
    assert s.status == "proposed"
    assert "job" not in mm.load_memory()["identity"]


def test_old_words_do_not_count():
    activity.note_user_input("Мене звати Олег", at=0.0)    # long ago
    assert activity.recent_user_text(seconds=60) == []


def test_typed_and_spoken_inputs_are_separate_utterances():
    activity.note_user_input("перше", at=100.0)
    activity.note_user_input("друге", at=110.0)
    assert activity.recent_user_text(seconds=10**9) == ["перше", "друге"]


def test_a_late_transcript_confirms_the_suggestion():
    """The model's save_memory can beat the user's transcript; the recheck
    after RECHECK_S sees the words and confirms the fact."""
    settled = []
    s = mm.save_from_model("relationships", "brother_name", "Максим", utterances=[],
                           on_final=settled.append, recheck_s=10**6)
    assert s.status == "proposed"
    activity.note_user_input("Мого брата звати Максим")
    # Run the recheck now instead of waiting for the timer.
    mm.save_from_model("relationships", "brother_name", "Максим", utterances=[],
                       on_final=settled.append, recheck_s=0)
    assert mm.load_memory()["relationships"]["brother_name"]["value"] == "Максим"
    assert settled[-1].status == "active"


def test_a_suggestion_nobody_backs_settles_as_proposed():
    settled = []
    activity.note_user_input("відкрий блокнот")
    mm.save_from_model("notes", "car", "Skoda", on_final=settled.append, recheck_s=0)
    assert [x.status for x in settled] == ["proposed"]


def test_a_backed_save_settles_at_once():
    settled = []
    activity.note_user_input("Мене звати Богдан")
    mm.save_from_model("identity", "name", "Богдан", on_final=settled.append)
    assert [x.status for x in settled] == ["active"]


def test_a_question_backs_nothing():
    """Seen live: "що в мене в нотатках про Atlas?" was taken as backing for a
    'fact' the model made from the search result."""
    assert trust.is_question("Що в мене в нотатках про Atlas?")
    assert trust.is_question("where does my sister live")
    assert not trust.is_question("Мого брата звати Максим")
    status, _ = trust.judge("notes", "atlas_notes", "нотатки про Atlas",
                            ["Що в мене в нотатках про Atlas?"])
    assert status == "proposed"
    status, _ = trust.judge("notes", "atlas_notes", "нотатки про Atlas",
                            ["Що в мене в нотатках про Atlas?", "Запам'ятай: нотатки про Atlas лежать у робочій теці"])
    assert status == "active"
