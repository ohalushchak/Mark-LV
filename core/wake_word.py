"""
Local wake-word detection ("Hey Jarvis", or any phrase the user picks).

Design goals:
  • ZERO cost when the feature is off — no engine package is imported at
    module load, only inside start()/install helpers. If the user never
    enables wake word, none of this touches the app.
  • ZERO latency on the audio path — the microphone callback only ever does a
    cheap, non-blocking queue push (feed()); the actual model inference runs in
    this module's own background thread, so the real-time audio thread and the
    Gemini stream are never slowed.
  • Fully local & offline — audio fed here never leaves the machine; there is no
    network call except the one-time install the user triggers from the UI.

The engine that does the listening is pluggable (core/wake_engines): the
pretrained openWakeWord "Hey Jarvis" by default, a typed phrase through
sherpa-onnx, or a Ukrainian word through Vosk. Everything below is shared by
all three.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

from core.wake_engines import (
    SAMPLE_RATE, WakeEngine, WakeEngineError, WakeSettings, engine_class,
)

# Minimum time between two detections. Streaming engines can report the same
# utterance twice across a block boundary; nothing legitimate wakes it twice
# within two seconds.
COOLDOWN_S = 2.0

# How much audio may wait for the worker: ~3 s of 64 ms blocks. When inference
# falls behind, the OLDEST audio goes — the newest is where the phrase being
# said right now is.
QUEUE_BLOCKS = 48


@dataclass
class Detection:
    label: str
    score: float
    engine: str
    # Microphone block (as numbered by the caller of feed()) during which the
    # phrase ended, or None when the caller did not number its blocks.
    end_seq: int | None = None


def current_settings() -> WakeSettings:
    """The wake-word settings as configured right now."""
    from memory.config_manager import (
        WAKE_MODELS_DIR, get_assistant_name, get_wake_settings,
    )
    return WakeSettings.from_config(get_wake_settings(), get_assistant_name(),
                                    WAKE_MODELS_DIR)


def is_installed(settings: WakeSettings | None = None) -> bool:
    """True if the chosen engine's package is importable (no model check)."""
    s = settings or current_settings()
    return engine_class(s.engine).is_installed()


def is_ready(settings: WakeSettings | None = None) -> bool:
    """True if the chosen engine is installed AND its model files are on disk.

    This is a cheap, DETERMINISTIC file-existence check. It deliberately does NOT
    construct a model to probe readiness — doing that is slow and, worse, can
    clash with the detector's own model when it's already running, which
    intermittently returned False and made the UI flicker to 'not downloaded'.
    Never raises.
    """
    try:
        s = settings or current_settings()
        return engine_class(s.engine).is_ready(s)
    except Exception:
        return False


def phrase_label(settings: WakeSettings | None = None) -> str:
    """What the user has to say, e.g. 'Hey Jarvis'. Loads nothing."""
    try:
        s = settings or current_settings()
        return engine_class(s.engine)(s).label
    except Exception:
        return "Hey Jarvis"


def install_and_download(logger: Callable[[str], None] = print,
                         notify: Callable[[str], None] | None = None,
                         settings: WakeSettings | None = None) -> tuple[bool, str]:
    """
    One-click setup for the UI button: pip-install the chosen engine if
    missing, then download its model. Returns (ok, message). Never raises —
    every failure is reported through the returned message and the logger.
    """
    from core.wake_engines._download import pip_install

    _tell = notify or (lambda _msg: None)

    def log(msg: str) -> None:
        logger(msg)
        _tell(msg)

    try:
        s = settings or current_settings()
        cls = engine_class(s.engine)
        if not cls.is_installed():
            pip_install(cls.pip_packages, log)
            import importlib
            importlib.invalidate_caches()
        if not cls.models_present(s):
            cls.download(s, log)
        if not cls.is_ready(s):
            return False, "installed, but the wake model could not be found."
        logger("Wake word: ready.")
        return True, "Wake word installed and ready."
    except WakeEngineError as e:
        return False, str(e)
    except Exception as e:
        return False, f"setup error: {e}"


