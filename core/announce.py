"""
core/announce.py — results that arrive after the conversation has moved on.

WHY THIS EXISTS
    A build that ran for two minutes, a push the user confirmed on the HUD, an
    agent that worked for ten: their outcome comes back when nobody is waiting
    for it. Speaking it at once would cut into whoever is talking, and a tool
    running in an executor thread has no way to reach the Live session anyway.
    So the result waits here, and main.py's announcer hands it to the session
    as an instruction once the room is quiet (`JarvisLive._run_announcer`).

    It is spoken while the assistant is asleep too, as one short sentence after
    which it stays asleep — the user asked to hear about their agent sessions
    by voice rather than read a tray notification. The tray is the fallback for
    an item that could not be said within a minute (no connection, a long
    conversation); see overdue().

KEYS
    An item may carry a key. A newer item with the same key replaces the older
    one still waiting, so a session that finished three times while the user
    was talking is one thing to hear, not three.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

# Older than this and the news is stale; dropped rather than read out late.
DEFAULT_MAX_AGE = 30 * 60.0


@dataclass
class Item:
    text: str                 # instruction for the model
    note: str = ""            # one line for the log and the tray
    key: str = ""
    max_age: float = DEFAULT_MAX_AGE
    at: float = field(default_factory=time.monotonic)
    notified: bool = False    # already shown in the tray


_lock = threading.Lock()
_items: list[Item] = []
_listeners: list[Callable[[Item], None]] = []


def say(text: str, note: str = "", key: str = "",
        max_age: float = DEFAULT_MAX_AGE) -> None:
    """Queue `text` for the model to say when the room is quiet."""
    item = Item(text=text, note=note, key=key, max_age=max_age)
    with _lock:
        if key:
            _items[:] = [i for i in _items if i.key != key]
        _items.append(item)
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(item)
        except Exception:
            pass        # a broken listener must not lose the announcement


def on_new(fn: Callable[[Item], None]) -> None:
    """Called (on the caller's thread) for every queued item — the UI uses it
    to show the note while the assistant cannot speak."""
    with _lock:
        _listeners.append(fn)


def pending() -> int:
    with _lock:
        _expire()
        return len(_items)


def take() -> list[Item]:
    """Everything still fresh, oldest first, removed from the queue."""
    with _lock:
        _expire()
        out, _items[:] = list(_items), []
        return out


def overdue(seconds: float) -> list[Item]:
    """Items still waiting after `seconds` that have not been shown yet; they
    are marked shown, so each one reaches the tray once."""
    now = time.monotonic()
    with _lock:
        _expire()
        out = [i for i in _items if not i.notified and now - i.at >= seconds]
        for i in out:
            i.notified = True
        return out


def _expire() -> None:
    now = time.monotonic()
    _items[:] = [i for i in _items if now - i.at <= i.max_age]


def combine(items: list[Item]) -> str:
    """One instruction for several items, so they are said in one breath
    instead of as a string of separate turns."""
    if len(items) == 1:
        return items[0].text
    parts = "\n\n".join(f"({n}) {i.text}" for n, i in enumerate(items, 1))
    return ("[SEVERAL_UPDATES] Several things finished while you were busy. "
            "Tell the user about all of them together, briefly, one short "
            "sentence each:\n\n" + parts)


def reset() -> None:
    """Tests only."""
    with _lock:
        _items.clear()
        _listeners.clear()
