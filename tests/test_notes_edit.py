"""Editing notes by voice (memory/notes_edit.py, actions/notes.py): where the
text lands, which note is allowed, and undo that never overwrites later edits."""

import pytest

from actions import notes as notes_action
from core import undo
from memory import notes_edit as ne
from memory import notes_index as ni

TODO = (
    "# Work tasks\n\n[[Home]]\n\n## Recorded action\n\n- [ ] Review use cases.\n"
    "  - Completion: not yet confirmed.\n\n## Atlas — call preparation\n\n"
    "- [x] Prepare questions.\n\nCapture answers after the call.\n\n## Using this list\n\n"
    "Keep checkboxes here.\n"
)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "Vault"
    (v / "Work").mkdir(parents=True)
    (v / ".obsidian").mkdir()
    (v / "Work" / "ToDo list.md").write_text(TODO, encoding="utf-8")
    (v / "Рецепти.md").write_bytes("# Рецепти\r\n\r\nВареники.\r\n".encode("utf-8"))
    (v / "Work" / "Atlas notes.md").write_text("# Atlas\n", encoding="utf-8")
    (v / "Work" / "Atlas plan.md").write_text("# Plan\n", encoding="utf-8")
    monkeypatch.setattr(ni, "vault_path", lambda: v)
    monkeypatch.setattr(ni, "_last_scan", 0.0)
    monkeypatch.setattr(undo, "_stack", [])
    return v


def _todo(v):
    return (v / "Work" / "ToDo list.md").read_text(encoding="utf-8")


def test_append_under_a_heading_joins_the_checklist(vault):
    msg, _ = ne.append("To Do List", "- [ ] Call Marta about pricing.", heading="Recorded action")
    assert msg.startswith("Added to Work/ToDo list.md under 'Recorded action'")
    text = _todo(vault)
    assert ("  - Completion: not yet confirmed.\n- [ ] Call Marta about pricing.\n\n"
            "## Atlas — call preparation") in text


def test_append_to_a_section_ending_in_prose_keeps_a_blank_line(vault):
    ne.append("todo list", "- [ ] Send the estimate.", heading="atlas")
    assert "Capture answers after the call.\n\n- [ ] Send the estimate.\n\n## Using this list" in _todo(vault)


def test_append_without_heading_goes_to_the_end_and_says_where(vault):
    msg, _ = ne.append("Work/ToDo list.md", "Added at the end.")
    assert _todo(vault).endswith("Keep checkboxes here.\n\nAdded at the end.\n")
    assert "at the end, in section 'Using this list'" in msg


def test_the_note_title_heading_means_its_own_text_not_the_last_subsection(vault):
    """Seen live: tasks added under "Work tasks" (the H1) went to the end of
    the file, inside "Using this list"."""
    msg, _ = ne.append("ToDo list", "- [ ] New task.", heading="Work tasks")
    assert "# Work tasks\n\n[[Home]]\n\n- [ ] New task.\n\n## Recorded action" in _todo(vault)
    assert "under 'Work tasks'" in msg


def test_deleting_a_whole_line_leaves_no_blank_line(vault):
    ne.append("ToDo list", "- [ ] Temp.", heading="Recorded action")
    ne.replace("ToDo list", "- [ ] Temp.", "")
    assert _todo(vault) == TODO


def test_unknown_heading_lists_the_real_ones(vault):
    with pytest.raises(ne.EditError) as e:
        ne.append("ToDo list", "x", heading="Nope")
    assert "Recorded action" in str(e.value)
    assert _todo(vault) == TODO


def test_line_endings_are_kept(vault):
    ne.append("Рецепти", "Борщ.")
    raw = (vault / "Рецепти.md").read_bytes()
    assert b"\r\n\r\n\xd0\x91" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_a_vague_name_is_refused_not_guessed(vault):
    with pytest.raises(ne.EditError, match="More than one note"):
        ne.append("Atlas", "x")
    with pytest.raises(ne.EditError, match="No note is called"):
        ne.append("shopping", "x")
    assert ne.resolve_exact("Atlas plan", vault).name == "Atlas plan.md"


