"""
actions/project_workspace.py — look inside the current project and make exact
edits to it.

FROM THE BlackStarG5/Mark-LIV FORK, AND WHAT CHANGED
    Kept: list, literal search, numbered reads, and an edit that replaces one
    exact piece of text — refused unless that text occurs exactly once and the
    file is unchanged since it was last read (a hash recorded at read time), so
    an edit made in the editor meanwhile is never overwritten. The write is
    atomic, and the edit goes on the undo stack, which refuses to undo over
    later changes. New files never overwrite an existing one.
    Changed:
      * The project is the current one (core/projects.py) — open sets it by
        name — instead of an absolute root on every call.
      * Answers are short text for the model, not JSON.
      * Files with keys or passwords are never read, searched or edited:
        whatever is read here goes to the model.
      * Listing follows .gitignore in a git repository (`git ls-files`).
      * A file with Windows line endings still matches an edit written with
        plain newlines.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

from core import jobs, projects

SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist",
             "build", ".idea", ".gradle", "target", ".next", ".cache"}
MAX_FILE_BYTES = 2_000_000
READ_LINES = 200
MAX_READ_LINES = 400
MAX_LIST = 300
MAX_HITS = 50

_reads: "OrderedDict[str, str]" = OrderedDict()     # path → sha256 at last read
_lock = threading.Lock()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _inside(root: Path, rel: str) -> Path:
    rel = str(rel or "").strip().strip('"')
    if not rel:
        raise ValueError("Say which file (a path inside the project).")
    path = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"{rel} is outside the project {root.name}.")
    if projects.is_secret(path):
        raise ValueError(f"{path.name} holds keys or passwords; I do not open such files.")
    return path


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _remember(path: Path, digest: str) -> None:
    with _lock:
        _reads[str(path)] = digest
        _reads.move_to_end(str(path))
        while len(_reads) > 256:
            _reads.popitem(last=False)


def _files(root: Path, sub: Path) -> list[Path]:
    """Project files under `sub`: git's view (respects .gitignore) when it is a
    repository, otherwise a walk that skips dependency and cache folders."""
    if (root / ".git").exists():
        try:
            done = subprocess.run(
                ["git", "-c", "core.quotepath=off", "ls-files", "--cached", "--others",
                 "--exclude-standard", "--", str(sub)],
                cwd=str(root), capture_output=True, timeout=15,
                stdin=subprocess.DEVNULL, **jobs.hidden_window_flags())
            if done.returncode == 0:
                out = []
                for line in jobs.decode(done.stdout).splitlines():
                    p = root / line
                    if line and p.is_file() and not projects.is_secret(p):
                        out.append(p)
                return out
        except Exception:
            pass
    out = []
    started = time.monotonic()
    for folder, dirs, names in os.walk(sub):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for n in sorted(names):
            p = Path(folder) / n
            if not projects.is_secret(p):
                out.append(p)
        if len(out) > 20_000 or time.monotonic() - started > 5:
            break
    return out


# ── actions ─────────────────────────────────────────────────────────────────

def _open(query: str) -> str:
    root, problem = projects.resolve(query)
    if root is None:
        return problem
    return projects.describe(root) + " It is now the current project."


def _list(root: Path, sub_rel: str, pattern: str) -> str:
    sub = _inside(root, sub_rel) if sub_rel else root
    if not sub.is_dir():
        return f"{sub_rel} is not a folder."
    files = _files(root, sub)
    if pattern:
        files = [f for f in files if fnmatch.fnmatch(f.name.lower(), pattern.lower())
                 or fnmatch.fnmatch(_rel(root, f).lower(), pattern.lower())]
    if not files:
        return "No files there." if not pattern else f"No files match {pattern}."
    shown = [_rel(root, f) for f in files[:MAX_LIST]]
    more = f"\n… and {len(files) - MAX_LIST} more" if len(files) > MAX_LIST else ""
    return f"{len(files)} file(s) in {root.name}/{sub_rel or ''}:\n" + "\n".join(shown) + more


def _search(root: Path, query: str, pattern: str) -> str:
    if not query:
        return "Say what text to search for."
    want = query.casefold()
    hits, scanned, partial = [], 0, False
    started = time.monotonic()
    for f in _files(root, root):
        if pattern and not fnmatch.fnmatch(f.name.lower(), pattern.lower()):
            continue
        scanned += 1
        if time.monotonic() - started > 8:
            partial = True
            break
        try:
            if f.stat().st_size > 1_000_000:
                continue
            raw = f.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:4096]:
            continue                                   # binary
        for n, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if want in line.casefold():
                hits.append(f"{_rel(root, f)}:{n}: {line.strip()[:200]}")
                if len(hits) >= MAX_HITS:
                    partial = True
                    break
        if partial:
            break
    if not hits:
        return f"'{query}' does not occur in {root.name} ({scanned} files searched)."
    tail = " (stopped early — narrow the search for more)" if partial else ""
    return f"{len(hits)} match(es) for '{query}'{tail}:\n" + "\n".join(hits)


def _read(root: Path, rel: str, line, count) -> str:
    path = _inside(root, rel)
    if not path.is_file():
        return f"{rel} does not exist in {root.name}."
    if path.stat().st_size > MAX_FILE_BYTES:
        return f"{rel} is larger than 2 MB; search inside it instead."
    raw = path.read_bytes()
    if b"\0" in raw[:4096]:
        return f"{rel} is a binary file."
    text = raw.decode("utf-8", errors="replace")
    _remember(path, _sha(raw))
    lines = text.splitlines()
    try:
        start = max(1, int(line or 1))
    except (TypeError, ValueError):
        start = 1
    try:
        n = max(1, min(MAX_READ_LINES, int(count or READ_LINES)))
    except (TypeError, ValueError):
        n = READ_LINES
    part = lines[start - 1:start - 1 + n]
    end = start + len(part) - 1
    body = "\n".join(f"{i}: {s}" for i, s in enumerate(part, start))
    more = (f"\n(more below — read again from line {end + 1})"
            if end < len(lines) else "")
    return f"{_rel(root, path)} — {len(lines)} lines, showing {start}–{end}:\n{body}{more}"


def _patch(root: Path, rel: str, old: str, new: str, player) -> str:
    path = _inside(root, rel)
    if not path.is_file():
        return f"{rel} does not exist; use create for a new file."
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        return f"{rel} is larger than 2 MB; I do not edit files that big."
    with _lock:
        seen = _reads.get(str(path))
    if seen is None:
        return f"Read {rel} first (action read), then edit it."
    if seen != _sha(raw):
        return f"{rel} changed since you last read it. Read it again before editing."
    if not old:
        return "old_text is required: the exact text to replace, copied from the file."
    text = raw.decode("utf-8", errors="strict") if _utf8(raw) else None
    if text is None:
        return f"{rel} is not UTF-8 text; I do not edit it."
    if text.count(old) == 0 and "\r\n" in text and "\r\n" not in old:
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    found = text.count(old)
    if found != 1:
        return (f"old_text occurs {found} times in {rel}; it must match exactly once. "
                "Include more surrounding lines." if found else
                f"old_text was not found in {rel}. Copy it exactly from a fresh read.")
    updated = text.replace(old, new, 1).encode("utf-8")
    _atomic_write(path, updated, raw)
    _remember(path, _sha(updated))

    from core.undo import push_undo

    def undo() -> str:
        if not path.exists() or path.read_bytes() != updated:
            return f"{path.name} changed after the edit, so I left it as it is."
        path.write_bytes(raw)
        _remember(path, _sha(raw))
        return f"Edit to {path.name} undone."
    push_undo(f"edit {path.name}", undo)
    line_no = text[:text.index(old)].count("\n") + 1
    delta = new.count("\n") - old.count("\n")
    if player is not None:
        try:
            player.write_log(f"SYS: ✎ {_rel(root, path)}:{line_no}")
        except Exception:
            pass
    return (f"Edited {_rel(root, path)} at line {line_no}"
            + (f" ({delta:+d} lines)" if delta else "") + ". It can be undone.")


def _utf8(raw: bytes) -> bool:
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _atomic_write(path: Path, data: bytes, expected: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".jarvis-edit-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if path.read_bytes() != expected:
            raise RuntimeError(f"{path.name} changed while I was editing; nothing replaced.")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _create(root: Path, rel: str, content: str, player) -> str:
    path = _inside(root, rel)
    if path.exists():
        return f"{rel} already exists. Read it and edit it instead."
    data = str(content or "").encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        return "That content is larger than 2 MB."
    made = [d for d in reversed(path.parents) if not d.exists()]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as fh:
        fh.write(data)
    _remember(path, _sha(data))

    from core.undo import push_undo

    def undo() -> str:
        if not path.exists() or path.read_bytes() != data:
            return f"{path.name} changed after it was created, so I kept it."
        path.unlink()
        for d in reversed(made):
            try:
                d.rmdir()
            except OSError:
                break
        return f"Removed {path.name} again."
    push_undo(f"create {path.name}", undo)
    if player is not None:
        try:
            player.write_log(f"SYS: + {_rel(root, path)}")
        except Exception:
            pass
    lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    return f"Created {_rel(root, path)} ({lines} lines). It can be undone."


def project_workspace(parameters: dict, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "info").strip().lower()
    query = str(p.get("project") or "").strip()
    if action == "open":
        return _open(query)
    root, problem = projects.resolve(query)
    if root is None:
        return problem
    root = root.resolve()
    try:
        if action == "info":
            return projects.describe(root)
        if action == "list":
            return _list(root, str(p.get("path") or ""), str(p.get("pattern") or ""))
        if action == "search":
            return _search(root, str(p.get("query") or "").strip(),
                           str(p.get("pattern") or ""))
        if action == "read":
            return _read(root, str(p.get("path") or ""), p.get("line"), p.get("count"))
        if action == "patch":
            return _patch(root, str(p.get("path") or ""), str(p.get("old_text") or ""),
                          str(p.get("new_text") if p.get("new_text") is not None else ""),
                          player)
        if action == "create":
            return _create(root, str(p.get("path") or ""), str(p.get("content") or ""), player)
    except (ValueError, RuntimeError, OSError) as e:
        return str(e)
    return "Unknown action. Use open, info, list, search, read, patch or create."


TOOL = {
    "name": "project_workspace",
    "description": (
        "Works inside an existing coding project. open sets the current project "
        "by name or folder ('open project Mark LIV') — the coding tools then use "
        "it without a path; info says what the project is. list shows its files "
        "(optionally a folder or a pattern like *.py), search finds text in them "
        "('where is the F9 key set?'), read shows a file with line numbers "
        "(200 lines at a time). patch replaces one exact piece of text "
        "(old_text, copied from a read, must occur exactly once) with new_text — "
        "always read the file first; create writes a NEW file. Edits and new "
        "files can be undone with the undo tool. Files with keys or passwords "
        "are never opened. For running tests or builds use run_command; for git "
        "use git_project; for a single new script from a description use "
        "code_helper; for a new project from scratch use dev_agent; for a large "
        "multi-file change use delegate_to_agent."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "description": "open | info | list | search | read | patch | create"},
            "project": {"type": "STRING",
                        "description": "Project name or folder; omit for the current project"},
            "path": {"type": "STRING",
                     "description": "File (read/patch/create) or folder (list), relative to the project"},
            "pattern": {"type": "STRING",
                        "description": "list/search: only files matching this, e.g. *.py"},
            "query": {"type": "STRING", "description": "search: the literal text to find"},
            "line": {"type": "INTEGER", "description": "read: first line (default 1)"},
            "count": {"type": "INTEGER", "description": "read: how many lines (default 200)"},
            "old_text": {"type": "STRING",
                         "description": "patch: the exact current text to replace"},
            "new_text": {"type": "STRING", "description": "patch: the replacement text"},
            "content": {"type": "STRING", "description": "create: the new file's content"},
        },
    },
    "handler": project_workspace,
}
