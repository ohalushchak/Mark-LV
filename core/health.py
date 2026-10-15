"""
core/health.py — what broke, where, and how often.

WHY
    A tool that crashed used to leave a traceback in the console and nothing
    else: the console scrolls away, nobody reads it, and the next time the
    same thing breaks nobody knows it broke yesterday too. Self-repair
    (core/self_repair.py) cannot fix what was never written down, and "what is
    broken?" had no answer.

    Two things are kept here, after the klcenka9/Mark-L fork (per-tool health)
    and the syamsulmuiin/Mark fork (an error journal with secrets redacted):

      * A journal, ~/.jarvis/health/errors.jsonl: one line per failure, with
        the traceback, the code version it happened on, and anything that
        looks like a key, a token, an e-mail address or the user's home
        folder replaced. It is outside the repository on purpose — it holds
        the user's own paths and arguments, and the repo is public.
      * A window of the last 20 calls per tool, in memory only, turned into a
        green / yellow / red status. In memory because a restart is usually
        what follows a fix: yesterday's failures should not keep a repaired
        tool red.

WHAT COUNTS AS A FAILURE
    Handlers catch their own errors and return them as a spoken sentence, so
    an exception reaching the loader is the rare case. Three kinds are
    recorded:
      crash     — an exception escaped the handler;
      reported  — the handler called health.failed(why), or its result starts
                  the way a failure does ("Error: …", "Tool 'x' failed: …");
      args      — the call did not match the tool's schema (core/tool_args).
    Plus background failures that are not tool calls: an uncaught exception in
    a thread or an asyncio task. Those go to the journal, not into a tool's
    status.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

DIR = Path.home() / ".jarvis" / "health"
JOURNAL = DIR / "errors.jsonl"
MAX_BYTES = 1_000_000          # then the journal rotates to errors.jsonl.1
WINDOW = 20                    # calls per tool that make up its status
RED_MIN_CALLS = 5              # klcenka9's thresholds: >20% of at least 5 calls
RED_RATE = 0.20
DEDUPE_SECONDS = 60.0          # the same error again within a minute is counted, not rewritten

_REPO = Path(__file__).resolve().parent.parent

_lock = threading.Lock()
_calls: dict[str, deque] = {}            # tool → deque[(ok, monotonic, kind)]
_last_written: dict[str, float] = {}     # error id → monotonic of last journal line
_listeners: list[Callable[[], None]] = []
_local = threading.local()
_version: str | None = None


# ── redaction ───────────────────────────────────────────────────────────────

_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "<key>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "<key>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "<token>"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "<token>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer <token>"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)(\s*[=:]\s*)['\"]?[^\s'\",]{6,}"),
     r"\1\2<redacted>"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "<email>"),
]
_secret_values: list[str] | None = None


def _config_secrets() -> list[str]:
    """Values of config entries whose name says they are secret, longest first,
    so a key that matches none of the patterns above is still hidden."""
    global _secret_values
    if _secret_values is None:
        found: list[str] = []
        try:
            from memory.config_manager import load_api_keys

            def walk(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if isinstance(v, str) and len(v) >= 12 and re.search(
                                r"(?i)key|token|secret|password|passwd", str(k)):
                            found.append(v)
                        else:
                            walk(v)
                elif isinstance(obj, list):
                    for v in obj:
                        walk(v)
            walk(load_api_keys())
        except Exception:
            pass
        _secret_values = sorted(set(found), key=len, reverse=True)
    return _secret_values


def redact(text) -> str:
    s = str(text or "")
    for value in _config_secrets():
        s = s.replace(value, "<redacted>")
    for rx, sub in _PATTERNS:
        s = rx.sub(sub, s)
    home = str(Path.home())
    if len(home) > 3:
        s = re.sub(re.escape(home), "~", s, flags=re.IGNORECASE)
        s = re.sub(re.escape(home.replace("\\", "/")), "~", s, flags=re.IGNORECASE)
    return s


# ── the journal ─────────────────────────────────────────────────────────────

def version() -> str:
    """Short git HEAD of the running code, '' outside a checkout."""
    global _version
    if _version is None:
        try:
            r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_REPO,
                               capture_output=True, text=True, timeout=5,
                               **_no_window())
            _version = r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            _version = ""
    return _version


def _no_window() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _where(exc: BaseException | None) -> str:
    """file:line of the innermost frame in this repository — the part of a
    traceback that says which code to look at."""
    if exc is None or exc.__traceback__ is None:
        return ""
    best = ""
    for fr in traceback.extract_tb(exc.__traceback__):
        try:
            rel = Path(fr.filename).resolve().relative_to(_REPO)
        except (ValueError, OSError):
            continue
        best = f"{rel.as_posix()}:{fr.lineno} in {fr.name}"
    return best


def _error_id(kind: str, tool: str, message: str, where: str, exc_type: str) -> str:
    # Digits and quoted values change between occurrences of the same bug
    # (line numbers stay in `where`), so they are left out of the identity.
    gist = re.sub(r"\d+", "#", re.sub(r"'[^']*'|\"[^\"]*\"", "…", message))[:80]
    sig = "|".join((kind, tool, exc_type, where or gist))
    return hashlib.sha1(sig.encode("utf-8", "replace")).hexdigest()[:8]


def _write(entry: dict) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        if JOURNAL.exists() and JOURNAL.stat().st_size > MAX_BYTES:
            JOURNAL.replace(JOURNAL.with_suffix(".jsonl.1"))
        with JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:                                   # pragma: no cover
        print(f"[Health] could not write the journal: {e}")


def log_error(kind: str, message: str, tool: str = "", exc: BaseException | None = None,
              args: dict | None = None) -> str:
    """Write one failure to the journal. Returns its id. Never raises."""
    try:
        exc_type = type(exc).__name__ if exc is not None else ""
        where = _where(exc)
        message = redact(message)[:500]
        err_id = _error_id(kind, tool, message, where, exc_type)
        now = time.monotonic()
        with _lock:
            last = _last_written.get(err_id, -1e9)
            if now - last < DEDUPE_SECONDS:
                return err_id
            _last_written[err_id] = now
        trace = ""
        if exc is not None and exc.__traceback__ is not None:
            trace = redact("".join(traceback.format_exception(type(exc), exc,
                                                              exc.__traceback__)))[-4000:]
        entry = {"id": err_id, "at": datetime.now().isoformat(timespec="seconds"),
                 "kind": kind, "tool": tool, "message": message, "where": where,
                 "type": exc_type, "trace": trace, "version": version()}
        if args:
            entry["args"] = redact(json.dumps(args, ensure_ascii=False, default=str))[:300]
        _write(entry)
        return err_id
    except Exception:                                        # pragma: no cover
        return ""


def recent(limit: int = 20, current_version_only: bool = False) -> list[dict]:
    """Newest first, one line per distinct error id (with how often it occurred)."""
    lines: list[str] = []
    for path in (JOURNAL.with_suffix(".jsonl.1"), JOURNAL):
        try:
            lines += path.read_text(encoding="utf-8").splitlines()
        except OSError:
            pass
    seen: dict[str, dict] = {}
    order: list[str] = []
    v = version()
    for raw in reversed(lines):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if current_version_only and v and e.get("version") != v:
            continue
        eid = e.get("id", "")
        if eid in seen:
            seen[eid]["count"] = seen[eid].get("count", 1) + 1
            continue
        e["count"] = 1
        seen[eid] = e
        order.append(eid)
    return [seen[i] for i in order[:limit]]


def get(err_id: str) -> dict | None:
    err_id = str(err_id or "").strip().lower()
    if not err_id:
        return None
    for e in recent(limit=500):
        if e.get("id", "").startswith(err_id):
            return e
    return None


# ── per-tool status ─────────────────────────────────────────────────────────

_FAILED_RE = re.compile(
    r"^\s*(\[?error\]?[:\s]|failed\b|tool '[^']+' failed|sir, the '[^']+' plugin failed)",
    re.IGNORECASE)


def looks_failed(result) -> bool:
    return isinstance(result, str) and bool(_FAILED_RE.match(result))


def begin_call() -> None:
    """Called by the loaders right before a handler runs, on its thread."""
    _local.reported = ""


def failed(why) -> None:
    """For handlers: 'this call failed', even though a sentence is returned.

        except Exception as e:
            health.failed(e)
            return f"Could not read the file: {e}"
    """
    _local.reported = str(why or "failed")[:500]


def end_call() -> str:
    why = getattr(_local, "reported", "")
    _local.reported = ""
    return why


def record(tool: str, ok: bool, kind: str = "", message: str = "",
           exc: BaseException | None = None, args: dict | None = None) -> None:
    """One call's outcome. Failures also go to the journal. Never raises."""
    try:
        with _lock:
            q = _calls.setdefault(tool, deque(maxlen=WINDOW))
            q.append((bool(ok), time.monotonic(), kind if not ok else ""))
        if not ok:
            log_error(kind or "reported", message, tool=tool, exc=exc, args=args)
        _changed()
    except Exception:                                        # pragma: no cover
        pass


