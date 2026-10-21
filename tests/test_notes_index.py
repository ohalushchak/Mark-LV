"""The Obsidian vault index and the memory tools (memory/notes_index.py,
actions/notes.py, actions/forget_memory.py, actions/recall_conversation.py)."""

import json
import os
import time

import pytest

from actions import forget_memory as fm_action
from actions import notes as notes_action
from actions import recall_conversation as rc_action
from core import undo
from memory import embedder
from memory import memory_manager as mm
from memory import notes_index as ni
from memory import search
from memory import store
from memory_fakes import FakeEmbedder


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "Vault"
    (v / "Projects").mkdir(parents=True)
    (v / ".obsidian").mkdir()
    (v / ".obsidian" / "workspace.md").write_text("# hidden", encoding="utf-8")
    (v / "Projects" / "Atlas.md").write_text(
        "---\ntags: [work]\n---\n# Atlas\nPlanning platform.\n\n## Next steps\n"
        "Надіслати оцінку клієнту до п'ятниці.\n", encoding="utf-8")
    (v / "Рецепти.md").write_text("# Рецепти\nВареники з картоплею.\n", encoding="utf-8")
    monkeypatch.setattr(ni, "vault_path", lambda: v)
    monkeypatch.setattr(ni, "_last_scan", 0.0)
    return v


# ── Chunking ──────────────────────────────────────────────────────────────────

def test_chunks_follow_headings_and_drop_front_matter():
    chunks = ni.chunk_markdown("---\na: b\n---\nIntro\n# Top\ntext\n## Sub\nmore\n# Next\nend")
    assert chunks == [("", "Intro"), ("Top", "text"), ("Top › Sub", "more"), ("Next", "end")]


def test_hashes_inside_code_fences_are_not_headings():
    chunks = ni.chunk_markdown("# Real\n```\n# not a heading\n```\nafter")
    assert [h for h, _ in chunks] == ["Real"]
    assert "# not a heading" in chunks[0][1]


def test_long_sections_are_cut_with_overlap():
    para = ("слово " * 120).strip()                 # ~720 chars
    chunks = ni.chunk_markdown("# H\n" + "\n\n".join([para + f" {i}" for i in range(4)]))
    assert len(chunks) >= 3
    assert all(len(t) <= ni.CHUNK_CHARS + ni.OVERLAP_CHARS + 10 for _, t in chunks)
    huge = ni.chunk_markdown("x " * 2000)
    assert all(len(t) <= ni.CHUNK_CHARS + ni.OVERLAP_CHARS + 10 for _, t in huge)


# ── Incremental index ─────────────────────────────────────────────────────────

def test_rescan_adds_changes_and_removes(vault):
    r = ni.rescan(force=True)
    assert (r["added"], r["notes"]) == (2, 2)            # .obsidian is skipped
    assert ni.rescan(force=True)["added"] == 0           # unchanged: nothing re-read
    note = vault / "Рецепти.md"
    note.write_text("# Рецепти\nБорщ і вареники.\n", encoding="utf-8")
    os.utime(note, ns=(time.time_ns(), time.time_ns() + 10**9))
    assert ni.rescan(force=True)["changed"] == 1
    (vault / "Projects" / "Atlas.md").unlink()
    r = ni.rescan(force=True)
    assert r["removed"] == 1
    assert store.get().one("SELECT COUNT(*) FROM chunks c JOIN notes n ON n.id = c.note_id "
                           "WHERE n.path LIKE 'Projects%'")[0] == 0


def test_rescans_are_rate_limited_unless_forced(vault):
    ni.rescan(force=True)
    (vault / "New.md").write_text("new", encoding="utf-8")
    ni.rescan()                                          # within RESCAN_MIN_S
    paths = lambda: {r["path"] for r in store.get().all("SELECT path FROM notes")}
    assert "New.md" not in paths()
    assert ni.rescan(force=True)["added"] == 1
    assert "New.md" in paths()


def test_another_vault_starts_over(vault, tmp_path):
    ni.rescan(force=True)
    other = tmp_path / "Other"
    other.mkdir()
    (other / "Only.md").write_text("one", encoding="utf-8")
    r = ni.rescan(force=True, vault=other)
    assert r["notes"] == 1
    assert [n["path"] for n in store.get().all("SELECT path FROM notes")] == ["Only.md"]


def test_obsidian_config_is_read(tmp_path):
    v1, v2 = tmp_path / "A", tmp_path / "B"
    v1.mkdir(), v2.mkdir()
    cfg = tmp_path / "obsidian.json"
    cfg.write_text(json.dumps({"vaults": {
        "x": {"path": str(v1), "ts": 5},
        "y": {"path": str(v2), "ts": 1, "open": True},
        "z": {"path": str(tmp_path / "gone"), "ts": 9}}}), encoding="utf-8")
    vaults = ni.obsidian_vaults(cfg)
    assert [v["name"] for v in vaults] == ["B", "A"]


