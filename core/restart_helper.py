"""
core/restart_helper.py — start JARVIS again after it exits, and take back a
self-repair that stops it from starting.

Run by main.py as a separate process just before the old instance exits:

    python core/restart_helper.py --pid <old pid> --repo <repo> --state <dir> -- <command…>

1. Wait for the old process to be gone (its microphone, speakers and tray
   icon must be free before the new one opens them).
2. Start the command again, in the repository, on the same console.
3. Watch for two minutes. The new instance writes <state>/started.ok once its
   Live session connects (core/self_repair.mark_started); that ends the watch.
   If instead it exits with an error, and <state>/applied.json names the
   commit that is checked out, the last self-repair is what broke startup:
   `git reset --keep` to the commit before it, leave rolled_back.json for the
   next instance to announce, and start once more. Only once — a second
   failure is not this helper's to guess at.

Deliberately standalone and stdlib-only (psutil is used when present): it must
work when the code it was started from is the code that is broken.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

WATCH_SECONDS = 120.0


def _wait_exit(pid: int, timeout: float = 30.0) -> None:
    try:
        import psutil
    except ImportError:
        time.sleep(3)
        return
    try:
        psutil.Process(pid).wait(timeout)
    except Exception:
        pass


def _launch(cmd: list[str], cwd: str, new_console: bool = True) -> subprocess.Popen:
    kw = {}
    if os.name == "nt" and new_console:
        # Its own console window: the terminal it was started from no longer
        # shows a process that outlived its parent (seen live: the restarted
        # JARVIS ran and connected, and the tab showed nothing). The full log
        # is also in ~/.jarvis/logs/console.log (core/console_log.py).
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    return subprocess.Popen(cmd, cwd=cwd, **kw)


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=60, **kw)


def watch(cmd: list[str], repo: str, state: Path, watch_s: float = WATCH_SECONDS,
          new_console: bool = True) -> str:
    """Start `cmd` and watch it. Returns what happened, for the log and tests:
    'started', 'exited', 'still running', 'rolled back', 'not rolled back'."""
    ok = state / "started.ok"
    ok.unlink(missing_ok=True)
    child = _launch(cmd, repo, new_console)
    deadline = time.monotonic() + watch_s
    code = None
    while time.monotonic() < deadline:
        if ok.exists():
            return "started"
        code = child.poll()
        if code is not None:
            break
        time.sleep(0.5)
    else:
        return "still running"          # e.g. no network yet: not a broken start
    if code == 0 or ok.exists():
        return "exited"
    try:
        applied = json.loads((state / "applied.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "exited"
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if not applied.get("commit") or head != applied["commit"]:
        return "exited"
    r = _git(repo, "reset", "--keep", applied["base"])
    if r.returncode != 0:
        return "not rolled back"
    (state / "applied.json").unlink(missing_ok=True)
    (state / "rolled_back.json").write_text(json.dumps({
        "commit": applied["commit"], "base": applied["base"], "exit": code,
        "at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    _launch(cmd, repo, new_console)
    return "rolled back"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("usage: restart_helper.py --pid N --repo DIR --state DIR -- command…")
        return 2
    split = argv.index("--")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--state", required=True)
    args = ap.parse_args(argv[:split])
    cmd = argv[split + 1:]
    if not cmd:
        return 2
    _wait_exit(args.pid)
    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)
    outcome = watch(cmd, args.repo, state)
    print(f"[Restart] {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
