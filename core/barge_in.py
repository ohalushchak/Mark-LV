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
a sound we did not play apart from our own echo and calibrates itself to the
room. What it cannot tell is a voice from any other sound we did not play — a
door, a keyboard, a television. A real session showed what that costs: two
answers cut off by background sounds, both on exactly the minimum evidence then
required (5 blocks, ~320 ms), one of them throwing away 14 s of a long answer.
So this adds what a single block cannot know, and asks for more of it than it
used to:

* **A settled room.** Nothing interrupts until the guard has learned from ~4 s
  of this room's echo; before that its threshold rests on too little to trust.
* **A higher bar.** A block counts only if what echo could not explain is at
  least `min_residual` of it — above the guard's own floor, which is tuned for
  the echo tail, where letting a quiet reply through matters more.
* **Sustained evidence.** At least `min_speech_s` (~0.64 s) of voice-like
  blocks within a short window. A cough, a click or a 0.45 s burst never gets
  there; real speech does, and the gap between two of its words does not reset
  the count the way it used to.
* **A cooldown.** After an interruption nothing can trigger another for a
  couple of seconds; `hold_off()` stretches that after a false alarm, so a
  television cannot pause the answer every two seconds.
* **The words themselves.** Every block heard while deciding is kept, plus a
  little from just before the evidence began. After the interruption that audio
  is handed back, so the model hears the whole phrase.

A decided interruption is still not final: main.py pauses the answer and
resumes it unless the model transcribes actual words (see `_on_voice_barge_in`).

Thread safety
-------------
`feed()` runs in the audio callback thread; `take_audio()`, `reset()` and
`hold_off()` run on the event loop. One small lock guards the shared state;
nothing in here blocks for longer than one FFT.
"""

from __future__ import annotations

import math
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

# Voice-like sound needed before an interruption is believed.
_MIN_SPEECH_S = 0.64
# Blocks of doubt tolerated inside that stretch — the pauses between words.
_GAP_BLOCKS = 3

# Echo blocks the guard must have learned from before anything interrupts
# (~4 s of the assistant talking in this room).
_SETTLE_BLOCKS = 64

# Share of a block that echo cannot explain before it counts as a voice. The
# guard's own floor is 0.15; distinct voices measured ~0.28 in its tests.
_MIN_RESIDUAL = 0.20


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
                 max_audio_s: float = _MAX_AUDIO_S,
                 min_speech_s: float = _MIN_SPEECH_S,
                 settle_blocks: int = _SETTLE_BLOCKS,
                 min_residual: float = _MIN_RESIDUAL) -> None:
        self._guard = guard
        self._sr = int(sample_rate)
        self._cooldown = float(cooldown_s)
        self._preroll = max(0, int(preroll_blocks))
        self._min_blocks = max(1, math.ceil(min_speech_s * sample_rate / max(1, block_size)))
        self._settle = max(0, int(settle_blocks))
        self._min_residual = float(min_residual)
        # The guard may ask for more than min_speech_s in a hard room
        # (EchoGuard._BLOCKS_NOISY = 12); the window must fit that too.
        self._window_len = max(self._min_blocks, 12) + _GAP_BLOCKS
        self._max_blocks = max(self._preroll + self._window_len,
                               int(max_audio_s * sample_rate / max(1, block_size)))
        self._lock = threading.Lock()
        self._buf: deque[bytes] = deque(maxlen=self._preroll + self._window_len)
        self._window: deque[bool] = deque(maxlen=self._window_len)
        self._triggered = False
        self._handed_off = False
        self._not_before = float("-inf")
        # Per-stretch diagnostics: what was heard, for one line in the console.
        self._blocks = 0
        self._voiced = 0
        self._best = 0
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

    @property
    def needed_blocks(self) -> int:
        """Voice-like blocks needed right now, in a window of a few more."""
        try:
            return max(self._min_blocks, int(self._guard.required_blocks))
        except Exception:
            return self._min_blocks

    # ── entry points ────────────────────────────────────────────────────────

    def reset(self) -> None:
        """A new stretch of assistant speech: forget the last one.

        The cooldown deliberately survives — it is about the user, not about
        which sentence happens to be playing.
        """
        with self._lock:
            self._buf = deque(maxlen=self._preroll + self._window_len)
            self._window.clear()
            self._triggered = False
            self._handed_off = False
            self._blocks = self._voiced = self._best = 0

    def hold_off(self, seconds: float, now: float | None = None) -> None:
        """Nothing triggers for `seconds` from now — used after a false alarm,
        when whatever set it off is probably still making noise."""
        t = time.monotonic() if now is None else now
        with self._lock:
            self._not_before = max(self._not_before, t + float(seconds))

    def summary(self) -> str | None:
        """One line about the current stretch, or None if nothing was judged."""
        with self._lock:
            if not self._blocks:
                return None
            return (f"{self._blocks} blocks heard, {self._voiced} sounded like a "
                    f"voice, best {self._best}/{self._need} needed"
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
            g = self._guard
            speech = bool(g.is_user_speech(pcm, self._sr, level, when=now))
            speech = speech and float(getattr(g, "last_residual", 1.0)) >= self._min_residual
            settled = int(getattr(g, "echo_blocks", self._settle)) >= self._settle
            need = max(self._min_blocks, int(g.required_blocks))
        except Exception:
            speech, settled, need = False, False, 1 << 30   # any doubt: do not interrupt

        with self._lock:
            if self._handed_off or self._triggered:
                return False
            self._window.append(speech)
            self._blocks += 1
            self._voiced += int(speech)
            # The window may be shorter than a hard room's demand; then the
            # evidence is whole consecutive runs, which the window still holds.
            score = sum(self._window)
            self._best = max(self._best, score)
            self._need = need
            if not speech or score < need or not settled:
                return False
            if t < self._not_before:
                return False
            self._triggered = True
            self._not_before = t + self._cooldown
            # Keep the evidence plus its pre-roll, and from here on keep
            # everything. The evidence starts at the first voice-like block
            # still in the window.
            first = next(i for i, v in enumerate(self._window) if v)
            span = len(self._window) - first
            keep = list(self._buf)[-(span + self._preroll):]
            self._buf = deque(keep, maxlen=self._max_blocks)
            return True

    def take_audio(self) -> bytes:
        """Hand over what the user has said so far. Call once per trigger."""
        with self._lock:
            if not self._triggered or self._handed_off:
                return b""
            self._handed_off = True
            audio = b"".join(self._buf)
            self._buf = deque(maxlen=self._preroll + self._window_len)
            self._window.clear()
            return audio
