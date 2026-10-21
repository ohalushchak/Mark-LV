"""The memory database: migration, facts with history, proposals, forgetting,
the prompt block, session summaries and redaction (memory/store.py,
memory/memory_manager.py). Every test gets its own database (conftest)."""

import json
from datetime import datetime, timedelta

from memory import memory_manager as mm
from memory import search
from memory import store


def _legacy(tmp_path, data):
    p = tmp_path / "long_term.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


# ── Migration from long_term.json ─────────────────────────────────────────────

def test_legacy_json_is_moved_once_and_kept_as_bak(tmp_path):
    p = _legacy(tmp_path, {
        "identity": {"name": {"value": "Jane", "updated": "2026-10-01"},
                     "city": "Lviv"},                       # an early bare string
        "preferences": {"coffee": {"value": "black", "updated": "2026-10-02"}},
        "sessions": [{"date": "2026-10-15", "summary": "Tested the HUD.", "language": "English"}],
        "monitors": {"ai": {"topic": "AI"}},
    })
    mem = mm.load_memory()
    assert mem["identity"]["name"] == {"value": "Jane", "updated": "2026-10-01"}
    assert mem["identity"]["city"]["value"] == "Lviv"
    assert mem["preferences"]["coffee"]["value"] == "black"
    assert mm.get_state("monitors") == {"ai": {"topic": "AI"}}
    assert "Tested the HUD." in mm.search_sessions("")
    assert not p.exists() and (tmp_path / "long_term.json.bak").exists()
    # A second open never migrates again, even if a file reappears.
    _legacy(tmp_path, {"identity": {"name": {"value": "Other"}}})
    mm._ready_for = None
    assert mm.load_memory()["identity"]["name"]["value"] == "Jane"


def test_unreadable_legacy_json_is_left_alone(tmp_path):
    p = tmp_path / "long_term.json"
    p.write_text("{not json", encoding="utf-8")
    assert mm.load_memory()["identity"] == {}
    assert p.exists()


def test_schema_version_is_recorded(tmp_path):
    s = store.Store(tmp_path / "x.db")
    assert s.one("PRAGMA user_version")[0] == store.SCHEMA_VERSION
    s.close()
    s = store.Store(tmp_path / "x.db")           # reopening runs nothing again
    assert s.one("PRAGMA user_version")[0] == store.SCHEMA_VERSION
    s.close()


# ── Facts ─────────────────────────────────────────────────────────────────────

def test_new_value_supersedes_and_keeps_history():
    a = mm.save_fact("relationships", "sister", "Олена, живе у Львові", source="user")
    b = mm.save_fact("relationships", "Sister", "Олена, живе в Києві", source="user")
    assert a.status == b.status == "active" and b.previous_id == a.id
    assert mm.load_memory()["relationships"]["sister"]["value"] == "Олена, живе в Києві"
    hist = mm.history("relationships", "sister")
    assert [h["status"] for h in hist] == ["active", "superseded"]
    assert mm.revert(b).startswith("Restored")
    assert mm.load_memory()["relationships"]["sister"]["value"] == "Олена, живе у Львові"


def test_same_value_again_changes_nothing():
    a = mm.save_fact("preferences", "coffee", "black")
    b = mm.save_fact("preferences", "coffee", "black", source="user", evidence="I drink it black")
    assert b.status == "unchanged" and b.id == a.id
    assert len(mm.history("preferences", "coffee")) == 1
    row = store.get().one("SELECT source, evidence FROM facts WHERE id = ?", (a.id,))
    assert row["source"] == "user" and row["evidence"] == "I drink it black"


def test_unknown_category_and_messy_key_are_cleaned():
    s = mm.save_fact("hobbies", " Favourite Game! ", "WoW")
    assert (s.category, s.key) == ("notes", "favourite_game")


def test_proposed_fact_stays_out_of_the_prompt_until_accepted():
    p = mm.save_fact("notes", "car", "Skoda", status="proposed")
    assert p.status == "proposed"
    assert "car" not in mm.load_memory()["notes"]
    assert "Skoda" not in mm.format_memory_for_prompt(mm.load_memory())
    assert "UNCONFIRMED" in mm.search_memory("")
    ui_rows = mm.all_entries_for_ui()
    assert ui_rows[0]["status"] == "proposed"
    assert mm.accept(p.id).startswith("Confirmed")
    assert mm.load_memory()["notes"]["car"]["value"] == "Skoda"
    assert mm.stats()["proposed"] == 0


def test_a_new_proposal_replaces_the_old_one_and_reject_drops_it():
    mm.save_fact("notes", "car", "Skoda", status="proposed")
    p2 = mm.save_fact("notes", "car", "Skoda Octavia", status="proposed")
    rows = store.get().all("SELECT value FROM facts WHERE status = 'proposed'")
    assert [r["value"] for r in rows] == ["Skoda Octavia"]
    assert mm.reject(p2.id).startswith("Dropped")
    assert mm.stats()["proposed"] == 0


def test_a_backed_save_clears_a_pending_proposal():
    mm.save_fact("notes", "car", "Skoda", status="proposed")
    mm.save_fact("notes", "car", "Skoda", source="user")
    assert mm.stats() == {"facts": 1, "proposed": 0, "sessions": 0}


def test_forget_deletes_the_slot_with_its_history():
    mm.save_fact("notes", "gym", "Tuesday")
    mm.save_fact("notes", "gym", "Thursday")
    assert mm.forget("gym", "notes") == "Forgotten: notes/gym"
    assert store.get().one("SELECT COUNT(*) FROM facts")[0] == 0
    assert mm.forget("gym", "notes") == "Not found: notes/gym"


