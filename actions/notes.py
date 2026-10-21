"""
notes — search, read, open and edit the user's Obsidian notes.

The index, the vault choice and the wrapping of note text as data live in
memory/notes_index.py; edits in memory/notes_edit.py. This is the voice-facing
tool. Edits act at once and register their reversal with core/undo, like every
other state-changing action: "undo" puts the note back as it was.
"""

import os
import platform
import subprocess

from core.undo import push_undo
from memory import notes_edit, notes_index


def _launch(uri: str) -> None:
    system = platform.system()
    if system == "Windows":
        os.startfile(uri)                                   # noqa: S606
    elif system == "Darwin":
        subprocess.Popen(["open", uri])
    else:
        subprocess.Popen(["xdg-open", uri])


def notes(parameters: dict, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "search").strip().lower()
    query = str(p.get("query") or "").strip()
    note = str(p.get("note") or "").strip()
    try:
        if action in ("append", "create", "replace", "remove"):
            return _edit(action, p, note, player)
        if action == "read":
            return notes_index.read(note or query)
        if action == "open":
            msg, uri = notes_index.open_note(note or query)
            if uri:
                try:
                    _launch(uri)
                except OSError as e:
                    return f"Found the note, but Obsidian could not be opened: {e}"
            return msg
        if action == "status":
            s = notes_index.status()
            if not s["vault"]:
                return notes_index._no_vault()
            return (f"Notes vault {s['vault']}: {s['notes']} notes in {s['chunks']} "
                    f"passages, {s['embedded']} indexed by meaning.")
        if not query:
            return "Say what to look for in the notes."
        return notes_index.search(query)
    except Exception as e:
        return f"Could not reach the notes: {e}"


def _edit(action: str, p: dict, note: str, player) -> str:
    try:
        if action == "append":
            msg, undo = notes_edit.append(note, str(p.get("text") or ""),
                                          str(p.get("heading") or ""))
        elif action == "remove":
            msg, undo = notes_edit.remove(note, str(p.get("text") or p.get("old_text") or ""))
        elif action == "replace":
            msg, undo = notes_edit.replace(note, str(p.get("old_text") or ""),
                                           str(p.get("new_text") or ""))
        else:
            msg, undo = notes_edit.create(note, str(p.get("text") or ""),
                                          str(p.get("folder") or ""))
    except notes_edit.EditError as e:
        return f"Nothing was changed: {e}"
    push_undo(msg[:100], undo)
    if player is not None:
        try:
            player.write_log(f"JARVIS: {msg[:160]}")
        except Exception:
            pass
    return msg + " 'Undo' reverts it."


def _probe():
    s = notes_index.status()
    if not s["vault"]:
        return False, "no vault: Obsidian has none open and NOTES VAULT is not set"
    return True, f"{s['notes']} notes indexed from {s['vault']}"


TOOL = {
    "name": "notes",
    "description": (
        "The user's own notes (their Obsidian vault, 'second brain'): search, read, "
        "open and edit them. Use for 'what's in my notes about the Atlas project?', 'що в мене "
        "записано про JARVIS?', 'read my todo list', 'open my recipes note', 'add "
        "to my todo list…', 'запиши в нотатку…', 'create a note…'. "
        "action='search' (default) with query in any language returns the most "
        "relevant passages with the note and heading they come from; 'read' with "
        "note (title or path) returns its text; 'open' shows it in Obsidian; "
        "'append' adds text to the end of note, or of the section named by "
        "heading; 'create' makes a new note titled note (optional folder, text); "
        "'remove' deletes the one line containing text (a list item with its "
        "sub-items) — use it to delete or drop a task; 'replace' swaps old_text "
        "(exact, from a read) for new_text, e.g. to tick a task; 'status' says "
        "which vault is indexed. Edits need the note's exact title or path — search "
        "first if unsure. Write in the language and style of the note; a task in a "
        "checklist is '- [ ] task'. Edits apply at once and the undo tool reverts "
        "them. Answer from what the notes say and name the note. Not for files in "
        "general (file_controller), facts you remember about the user "
        "(recall_memory) or earlier conversations (recall_conversation)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "enum": ["search", "read", "open", "append", "create", "remove",
                                "replace", "status"],
                       "description": "search | read | open | append | create | remove | replace | status"},
            "query": {"type": "STRING", "description": "search: what to look for, any language"},
            "note": {"type": "STRING",
                     "description": "read/open/append/remove/replace: the note's title or path; create: the new title"},
            "text": {"type": "STRING",
                     "description": "append/create: the Markdown to write; remove: words from the line to delete"},
            "heading": {"type": "STRING", "description": "append: add at the end of this section"},
            "folder": {"type": "STRING", "description": "create: folder inside the vault (default: its root)"},
            "old_text": {"type": "STRING", "description": "replace: the exact passage now in the note"},
            "new_text": {"type": "STRING", "description": "replace: what to put instead"},
        },
    },
    "handler": notes,
    "probe": _probe,
}