def record_result(tool: str, result, args: dict | None = None) -> None:
    """For a handler that returned: decide from health.failed() and the text."""
    why = end_call()
    if why:
        record(tool, False, "reported", why, args=args)
    elif looks_failed(result):
        record(tool, False, "reported", str(result), args=args)
    else:
        record(tool, True)


def status(tool: str) -> str:
    """'ok', 'warn' (a recent failure), 'bad' (klcenka9's red), '' (never called)."""
    with _lock:
        q = list(_calls.get(tool, ()))
    if not q:
        return ""
    fails = sum(1 for ok, _, _ in q if not ok)
    if len(q) >= RED_MIN_CALLS and fails / len(q) > RED_RATE:
        return "bad"
    if fails:
        return "warn"
    return "ok"


def summary() -> dict:
    with _lock:
        tools = list(_calls)
    by = {"ok": [], "warn": [], "bad": []}
    for t in tools:
        s = status(t)
        if s:
            by[s].append(t)
    errs = recent(limit=50, current_version_only=True)
    return {"bad": sorted(by["bad"]), "warn": sorted(by["warn"]), "ok": len(by["ok"]),
            "errors": len(errs), "version": version(),
            "latest": errs[0] if errs else None}


def describe_tool(tool: str) -> str:
    with _lock:
        q = list(_calls.get(tool, ()))
    fails = [k for ok, _, k in q if not ok]
    return f"{tool}: {len(fails)} of the last {len(q)} calls failed"


