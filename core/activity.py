"""
core/activity.py — who spoke last, readable from any thread.

WHY THIS EXISTS
    Some decisions depend on the ORDER of the conversation, not its content:
    "did the user answer after the assistant asked?" (the agent hand-off will
    not start until they have) and "is anyone talking, or may a background
    result be spoken now?". Only main.py sees the audio and transcript streams,
    and action handlers run in executor threads with no reference to it, so
    main.py reports here and anything can read. The pattern is the
    rayhao-han/Mark-LV fork's core/activity.py.

    main.py reports:
        set_speaking(bool)   the assistant's voice started / stopped
        note_user_input(t)   a real user turn: their transcript or a typed command,
                             with its words, which memory checks a saved fact
                             against (did the user say this, or did the model
                             infer it?)

    Text the application injects itself (tool results, announcements) is not
    user input and must never be reported as it — otherwise the assistant could
    "answer" its own question.

COST
    Two short deques behind a lock.
"""

from __future__ import annotations

import threading
import time
from collections import deque

_lock = threading.Lock()
_speaking = False
# Monotonic timestamps, newest last. Only recent ordering matters.
_speech_ends: deque[float] = deque(maxlen=64)
_user_inputs: deque[float] = deque(maxlen=64)
# (monotonic time, text) of what the user said or typed. Transcription arrives
# in word-sized pieces, so recent_user_text() glues pieces that came close
# together back into utterances.
_user_words: deque[tuple[float, str]] = deque(maxlen=400)
_PIECE_GAP_S = 1.5


def set_speaking(value: bool) -> None:
    """The assistant's voice started or stopped. Only a True→False transition
    marks the end of an utterance."""
    global _speaking
    with _lock:
        if _speaking and not value:
            _speech_ends.append(time.monotonic())
        _speaking = bool(value)


def note_user_input(text: str = "", at: float | None = None) -> None:
    when = time.monotonic() if at is None else at
    with _lock:
        _user_inputs.append(when)
        if text and text.strip():
            _user_words.append((when, text.strip()))


def recent_user_text(seconds: float = 900.0, limit: int = 12) -> list[str]:
    """What the user said or typed in the last `seconds`, newest last, as up to
    `limit` utterances."""
    now = time.monotonic()
    with _lock:
        pieces = [(t, w) for t, w in _user_words if now - t <= seconds]
    out: list[list] = []
    for t, w in pieces:
        if out and t - out[-1][0] <= _PIECE_GAP_S:
            out[-1][0] = t
            out[-1][1] += " " + w
        else:
            out.append([t, w])
    return [w for _, w in out][-limit:]


def is_speaking() -> bool:
    with _lock:
        return _speaking


def last_user_input() -> float:
    with _lock:
        return _user_inputs[-1] if _user_inputs else 0.0


def user_answered_since(asked_at: float) -> bool:
    """True when the assistant finished speaking after `asked_at` (it asked)
    and the user said or typed something after that.

    Measured from the end of the assistant's speech rather than from the tool
    call: the transcript of the user's original request can arrive a moment
    after the model has already called the tool, and that late transcript must
    not count as the answer."""
    with _lock:
        ends = [e for e in _speech_ends if e >= asked_at]
        if not ends:
            return False
        first_end = ends[0]
        return any(u > first_end for u in _user_inputs)


def reset() -> None:
    """Tests only."""
    global _speaking
    with _lock:
        _speaking = False
        _speech_ends.clear()
        _user_inputs.clear()
        _user_words.clear()
