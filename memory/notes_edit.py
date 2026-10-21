"""
memory/notes_edit.py — change the user's notes by voice: add to a note, create
one, or replace an exact passage.

THE FLOW
    Nathan123y's Obsidian flow, adapted: every edit is written atomically
    (temp file, then os.replace, so Obsidian never sees half a file), read back,
    and registered with a reversal (actions/notes.py hands it to core/undo).
    Following the convention for state-changing actions it acts at once rather
    than asking first — "undo" puts the previous text back. A reversal refuses
    to run if the note changed since the edit, so undo never throws away
    something the user typed in Obsidian afterwards.

FINDING THE NOTE TO CHANGE
    Reading may use the forgiving search of notes_index._resolve; writing does
    not. A write goes to the note named by its path or exact title ("ToDo
    list"), or to the one note whose title holds every word asked for. Anything
    vaguer is refused with the candidates, because appending to the wrong note
    — the nearest match by meaning — is worse than asking.

ADDING UNDER A HEADING
    With `heading`, the text goes to the end of that heading's OWN text —
    before the next heading of any level — after its last line, with list
    items kept together (a new "- [ ] task" lands right under the others).
    Seen live: asked to add tasks under "Work tasks", the title of the whole
    to-do note, the first version went to the end of everything below it,
    which put them inside the last subsection ("Using this list"). Without a
    heading the text goes to the end of the note, and the result names the
    section it landed in, so a wrong place is heard at once.
    Line endings follow the file (CRLF stays CRLF).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from memory import notes_index as _ni
from memory import store as _store
from memory.search import tokens

MAX_TEXT = 5000
_BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]+')
_LIST = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)")


class EditError(Exception):
    """A refusal to say to the user, not a crash."""


# ── Which note ────────────────────────────────────────────────────────────────

def _vault() -> Path:
    v = _ni.vault_path()
    if v is None:
        raise EditError(_ni._no_vault())
    return v


def _inside(p: Path, vault: Path) -> bool:
    try:
        rp, rv = p.resolve(), vault.resolve()
    except OSError:
        return False
    return rp.is_relative_to(rv) and ".obsidian" not in rp.relative_to(rv).parts


def resolve_exact(name: str, vault: Path) -> Path:
    name = (name or "").strip().strip("[]").split("|")[0].strip()
    if not name:
        raise EditError("Say which note to change.")
    cand = vault / name
    if cand.suffix.lower() != ".md":
        cand = cand.with_name(cand.name + ".md")
    if cand.is_file() and _inside(cand, vault):
        return cand.resolve()
    _ni.rescan()
    rows = _store.get().all("SELECT path, title FROM notes")
    # Spacing and punctuation do not count: "To Do List" is the note "ToDo list".
    def squash(t: str) -> str:
        return re.sub(r"[\W_]+", "", t.casefold())
    stem = squash(Path(name).stem)
    exact = [r for r in rows if squash(r["title"]) == stem]
    if len(exact) == 1:
        return (vault / exact[0]["path"]).resolve()
    want = [t for t in tokens(name) if len(t) > 1]
    holding = [r for r in rows if want and all(
        any(w2.startswith(w) or w.startswith(w2) for w2 in tokens(r["title"])) for w in want)]
    pool = exact or holding
    if len(pool) == 1:
        return (vault / pool[0]["path"]).resolve()
    if pool:
        names = "; ".join(r["path"] for r in pool[:6])
        raise EditError(f"More than one note matches '{name}': {names}. Say which one.")
    raise EditError(f"No note is called '{name}'. Notes are changed only by their exact "
                    f"title or path; notes search finds the right one.")


# ── Writing ───────────────────────────────────────────────────────────────────

def _read(p: Path) -> tuple[str, str]:
    raw = p.read_bytes().decode("utf-8", errors="replace")
    nl = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), nl


def _write(p: Path, text: str, nl: str) -> bytes:
    data = text.replace("\n", nl).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".jarvis-", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if p.read_bytes() != data:
        raise EditError(f"The note {p.name} did not read back as written.")
    return data


def _reverser(p: Path, written: bytes, before: bytes | None, rel: str):
    """A callable for core/undo: restore `before` (or delete a created note) —
    only if the note still holds exactly what this edit wrote."""
    def undo() -> str:
        try:
            now = p.read_bytes() if p.exists() else None
        except OSError as e:
            return f"Could not read {rel} to undo: {e}"
        if now != written:
            return f"{rel} has changed since, so it was left as it is."
        if before is None:
            p.unlink()
            msg = f"Deleted the new note {rel}"
        else:
            tmp = p.with_name(p.name + ".jarvis-undo")
            tmp.write_bytes(before)
            os.replace(tmp, p)
            msg = f"Restored {rel} to how it was"
        _ni.rescan(force=True)
        return msg
    return undo


def _clean_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").strip("\n")
    if not text.strip():
        raise EditError("Say what to write.")
    if len(text) > MAX_TEXT:
        raise EditError(f"That is {len(text)} characters; a voice edit is limited to {MAX_TEXT}.")
    return text


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line index, level, title) of every heading outside code fences."""
    out, fenced = [], False
    for i, line in enumerate(lines):
        if _ni._FENCE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else _ni._HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def _insert(body: str, text: str, heading: str) -> tuple[str, str]:
    """The new body, and the heading of the section the text landed in."""
    lines = body.split("\n")
    end = len(lines)
    hs = _headings(lines)
    if heading:
        want = heading.strip().lstrip("#").strip().casefold()
        found = ([h for h in hs if h[2].casefold() == want]
                 or [h for h in hs if want and want in h[2].casefold()])
        if not found:
            names = "; ".join(h[2] for h in hs[:10]) or "none"
            raise EditError(f"No heading '{heading}' in that note. Its headings: {names}.")
        idx = found[0][0]
        end = next((i for i, _lv, _t in hs if i > idx), len(lines))
    pos = end
    while pos > 0 and not lines[pos - 1].strip():
        pos -= 1
    new = text.split("\n")
    prev = lines[pos - 1] if pos > 0 else ""
    glue = [] if (pos == 0 or (_LIST.match(prev) and _LIST.match(new[0]))) else [""]
    after = lines[pos:]
    tail = [""] if any(l.strip() for l in after) else []
    while after and not after[0].strip():
        after = after[1:]
    out = lines[:pos] + glue + new + tail + after
    landed = next((t for i, _lv, t in reversed(hs) if i < pos), "")
    return "\n".join(out).rstrip("\n") + "\n", landed