# ── Search, read, open ────────────────────────────────────────────────────────

def test_search_wraps_results_as_data(vault):
    out = ni.search("клієнту оцінку")
    assert out.startswith(ni.WRAP_OPEN) and ni.WRAP_CLOSE in out
    assert "Projects/Atlas.md › Atlas › Next steps" in out


def test_search_by_meaning_reports_progress(vault):
    embedder.use(FakeEmbedder())
    ni.rescan(force=True)
    out = ni.search("вареники")
    assert "Рецепти.md" in out and "being built" in out   # nothing embedded yet
    search.embed_pending("chunk")
    assert "being built" not in ni.search("вареники")
    embedder.use(None)


def test_read_by_title_and_never_outside_the_vault(vault, tmp_path):
    (tmp_path / "secret.md").write_text("outside", encoding="utf-8")
    assert "Planning platform." in ni.read("Atlas")
    assert "tags:" not in ni.read("Atlas")
    assert "Вареники" in ni.read("Рецепти.md")
    assert ni.read("../secret").startswith("No note called")
    assert ni.read(str(tmp_path / "secret.md")).startswith("No note called")


def test_open_builds_an_obsidian_link(vault):
    msg, uri = ni.open_note("Atlas")
    assert uri == "obsidian://open?vault=Vault&file=Projects/Atlas"
    assert msg == "Opened Projects/Atlas.md in Obsidian."


def test_no_vault_is_explained(monkeypatch):
    monkeypatch.setattr(ni, "vault_path", lambda: None)
    assert "NOTES VAULT" in ni.search("x")
    assert "NOTES VAULT" in notes_action.notes({"action": "status"})
    assert notes_action._probe()[0] is False


def test_notes_tool_actions(vault, monkeypatch):
    launched = []
    monkeypatch.setattr(notes_action, "_launch", launched.append)
    assert "Atlas" in notes_action.notes({"query": "planning"})
    assert "Вареники" in notes_action.notes({"action": "read", "note": "Рецепти"})
    assert notes_action.notes({"action": "open", "note": "Atlas"}).startswith("Opened")
    assert launched == ["obsidian://open?vault=Vault&file=Projects/Atlas"]
    assert "2 notes" in notes_action.notes({"action": "status"})
    assert notes_action.notes({}) == "Say what to look for in the notes."
    assert notes_action.TOOL["name"] == "notes"


# ── Memory tools ──────────────────────────────────────────────────────────────

def test_forget_memory_names_what_went_and_undo_brings_it_back(monkeypatch):
    monkeypatch.setattr(undo, "_stack", [])
    mm.save_fact("relationships", "sister", "Олена Коваль")
    mm.save_fact("notes", "dog", "Бакс")
    out = fm_action.forget_memory({"subject": "Олена"})
    assert "Deleted 1" in out and "sister" in out
    assert "sister" not in mm.load_memory()["relationships"]
    assert "dog" in mm.load_memory()["notes"]
    assert "relationships/sister" in undo.undo_last()
    assert mm.load_memory()["relationships"]["sister"]["value"] == "Олена Коваль"


def test_forget_memory_with_no_match_deletes_nothing():
    mm.save_fact("notes", "dog", "Бакс")
    assert "nothing was deleted" in fm_action.forget_memory({"subject": "Японія"})
    assert fm_action.forget_memory({}).startswith("Say what to forget")


def test_recall_conversation_tool():
    assert rc_action.recall_conversation({}) == "There are no saved conversations yet."
    mm.save_session_summary("Обговорювали стендап о 10:30.")
    assert "стендап" in rc_action.recall_conversation({"query": "стендап"})


def test_a_match_buried_in_a_project_subfolder_opens_the_folder_note():
    known = {"P/Orbit/Orbit.md", "P/Orbit/pkg/sources/history/old.md",
             "Рецепти/Рецепти.md", "Рецепти/Меню.md", "P/Orbit/log.md"}
    assert ni._folder_note_for("P/Orbit/pkg/sources/history/old.md", known) == "P/Orbit/Orbit.md"
    assert ni._folder_note_for("Рецепти/Меню.md", known) == "Рецепти/Меню.md"
    assert ni._folder_note_for("P/Orbit/log.md", known) == "P/Orbit/log.md"
    assert ni._folder_note_for("Home.md", known) == "Home.md"


def test_search_keeps_at_most_two_passages_per_note(vault):
    big = "\n\n".join(f"## Частина {i}\nоцінка клієнту номер {i} " + "текст " * 150
                      for i in range(8))
    (vault / "Big.md").write_text(big, encoding="utf-8")
    ni.rescan(force=True)
    out = ni.search("оцінка клієнту")
    assert out.count("Big.md") <= 2 and "Atlas.md" in out
