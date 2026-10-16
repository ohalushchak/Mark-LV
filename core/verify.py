"""
Did the action actually happen? — the verdict a tool's result carries.

A handler's sentence says what it *tried*: "Opened Notepad.", "Clicked (812,
430)". The model passed that on as fact, so the user heard "done" for an app
that never started and a click that landed on nothing. Now an action may
declare, beside its handler, how to look at the computer before and after:

    TOOL = {..., "snapshot": before_fn, "verify": verify_fn}

    before_fn(parameters) -> anything          # optional; runs first
    verify_fn(parameters, result, before) -> Verdict | None

``core/action_loader.py`` runs them around the handler and appends the verdict
to the result, so the model reads the evidence in the same tool response:

    [VERIFIED] what was seen
    [UNVERIFIED] why it could not be seen — and not to claim it happened
    [FAILED] what was seen instead — and not to claim success

``None`` means "nothing to check for this call" (a read, a pending
confirmation) and the result goes out as it is. A handler that already
reported a failure is not checked again.

Three states, not two: an effect that cannot be observed (no UIA value in a
rich editor, a setting with no read-back on this OS) is UNVERIFIED, never
VERIFIED and never FAILED. Mixed from bfrpaulondev/Mark-BP (before/after
postconditions, "can claim success" only with evidence) and
syamsulmuiin/Mark (anything without evidence is unverified, not success).
bfrpaulondev checked immediately after the handler and got "unverified" for
every slow repaint; `poll` re-looks for a moment before giving up.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Optional

VERIFIED = "verified"
UNVERIFIED = "unverified"
FAILED = "failed"

# How long an effect gets to show before the verdict is final. Measured on
# Windows: a click's repaint lands in 50–300 ms, a focus switch in ~100 ms,
# Notepad's first window ~0.6 s after Popen. Apps get more (open_app's own
# timeout); everything else stays within one short breath.
SETTLE_S = 1.2


@dataclass(frozen=True)
class Verdict:
    state: str
    detail: str            # one short sentence: the evidence, or why there is none

    @property
    def ok(self) -> bool:
        return self.state == VERIFIED


def verified(detail: str) -> Verdict:
    return Verdict(VERIFIED, detail)


def unverified(detail: str) -> Verdict:
    return Verdict(UNVERIFIED, detail)


def failed(detail: str) -> Verdict:
    return Verdict(FAILED, detail)


def poll(check: Callable[[], Optional[Verdict]], timeout: float = SETTLE_S,
         interval: float = 0.15) -> Optional[Verdict]:
    """Run `check` until it says VERIFIED or `timeout` runs out; the last
    verdict wins. A check that raises counts as "could not look"."""
    deadline = time.monotonic() + max(0.0, timeout)
    last: Optional[Verdict] = None
    while True:
        try:
            last = check()
        except Exception as e:
            last = unverified(f"the check itself failed ({type(e).__name__}: {e})")
        if last is None or last.state == VERIFIED or time.monotonic() >= deadline:
            return last
        time.sleep(interval)


def snapshot(fn: Optional[Callable], parameters: dict) -> Any:
    """The "before" picture; None when there is none or taking it failed."""
    if fn is None:
        return None
    try:
        return fn(parameters)
    except Exception:
        traceback.print_exc()
        return None


# Results that say nothing was done yet — waiting for the user — so there is
# no effect to look for.
WAITING = ("[CONFIRM_SEND]", "[NOT_SENT]", "[ALREADY_SENT]", "[CONFIRMATION_PENDING]")


def check(fn: Optional[Callable], parameters: dict, result: Any, before: Any) -> Optional[Verdict]:
    """The verdict for one call; a crash in the check is an UNVERIFIED
    verdict, never an exception into the tool call."""
    if fn is None or (isinstance(result, str) and result.lstrip().startswith(WAITING)):
        return None
    try:
        v = fn(parameters, result, before)
    except Exception as e:
        traceback.print_exc()
        return unverified(f"the check itself failed ({type(e).__name__}: {e})")
    if v is not None and not isinstance(v, Verdict):
        return None
    return v


_TAIL = {
    VERIFIED: "",
    UNVERIFIED: (" Say what was done, and that you could not confirm the effect; "
                 "do not claim it happened."),
    FAILED: " It did not take effect: tell the user, do not claim success.",
}


def tag(result: str, v: Optional[Verdict]) -> str:
    """The result as the model reads it, verdict last."""
    if v is None:
        return result
    line = f"[{v.state.upper()}] {v.detail.rstrip('.')}.{_TAIL.get(v.state, '')}"
    return f"{result.rstrip()}\n{line}" if result else line
