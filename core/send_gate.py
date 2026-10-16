"""
Nothing leaves the user's accounts until the user says so.

Seen live twice in one afternoon: the assistant typed into whatever had the
keyboard focus in Slack and pressed Enter — once into the search box, which
opened another channel, once into #qa's message box — and two messages went
to work channels nobody meant to write to. The check after the Enter even
said so ("the text in 'Message to qa' was submitted"), but by then it was
sent.

So sending is asked first, the way handing work to Claude Code / Codex is
(core/agents.py):

  1. The first call that would send — Enter in a message box, a click on a
     Send button, send_message — does NOT send. It registers exactly what
     would go where, puts a CONFIRM button on the HUD, and tells the model to
     ask the user and end its turn.
  2. The model calls again with confirmed=true. That parameter is only "this
     is the follow-up"; it is not the permission. The permission is that the
     user spoke or typed AFTER the assistant finished asking
     (core/activity.py) — the model cannot produce that by itself. What is
     sent is what was asked about: a different text, field or recipient is
     refused.
  3. Or the user presses CONFIRM on the HUD, which sends at once.

The gate cannot tell "yes" from "no" in every language — that part is the
model's judgement, as with the agent hand-off — but it guarantees the
question was asked and answered before anything is sent.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from core import activity, confirm

ASK_TTL = 120.0          # seconds a question about sending stays valid


@dataclass
class _Pending:
    what: str                     # "'Привіт' to John Smith on WhatsApp"
    key: tuple                    # what exactly was asked about
    run: Callable[[], str]        # does the sending, re-checking it is still right
    asked_at: float


_lock = threading.Lock()
_pending: Optional[_Pending] = None
_done: Optional[tuple] = None     # (key, result, at) — sent from the HUD button


def ask(what: str, key: tuple, run: Callable[[], str], tool: str) -> str:
    """Register a send and return what the model must do instead of sending."""
    global _pending, _done
    p = _Pending(what=what, key=key, run=run, asked_at=time.monotonic())
    with _lock:
        _pending, _done = p, None
    button = ""
    if not confirm.pending_title():
        confirm.request(key="send", title=f"Send {what}", detail="Nothing is sent until you confirm.",
                        run=lambda: _from_button(p))
        button = " They can also press CONFIRM on the HUD."
    return (f"[CONFIRM_SEND] Nothing was sent. Ask the user now, in ONE short sentence in "
            f"their language, whether to send {what}.{button} Then END YOUR TURN — their "
            f"spoken answer must come first. If they agree, call {tool} again with the "
            f"same arguments and confirmed=true. If they decline, drop it and say nothing "
            f"was sent.")


def _from_button(p: _Pending) -> str:
    global _pending, _done
    with _lock:
        if _pending is not p:
            return "That message is no longer waiting."
        _pending = None
    result = p.run()
    with _lock:
        _done = (p.key, result, time.monotonic())
    return result


def approve(key: tuple) -> Optional[str]:
    """None: go ahead and send now. Otherwise the sentence to return instead."""
    global _pending, _done
    with _lock:
        p, d = _pending, _done
    if d is not None and d[0] == key and time.monotonic() - d[2] < ASK_TTL:
        with _lock:
            _done = None
        return f"[ALREADY_SENT] The user confirmed it on the HUD and it was handled: {d[1]}"
    if p is None or time.monotonic() - p.asked_at > ASK_TTL:
        with _lock:
            _pending = None
        return ("[NOT_SENT] Nothing is waiting for the user's approval, so nothing was sent. "
                "Call again without confirmed to ask them first.")
    if p.key != key:
        return (f"[NOT_SENT] That is not what the user was asked about ({p.what}), so nothing "
                "was sent. Ask them again about this one.")
    if not activity.user_answered_since(p.asked_at):
        return ("[NOT_SENT] Nothing was sent: you called again before the user answered. Do "
                "NOT say it was sent. End your turn and wait for their reply.")
    with _lock:
        _pending = None
    confirm.dismiss("send")
    return None


def pending() -> Optional[str]:
    with _lock:
        p = _pending
    if p is None or time.monotonic() - p.asked_at > ASK_TTL:
        return None
    return p.what


def reset() -> None:
    """Tests only."""
    global _pending, _done
    with _lock:
        _pending = _done = None