def append(name: str, text: str, heading: str = ""):
    """Add `text` at the end of a note or of one of its sections.
    Returns (message, undo callable)."""
    vault = _vault()
    p = resolve_exact(name, vault)
    text = _clean_text(text)
    before = p.read_bytes()
    body, nl = _read(p)
    new_body, landed = _insert(body, text, heading)
    written = _write(p, new_body, nl)
    rel = p.relative_to(vault.resolve()).as_posix()
    _ni.rescan(force=True)
    if heading:
        where = f" under '{landed}'"
    else:
        where = f" at the end, in section '{landed}'" if landed else " at the end"
    return f"Added to {rel}{where}: {text[:200]}", _reverser(p, written, before, rel)


def replace(name: str, old: str, new: str):
    """Replace one exact passage. Returns (message, undo callable)."""
    vault = _vault()
    p = resolve_exact(name, vault)
    if not (old or "").strip():
        raise EditError("Say the exact text to replace.")
    body, nl = _read(p)
    n = body.count(old)
    if n != 1:
        raise EditError(f"That text occurs {n} times in the note; it must occur exactly once."
                        if n else "That exact text is not in the note — read it first.")
    before = p.read_bytes()
    at = body.index(old)
    after = body[at + len(old):at + len(old) + 1]
    if not new and (at == 0 or body[at - 1] == "\n") and after in ("\n", ""):
        # Deleting whole lines takes their line break too, so a removed list
        # item leaves no blank line inside the list.
        changed = body[:at] + body[at + len(old) + len(after):]
    else:
        changed = body.replace(old, new or "", 1)
    written = _write(p, changed, nl)
    rel = p.relative_to(vault.resolve()).as_posix()
    _ni.rescan(force=True)
    return f"Changed {rel}: '{old[:80]}' → '{(new or '')[:80]}'", _reverser(p, written, before, rel)


def remove(name: str, words: str):
    """Delete the one line that contains `words` (any case), with the lines
    indented under it — a list item and its sub-items. Returns (message, undo
    callable).

    Seen live: to drop "- [ ] подзвонити Габріелю" the model had to guess the
    exact text for replace, fail, read the note and try again — three calls
    for "delete that item". Here the item's own words are enough."""
    vault = _vault()
    p = resolve_exact(name, vault)
    want = " ".join((words or "").split()).casefold()
    if not want:
        raise EditError("Say which line to remove.")
    body, nl = _read(p)
    lines = body.split("\n")
    hits = [i for i, l in enumerate(lines) if want in " ".join(l.split()).casefold()]
    if len(hits) != 1:
        if not hits:
            raise EditError(f"No line in the note contains '{words}'.")
        shown = "; ".join(lines[i].strip()[:80] for i in hits[:5])
        raise EditError(f"{len(hits)} lines contain '{words}': {shown}. Say more of the line.")
    i = hits[0]
    indent = len(lines[i]) - len(lines[i].lstrip())
    j = i + 1
    while j < len(lines) and lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) > indent:
        j += 1
    gone = lines[i:j]
    rest = lines[:i] + lines[j:]
    # An item that stood alone between blank lines leaves one blank, not two.
    if 0 < i < len(rest) and not rest[i - 1].strip() and not rest[i].strip():
        del rest[i]
    before = p.read_bytes()
    written = _write(p, "\n".join(rest), nl)
    rel = p.relative_to(vault.resolve()).as_posix()
    _ni.rescan(force=True)
    extra = f" (and {len(gone) - 1} line(s) under it)" if len(gone) > 1 else ""
    return (f"Removed from {rel}: {gone[0].strip()[:160]}{extra}",
            _reverser(p, written, before, rel))


def create(title: str, text: str = "", folder: str = ""):
    """A new note. Returns (message, undo callable)."""
    vault = _vault()
    title = _BAD_NAME.sub(" ", (title or "")).strip().strip(".")
    if not title:
        raise EditError("Say what to call the note.")
    base = vault / folder.strip().strip("/\\") if folder.strip() else vault
    p = base / f"{title}.md"
    if not _inside(p, vault):
        raise EditError("Notes can only be created inside the vault.")
    if p.exists():
        raise EditError(f"A note called '{title}' already exists there; add to it instead.")
    body = (text or "").replace("\r\n", "\n").strip("\n")
    if len(body) > MAX_TEXT:
        raise EditError(f"That is {len(body)} characters; a voice edit is limited to {MAX_TEXT}.")
    content = body + "\n" if body.startswith("#") else f"# {title}\n" + (f"\n{body}\n" if body else "")
    p.parent.mkdir(parents=True, exist_ok=True)
    written = _write(p, content, "\n")
    rel = p.relative_to(vault.resolve()).as_posix()
    _ni.rescan(force=True)
    return f"Created {rel}.", _reverser(p, written, None, rel)