# ── background failures ─────────────────────────────────────────────────────

def install_hooks() -> None:
    """Uncaught exceptions in the main thread and in worker threads go to the
    journal as well as to the console, where they went before."""
    prev_sys = sys.excepthook
    prev_thread = threading.excepthook

    def _sys_hook(tp, value, tb):
        if value is not None and not isinstance(value, KeyboardInterrupt):
            log_error("thread", f"{tp.__name__}: {value}", exc=value)
        prev_sys(tp, value, tb)

    def _thread_hook(a):
        if a.exc_value is not None and not isinstance(a.exc_value, SystemExit):
            name = getattr(a.thread, "name", "") or "thread"
            log_error("thread", f"[{name}] {a.exc_type.__name__}: {a.exc_value}",
                      exc=a.exc_value)
        prev_thread(a)

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


def asyncio_handler(loop, context: dict) -> None:
    """For loop.set_exception_handler(): an asyncio task died with nobody
    awaiting it."""
    exc = context.get("exception")
    msg = context.get("message") or (repr(exc) if exc else "asyncio error")
    if exc is not None:
        log_error("asyncio", f"{msg}: {type(exc).__name__}: {exc}", exc=exc)
    loop.default_exception_handler(context)


# ── listeners ───────────────────────────────────────────────────────────────

def on_change(fn: Callable[[], None]) -> None:
    _listeners.append(fn)


def _changed() -> None:
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


def reset() -> None:
    """Tests only."""
    global _secret_values, _version
    with _lock:
        _calls.clear()
        _last_written.clear()
    _secret_values = None
    _local.reported = ""