def test_replace_needs_exactly_one_match(vault):
    msg, _ = ne.replace("ToDo list", "Prepare questions.", "Prepare eight questions.")
    assert "Prepare eight questions." in _todo(vault) and msg.startswith("Changed")
    with pytest.raises(ne.EditError, match="not in the note"):
        ne.replace("ToDo list", "missing text", "x")
    with pytest.raises(ne.EditError, match="2 times"):
        ne.replace("ToDo list", "- [", "x")


def test_create_and_refuse_overwrite_or_escape(vault):
    msg, _ = ne.create("Ідеї: подарунки?", "- книга", folder="Personal")
    p = vault / "Personal" / "Ідеї  подарунки.md"
    assert msg == "Created Personal/Ідеї  подарунки.md." and p.read_text(encoding="utf-8") == "# Ідеї  подарунки\n\n- книга\n"
    with pytest.raises(ne.EditError, match="already exists"):
        ne.create("Ідеї: подарунки?", "x", folder="Personal")
    with pytest.raises(ne.EditError, match="inside the vault"):
        ne.create("x", folder="../outside")
    with pytest.raises(ne.EditError, match="inside the vault"):
        ne.create("x", folder=".obsidian")


def test_undo_restores_and_refuses_after_a_later_edit(vault):
    _, undo1 = ne.append("ToDo list", "- [ ] One.")
    assert undo1().startswith("Restored")
    assert _todo(vault) == TODO
    _, undo2 = ne.append("ToDo list", "- [ ] Two.")
    (vault / "Work" / "ToDo list.md").write_text(_todo(vault) + "typed in Obsidian\n", encoding="utf-8")
    assert "changed since" in undo2()
    assert "typed in Obsidian" in _todo(vault)


def test_undo_of_create_deletes_the_note(vault):
    _, u = ne.create("Temp", "hi")
    assert u().startswith("Deleted the new note")
    assert not (vault / "Temp.md").exists()


def test_edits_reach_the_index(vault):
    ne.append("ToDo list", "Унікальнеслово тут.")
    assert "Унікальнеслово" in ni.search("Унікальнеслово")


def test_notes_tool_edit_registers_undo(vault):
    out = notes_action.notes({"action": "append", "note": "ToDo list",
                              "text": "- [ ] Book the dentist.", "heading": "Recorded action"})
    assert out.endswith("'Undo' reverts it.")
    assert "Book the dentist" in _todo(vault)
    assert undo.undo_last().startswith("Undone:")
    assert _todo(vault) == TODO
    refused = notes_action.notes({"action": "append", "note": "Atlas", "text": "x"})
    assert refused.startswith("Nothing was changed: More than one note")
    assert notes_action.notes({"action": "create", "note": "New", "text": ""}).startswith("Created New.md")


def test_remove_drops_one_item_with_its_sub_items(vault):
    msg, u = ne.remove("ToDo list", "review USE cases")
    text = _todo(vault)
    assert "Review use cases" not in text and "Completion: not yet confirmed" not in text
    assert "(and 1 line(s) under it)" in msg
    assert "## Recorded action\n\n## Atlas — call preparation" in text
    assert u().startswith("Restored") and _todo(vault) == TODO


def test_remove_refuses_an_ambiguous_or_missing_line(vault):
    with pytest.raises(ne.EditError, match="lines contain"):
        ne.remove("ToDo list", "- [")
    with pytest.raises(ne.EditError, match="No line"):
        ne.remove("ToDo list", "dentist")
    assert _todo(vault) == TODO


def test_notes_tool_remove(vault):
    ne.append("ToDo list", "- [ ] подзвонити Марті", heading="Work tasks")
    out = notes_action.notes({"action": "remove", "note": "ToDo list", "text": "марті"})
    assert out.startswith("Removed from Work/ToDo list.md: - [ ] подзвонити Марті")
    assert _todo(vault) == TODO
