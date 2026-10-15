"""
Push-to-talk — hold a key, speak, release.

Why this exists
---------------
Wake-word is hands-free but it is not always what you want: in a meeting, in a
noisy room, or when you simply do not feel like saying a name out loud, a key
you hold is faster and never mishears. It is also the natural way to use the
assistant while another window has focus.

How it works, and what it costs
-------------------------------
Zero new dependencies, best available mechanism per platform:

* **Windows** — `GetAsyncKeyState` polled from one small thread. This is
  deliberately *not* `RegisterHotKey`, which only reports a press: push-to-talk
  needs the release too, and it needs to work while another application has
  focus. Polling two virtual-key codes 30 times a second is a rounding error of
  CPU and needs no message loop.
* **macOS / Linux** — no portable way to read global key state without pulling
  in a new package or asking for accessibility permissions, so the chord is
  bound as an application shortcut instead: it works whenever the assistant's
  window has focus. `scope` reports which of the two you got, so the UI can say
  so honestly rather than pretending.

The class never raises. If the platform hook cannot be installed it simply
reports `scope == "window"` and the Qt shortcut carries it.
"""

from __future__ import annotations

import platform
import threading
import time
from typing import Callable

_OS = platform.system()

# The default chord. Ctrl+Space is free in most desktop environments and is the
# same finger shape on every keyboard layout, which matters for a worldwide app.
DEFAULT_CHORD = ("ctrl", "space")

# Windows virtual-key codes for the names we accept.
_VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
    "space": 0x20, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "capslock": 0x14, "insert": 0x2D,
}

# Qt key sequence text for the same chord, used by the windowed fallback.
_QT_NAME = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "space": "Space",
            "f8": "F8", "f9": "F9", "f10": "F10",
            "capslock": "CapsLock", "insert": "Ins"}

_POLL_HZ = 30.0
# A key has to be down this long before we call it speech. It stops a stray
# brush of the chord from opening the microphone.
_DEBOUNCE_S = 0.06


def chord_label(chord=DEFAULT_CHORD) -> str:
    """Human-readable name of the chord, for the UI and the logs."""
    return "+".join(_QT_NAME.get(k, k.title()) for k in chord)


def qt_sequence(chord=DEFAULT_CHORD) -> str:
    """The same chord as a QKeySequence string."""
    return "+".join(_QT_NAME.get(k, k.title()) for k in chord)


class PushToTalk:
    """Calls `on_change(held: bool)` whenever the chord is pressed or released.

    Start it once; it is safe to start and stop repeatedly, and safe to stop a
    detector that never started.
    """

    def __init__(self, on_change: Callable[[bool], None], chord=DEFAULT_CHORD):
        self._on_change = on_change
        self._chord = tuple(chord)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._held = False
        self._scope = "window"

    # ── state ───────────────────────────────────────────────────────────────

    @property
    def held(self) -> bool:
        return self._held

    @property
    def scope(self) -> str:
        """'global' once a system-wide hook is running, else 'window'."""
        return self._scope

    @property
    def label(self) -> str:
        return chord_label(self._chord)

    # ── lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> str:
        """Begin watching. Returns the scope actually achieved."""
        self.stop()
        self._stop.clear()
        if _OS == "Windows" and self._can_poll():
            self._scope = "global"
            self._thread = threading.Thread(
                target=self._poll_loop, name="push-to-talk", daemon=True)
            self._thread.start()
        else:
            self._scope = "window"
        return self._scope

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        self._set_held(False)

    # ── the windowed fallback drives this directly ──────────────────────────

    def set_held(self, held: bool) -> None:
        """Feed a press/release from a Qt shortcut (non-Windows, or no hook)."""
        self._set_held(bool(held))

    # ── internals ───────────────────────────────────────────────────────────

    def _can_poll(self) -> bool:
        try:
            import ctypes
            ctypes.windll.user32.GetAsyncKeyState  # noqa: B018 — presence check
            return all(k in _VK for k in self._chord)
        except Exception:
            return False

    def _set_held(self, held: bool) -> None:
        if held == self._held:
            return
        self._held = held
        try:
            self._on_change(held)
        except Exception:
            pass          # a listener fault must never kill the watcher

    def _poll_loop(self) -> None:
        import ctypes
        user32 = ctypes.windll.user32
        codes = [_VK[k] for k in self._chord]
        period = 1.0 / _POLL_HZ
        down_since = 0.0

        while not self._stop.is_set():
            try:
                # The high bit of the return value is "currently down".
                down = all(user32.GetAsyncKeyState(c) & 0x8000 for c in codes)
            except Exception:
                break     # driver or session teardown — fall back to windowed
            now = time.monotonic()
            if down:
                if down_since == 0.0:
                    down_since = now
                elif now - down_since >= _DEBOUNCE_S:
                    self._set_held(True)
            else:
                down_since = 0.0
                self._set_held(False)
            self._stop.wait(period)

        self._set_held(False)
        self._scope = "window"