def test_forget_rows_and_restore_rows_round_trip():
    mm.save_fact("relationships", "sister", "Олена")
    mm.save_fact("relationships", "sister", "Олена Петрівна")
    rows = [dict(r) for r in store.get().all("SELECT * FROM facts WHERE status = 'active'")]
    gone = mm.forget_rows(rows)
    assert len(gone) == 2 and mm.load_memory()["relationships"] == {}
    assert "relationships/sister" in mm.restore_rows(gone)
    assert mm.load_memory()["relationships"]["sister"]["value"] == "Олена Петрівна"
    assert len(mm.history("relationships", "sister")) == 2


def test_runaway_guard_drops_history_first(monkeypatch):
    monkeypatch.setattr(mm, "MAX_FACT_ROWS", 3)
    mm.save_fact("notes", "a", "1")
    mm.save_fact("notes", "a", "2")       # 1 is history now
    mm.save_fact("notes", "b", "x")
    mm.save_fact("notes", "c", "y")       # 4 rows → the superseded one goes
    assert store.get().one("SELECT COUNT(*) FROM facts WHERE status = 'superseded'")[0] == 0
    assert set(mm.load_memory()["notes"]) == {"a", "b", "c"}


def test_long_value_is_cut():
    s = mm.save_fact("notes", "long", "x" * 1000)
    assert len(s.value) == mm.MAX_VALUE_LENGTH + 1 and s.value.endswith("…")


# ── Prompt block and recall (full text only: no embedder in these tests) ─────

def test_prompt_block_keeps_identity_and_indexes_overflow(monkeypatch):
    monkeypatch.setattr(mm, "PROMPT_CORE_CHARS", 120)
    mm.save_fact("identity", "name", "Jane")
    for i in range(10):
        mm.save_fact("preferences", f"thing_{i}", "a fairly long preference value here")
    block = mm.format_memory_for_prompt(mm.load_memory())
    assert "Name: Jane" in block and "[ALSO REMEMBERED" in block


def test_recall_by_words_finds_inflected_cyrillic():
    mm.save_fact("relationships", "sister", "Олена, живе в Києві")
    out = mm.search_memory("Києві")
    assert "Олена" in out
    assert "Олена" in mm.search_memory("хто живе у Києві")


def test_recall_reports_nothing_and_that_meaning_search_is_off():
    mm.save_fact("preferences", "coffee", "black")
    out = mm.search_memory("сестра")
    assert out.startswith("Nothing stored about 'сестра'") and "still loading" in out


def test_recall_of_an_empty_store():
    assert mm.search_memory("anything") == "I have not stored anything about this person yet."


def test_fts_syntax_in_a_query_is_never_executed():
    mm.save_fact("notes", "mail", "e-mail is on the work laptop")
    for q in ['e-mail', 'NOT AND OR', '"', '*', 'col:value', "м'ята", "(", "NEAR(a b)"]:
        mm.search_memory(q)                       # must not raise
    assert "work laptop" in mm.search_memory("e-mail")


# ── Session summaries ─────────────────────────────────────────────────────────

def test_summaries_are_kept_searchable_and_briefed_once():
    mm.save_session_summary("Налаштовували повідомлення в Слеку для каналу social.")
    mm.save_session_summary("Тестували компактний режим і трей.")
    assert "компактний" in mm.search_sessions("")
    assert "Слеку" in mm.search_sessions("Слеку")
    last = mm.pop_last_session()
    assert last and "компактний" in last["summary"]
    assert mm.pop_last_session() is None          # both marked as briefed
    assert mm.stats()["sessions"] == 2


def test_an_old_unbriefed_summary_is_not_brought_up():
    old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
    store.get().run("INSERT INTO sessions(ended, summary) VALUES (?, ?)", (old, "Long ago."))
    assert mm.pop_last_session() is None


def test_no_conversations_yet():
    assert mm.search_sessions("x") == "There are no saved conversations yet."


def test_redaction_hides_the_value_not_the_word():
    assert mm.redact("my password is hunter2, ok") == "my password is [REDACTED] ok"
    assert mm.redact("пароль: qwerty від роутера") == "пароль: [REDACTED] від роутера"
    assert mm.redact("PIN 1234") == "PIN [REDACTED]"
    assert "hunter2" not in mm.redact("Password=hunter2")
    assert mm.redact("ключ від машини у шафі") == "ключ від машини у шафі"


def test_summary_is_redacted_before_it_is_stored():
    mm.save_session_summary("Налаштували Wi-Fi, пароль: Secret123 записали.")
    row = store.get().one("SELECT summary FROM sessions")
    assert "Secret123" not in row["summary"]


# ── Named state ───────────────────────────────────────────────────────────────

def test_state_round_trip_and_monitors_use_it():
    assert mm.get_state("missing", 7) == 7
    mm.set_state("x", {"a": [1, "б"]})
    assert mm.get_state("x") == {"a": [1, "б"]}
    from actions import background_monitor as bm
    bm._save({"ai": {"topic": "AI"}})
    assert bm._load() == {"ai": {"topic": "AI"}}


def test_deleting_a_row_removes_its_vector():
    s = mm.save_fact("notes", "a", "x")
    store.get().run("INSERT INTO vectors(kind, ref, model, hash, vec) VALUES "
                    "('fact', ?, 'm', 'h', x'00000000')", (s.id,))
    mm.forget("a", "notes")
    assert store.get().one("SELECT COUNT(*) FROM vectors")[0] == 0
    search.INDEX.changed()
