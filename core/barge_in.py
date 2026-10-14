"""
Interrupting the assistant by voice while it is still speaking.

What it decides
---------------
While JARVIS talks, the microphone is not streamed to the model — on speakers
it would hear itself and answer its own sentence. It is still listened to
locally, block by block, and this module decides when what it hears is the
user talking over the answer rather than the answer coming back through the
room.

The per-block judgement is not made here. `core.echo.EchoGuard` already tells
a different voice apart from our own echo and calibrates itself to the room;
this adds what a single block cannot know:

* **Sustained evidence.** One block that sounds like a voice is a cough, a
  keystroke or a door. The guard says how many blocks in a row it needs before
  it believes an interruption — ~320 ms in a normal room, ~770 ms in one where
  echo and voice are hard to separate — and a single block of doubt starts the
  count again.
* **A cooldown.** After an interruption nothing can trigger another for a
  couple of seconds, so a user still finishing their sentence over the start of
  the next answer cannot set off a ping-pong of interruptions.
* **The words themselves.** Every block heard while deciding is kept, plus a
  little from just before the evidence began — the quiet onset of a word sits
  below the guard's bar. After the interruption that audio is handed back, so
  the model hears the whole phrase and the user does not have to repeat it.

Thread safety
-------------
`feed()` runs in the audio callback thread; `take_audio()` and `reset()` run on
the event loop. One small lock guards the shared state; nothing in here blocks
for longer than one FFT.
"""

from __future__ import annotations

import threading
import time
from collections import deque

# No second interruption within this long of the last one.
_COOLDOWN_S = 2.0

# Blocks kept from before the evidence started (~128 ms at 1024 samples / 16
# kHz). Enough for the onset of a word; short enough that our own echo from
# just before it is barely there.
_PREROLL_BLOCKS = 2

# Upper bound on the audio handed back after an interruption.
_MAX_AUDIO_S = 4.0

# The most blocks the guard can ever ask for (EchoGuard._BLOCKS_NOISY), with
# headroom. Before a trigger the buffer only needs to hold the current run.
_MAX_STREAK = 16


class BargeIn:
    """Decides, block by block, whether the user is talking over the assistant.

    Usage, per stretch of assistant speech:

    * `feed()` every microphone block from the audio thread. It returns True
      exactly once, on the block that settles it.
    * `take_audio()` from the event loop when acting on that: the user's speech
      so far, as raw PCM bytes. After it, `handed_off` is True and the caller
      streams the microphone normally again.
    * `reset()` when a new stretch of speech begins.
    """

    def __init__(self, guard, sample_rate: int, block_size: int,
                 cooldown_s: float = _COOLDOWN_S,
                 preroll_blocks: int = _PREROLL_BLOCKS,
                 max_audio_s: float = _MAX_AUDIO_S) -> None:
        self._guard = guard
        self._sr = int(sample_rate)
        self._cooldown = float(cooldown_s)
        self._preroll = max(0, int(preroll_blocks))
        self._max_blocks = max(self._preroll + _MAX_STREAK,
                               int(max_audio_s * sample_rate / max(1, block_size)))
        self._lock = threading.Lock()
        self._buf: deque[bytes] = deque(maxlen=self._preroll + _MAX_STREAK)
        self._streak = 0
        self._triggered = False
        self._handed_off = False
        self._last_trigger = float("-inf")
        # Per-stretch diagnostics: what was heard, for one line in the console.
        self._blocks = 0
        self._voiced = 0
        self._best_run = 0
        self._need = 0

    # ── state, for the caller and for tests ─────────────────────────────────

    @property
    def triggered(self) -> bool:
        """An interruption has been decided in the current stretch."""
        return self._triggered

    @property
    def handed_off(self) -> bool:
        """The decided interruption has been acted on; stream normally."""
        return self._handed_off

    # ── entry points ────────────────────────────────────────────────────────

    def reset(self) -> None:
        """A new stretch of assistant speech: forget the last one.

        The cooldown deliberately survives — it is about the user, not about
        which sentence happens to be playing.
        """
        with self._lock:
            self._buf = deque(maxlen=self._preroll + _MAX_STREAK)
            self._streak = 0
            self._triggered = False
            self._handed_off = False
            self._blocks = self._voiced = self._best_run = 0

    def summary(self) -> str | None:
        """One line about the current stretch, or None if nothing was judged."""
        with self._lock:
            if not self._blocks:
                return None
            return (f"{self._blocks} blocks heard, {self._voiced} sounded like a "
                    f"voice, longest run {self._best_run}/{self._need} needed"
                    + (" — interrupted" if self._triggered else ""))

    def feed(self, pcm, level: float, now: float | None = None) -> bool:
        """Judge one microphone block. True only on the block that decides it."""
        t = time.monotonic() if now is None else now
        try:
            data = pcm.tobytes() if hasattr(pcm, "tobytes") else bytes(pcm)
        except Exception:
            return False

        with self._lock:
            if self._handed_off:
                return False
            self._buf.append(data)
            if self._triggered:
                # Decided, not yet acted on: keep every word for the hand-back.
                return False

        # Outside the lock: the guard does an FFT and has its own state.
        try:
            speech = bool(self._guard.is_user_speech(pcm, self._sr, level, when=now))
            need = int(self._guard.required_blocks)
        except Exception:
            speech, need = False, 1 << 30        # any doubt: do not interrupt

        with self._lock:
            if self._handed_off or self._triggered:
                return False
            self._streak = self._streak + 1 if speech else 0
            self._blocks += 1
            self._voiced += int(speech)
            self._best_run = max(self._best_run, self._streak)
            self._need = need
            if self._streak < max(1, need):
                return False
            if t - self._last_trigger < self._cooldown:
                return False
            self._triggered = True
            self._last_trigger = t
            # Keep the run plus its pre-roll, and from here on keep everything.
            keep = list(self._buf)[-(self._streak + self._preroll):]
            self._buf = deque(keep, maxlen=self._max_blocks)
            return True

    def take_audio(self) -> bytes:
        """Hand over what the user has said so far. Call once per trigger."""
        with self._lock:
            if not self._triggered or self._handed_off:
                return b""
            self._handed_off = True
            audio = b"".join(self._buf)
            self._buf = deque(maxlen=self._preroll + _MAX_STREAK)
            self._streak = 0
            return audio
