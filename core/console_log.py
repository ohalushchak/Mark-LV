"""
core/console_log.py — everything printed to the console also goes to a file.

WHY
    The console is where JARVIS explains itself: every tool call, reply,
    reconnect and traceback is printed there. After a self-repair restart the
    new instance is no longer attached to the terminal it was started from
    (seen live: its output never appeared in the tab, so nobody could tell what
    it did), and a crash at startup leaves nothing behind at all. A copy on
    disk survives both: ~/.jarvis/logs/console.log for this run, the previous
    run beside it as console.log.1.

    Only main.main() installs it; tests import main without it.
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

DIR = Path.home() / ".jarvis" / "logs"
FILE = DIR / "console.log"


class _Tee:
    """A text stream that writes to the original stream and to the log file."""

    def __init__(self, stream, sink, lock):
        self._stream, self._sink, self._lock = stream, sink, lock

    def write(self, text):
        try:
            n = self._stream.write(text) if self._stream is not None else len(text)
        except Exception:
            n = len(text)
        try:
            with self._lock:
                self._sink.write(text)
                if "\n" in text:
                    self._sink.flush()
        except Exception:
            pass
        return n

    def flush(self):
        for s in (self._stream, self._sink):
            try:
                if s is not None:
                    s.flush()
            except Exception:
                pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install() -> Path | None:
    """Start copying stdout and stderr to the log. Never raises."""
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        if FILE.exists():
            FILE.replace(FILE.with_suffix(".log.1"))
        sink = FILE.open("w", encoding="utf-8", errors="replace", buffering=1)
        sink.write(f"=== JARVIS started {datetime.now().isoformat(timespec='seconds')} "
                   f"({' '.join(sys.argv)}) ===\n")
        lock = threading.Lock()
        sys.stdout = _Tee(sys.stdout, sink, lock)
        sys.stderr = _Tee(sys.stderr, sink, lock)
        return FILE
    except Exception as e:
        print(f"[Log] console log unavailable: {e}")
        return None