class WakeWordDetector:
    """
    Runs a wake engine in a dedicated thread. The mic thread calls feed() with
    raw int16 frames; detections invoke on_detect(Detection) — called from this
    thread, so the callback must marshal to whatever loop/UI it needs.
    """

    def __init__(self, on_detect: Callable[[Detection], None],
                 settings: WakeSettings | None = None,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None,
                 engine: WakeEngine | None = None,
                 cooldown_s: float = COOLDOWN_S):
        self._on_detect = on_detect
        self._settings  = settings
        self._engine    = engine
        self._logger    = logger
        # See PluginRegistry: `logger` is the console and gets everything,
        # `notify` is the activity log and gets only what the user must act on.
        self._notify    = notify or (lambda _msg: None)
        self._cooldown  = cooldown_s
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_BLOCKS)
        self._thread: threading.Thread | None = None
        self._running = False
        self._ready = False
        self._quiet_until = 0.0
        self._reset_pending = False
        self._last_hit = 0.0
        self.error = ""

    def start(self) -> bool:
        """Load the model and spawn the inference thread. Returns True on success.
        Blocks while the model loads, so call it off the asyncio and Qt loops.
        Safe to call again — a no-op if already running. Never raises."""
        if self._running:
            return True
        built = self._engine is None
        try:
            if built:
                s = self._settings or current_settings()
                self._settings = s
                self._engine = engine_class(s.engine)(s)
            self._engine.load()
        except Exception as e:
            self.error = str(e)
            self._logger(f"Wake word: could not load — {e}")
            self._notify(f"Wake word unavailable ({e}) — use WAKE NOW or push-to-talk.")
            if built:
                self._engine = None     # a retry builds it again from settings
            return False
        self.error = ""
        self._running = True
        self._ready = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="WakeWordThread")
        self._thread.start()
        self._logger(f"Wake word: listening for '{self.label}' ({self._engine.id}).")
        return True

    def stop(self) -> None:
        self._running = False
        self._ready = False
        # unblock the thread if it's waiting on the queue
        self._drain()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        # Let the worker release its model before a replacement is loaded —
        # two engines alive at once is two models in memory. Bounded: one
        # inference step is milliseconds.
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=1.0)

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def label(self) -> str:
        if self._engine is not None:
            return self._engine.label
        return phrase_label(self._settings)

    def arm(self, quiet_s: float = 0.0) -> None:
        """Start listening afresh: forget everything heard so far, and ignore
        the microphone for `quiet_s` seconds.

        Called whenever the assistant falls asleep. Its own last words are
        still coming out of the speakers then, and they wake it better than the
        user does — measured on one setup at 0.97-0.99 for its own voice
        against 0.85-0.89 for the real phrase."""
        self._quiet_until = time.monotonic() + max(0.0, quiet_s)
        self._drain()
        # The engine is only touched from the worker thread; ask it to reset.
        self._reset_pending = True

    def feed(self, frame_int16, seq: int | None = None) -> None:
        """Called from the mic callback (real-time thread). Must stay cheap and
        never block — the frame is copied and the oldest queued audio is
        dropped if the worker is behind."""
        if not self._running or time.monotonic() < self._quiet_until:
            return
        try:
            # frame_int16 is a numpy int16 array (possibly 2-D mono) — flatten to 1-D
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            item = (seq, data)
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                self._queue.put_nowait(item)
        except Exception:
            pass

    def _loop(self) -> None:
        engine = self._engine
        try:
            while self._running:
                item = self._queue.get()
                if item is None or not self._running:
                    break
                if self._reset_pending:
                    self._reset_pending = False
                    engine.reset()
                seq, frame = item
                try:
                    hit = engine.process(frame)
                except Exception as e:
                    self._logger(f"Wake word: inference error — {e}")
                    continue
                if hit is None:
                    continue
                now = time.monotonic()
                if now - self._last_hit < self._cooldown:
                    continue
                self._last_hit = now
                end_seq = None
                if seq is not None:
                    back = int(round(hit.ago_s * SAMPLE_RATE / max(1, len(frame))))
                    end_seq = seq - back
                self._logger(f"Wake word: heard '{hit.label}' (score {hit.score:.2f}).")
                # drain any backlog so we don't double-fire on the same utterance
                self._drain()
                engine.reset()
                try:
                    self._on_detect(Detection(hit.label, hit.score, engine.id, end_seq))
                except Exception as e:
                    self._logger(f"Wake word: on_detect error — {e}")
        finally:
            try:
                engine.close()
            except Exception:
                pass

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