# ── A press-only global hotkey (the quick prompt) ───────────────────────────
#
# Push-to-talk polls key state because it needs the release. A hotkey that
# only has to notice a press is what `RegisterHotKey` is for, and it does two
# things polling cannot: Windows swallows the key, so the app in front does
# not also act on it, and the press counts as input to this process, which is
# what lets the popup it opens take the keyboard focus (a background process
# that merely calls SetForegroundWindow gets a flashing taskbar button
# instead). The registration fails when another program already owns the key;
# `scope` then says "window" and the caller binds an application shortcut.

_HOTKEY_MODS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012

_VK.update({"j": 0x4A, "k": 0x4B, "q": 0x51, "f7": 0x76, "f11": 0x7A, "f12": 0x7B})
_QT_NAME.update({"j": "J", "k": "K", "q": "Q", "f7": "F7", "f11": "F11",
                 "f12": "F12", "win": "Meta"})


def parse_chord(text: str) -> tuple[str, ...]:
    """"Ctrl+Alt+J" → ("ctrl", "alt", "j"); unknown keys give ()."""
    keys = tuple(k.strip().lower() for k in str(text or "").split("+") if k.strip())
    if not keys or keys[-1] not in _VK or any(k not in _HOTKEY_MODS for k in keys[:-1]):
        return ()
    return keys


class GlobalHotkey:
    """Calls `on_press()` (on its own thread) each time the chord is pressed.

    `start()` returns "global" when Windows granted the hotkey, else "window".
    Safe to stop when never started, and to start again with another chord.
    """

    def __init__(self, on_press: Callable[[], None], chord=("f9",)):
        self._on_press = on_press
        self._chord = tuple(chord)
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._scope = "window"
        self._ready = threading.Event()

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def label(self) -> str:
        return chord_label(self._chord)

    def start(self) -> str:
        self.stop()
        self._scope = "window"
        if _OS != "Windows" or not self._chord or self._chord[-1] not in _VK:
            return self._scope
        self._ready.clear()
        self._thread = threading.Thread(target=self._loop, name="quick-prompt-hotkey",
                                        daemon=True)
        self._thread.start()
        # The registration result is known within a few milliseconds.
        self._ready.wait(timeout=1.0)
        return self._scope

    def stop(self) -> None:
        t, self._thread = self._thread, None
        if t is not None and t.is_alive() and self._tid:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._tid, _WM_QUIT, 0, 0)
            except Exception:
                pass
            t.join(timeout=1.0)
        self._tid = 0
        self._scope = "window"

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        mods = _MOD_NOREPEAT
        for k in self._chord[:-1]:
            mods |= _HOTKEY_MODS.get(k, 0)
        # The hotkey belongs to this thread (hwnd NULL), so this thread's own
        # message loop below is where WM_HOTKEY arrives, and where it must be
        # unregistered.
        ok = bool(user32.RegisterHotKey(None, 1, mods, _VK[self._chord[-1]]))
        self._scope = "global" if ok else "window"
        self._ready.set()
        if not ok:
            return
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY:
                    try:
                        self._on_press()
                    except Exception:
                        pass     # a listener fault must never kill the loop
        finally:
            user32.UnregisterHotKey(None, 1)
            self._scope = "window"
