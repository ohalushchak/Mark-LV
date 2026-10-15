"""
core/agent_relay.py — hear about your own Claude Code and Codex sessions.

WHY THIS EXISTS
    Agent sessions run for minutes in a terminal the user is not looking at.
    The omygod9138/Mark-L fork spoke a session's reply when it finished and
    announced sessions waiting for confirmation, through the herdr terminal
    multiplexer and a Stop hook whose script it did not ship. Here it is built
    on the two CLIs' own hooks, which share one format: core/agent_hook.py
    drops each event into ~/.jarvis/agent_queue and this module turns the
    queue into announcements (core/announce.py speaks them when quiet).

WHAT GETS SAID, AND WHAT DOES NOT
    * A turn that ended — only when it ran at least `relay_min_turn_s` (30 s by
      default). Quick replies are ones the user was watching arrive.
    * A session waiting for permission — only after 15 s, and only if its
      transcript has not changed since, which is how an answered prompt is
      told apart from one still waiting.
    * Per session the newest wins (announce keys), and anything older than ten
      minutes is dropped rather than read out late.
    * Runs the assistant started itself are skipped by the hook.

INSTALLING
    install(True) adds one hook entry per event to ~/.claude/settings.json and
    ~/.codex/hooks.json; install(False) removes exactly those entries. Ours are
    recognised by the hook's file name, nothing else in either file is
    touched, and the previous file is kept beside it as *.jarvis-backup.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

QUEUE = Path.home() / ".jarvis" / "agent_queue"
HOOK = Path(__file__).resolve().parent / "agent_hook.py"
EVENTS = ("UserPromptSubmit", "Stop", "PermissionRequest")
LABEL = {"claude": "Claude Code", "codex": "Codex"}

MAX_AGE = 600.0              # older than ten minutes: stale, dropped
PERMISSION_WAIT = 15.0       # a prompt must still be waiting after this long


def settings_files() -> dict[str, Path]:
    return {"claude": Path.home() / ".claude" / "settings.json",
            "codex": Path.home() / ".codex" / "hooks.json"}


def hook_command(source: str) -> str:
    py = Path(sys.executable).as_posix()
    return f'"{py}" "{HOOK.as_posix()}" {source}'


def _ours(entry: dict) -> bool:
    return any(HOOK.name in str(h.get("command", ""))
               for h in (entry.get("hooks") or []) if isinstance(h, dict))


def _edit(path: Path, source: str, enable: bool) -> str:
    if not path.parent.is_dir():
        return f"{LABEL[source]}: not installed here (no {path.parent})."
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            return f"{LABEL[source]}: {path} is not valid JSON, so I left it alone."
        if not isinstance(data, dict):
            return f"{LABEL[source]}: {path} has an unexpected shape, so I left it alone."
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    changed = False
    for event in EVENTS:
        entries = [e for e in (hooks.get(event) or []) if isinstance(e, dict)]
        kept = [e for e in entries if not _ours(e)]
        if enable:
            kept.append({"hooks": [{"type": "command", "command": hook_command(source),
                                    "timeout": 10}]})
        if kept != entries:
            changed = True
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not changed:
        return f"{LABEL[source]}: already {'on' if enable else 'off'}."
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".jarvis-backup"))
    tmp = path.with_name(path.name + ".jarvis-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return f"{LABEL[source]}: {'on' if enable else 'off'}."


def install(enable: bool) -> str:
    """Add (or remove) the relay hook in both CLIs' settings."""
    out = [_edit(path, source, enable) for source, path in settings_files().items()]
    from memory.config_manager import save_agent_settings
    save_agent_settings(relay_enabled=bool(enable))
    if enable:
        QUEUE.mkdir(parents=True, exist_ok=True)
    return "Agent session announcements " + ("on" if enable else "off") + " — " + " ".join(out)


def installed() -> dict[str, bool]:
    out = {}
    for source, path in settings_files().items():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            hooks = data.get("hooks") or {}
            out[source] = all(any(_ours(e) for e in hooks.get(ev) or [] if isinstance(e, dict))
                              for ev in EVENTS)
        except (OSError, ValueError, AttributeError):
            out[source] = False
    return out


def _project(cwd: str) -> str:
    return Path(cwd).name if cwd else "a project"


def _duration(secs) -> str:
    if secs is None:
        return ""
    secs = float(secs)
    return f"{secs:.0f} s" if secs < 90 else f"{secs / 60:.0f} min"


class Relay:
    """check() returns announcements (text, note, key) for what is due."""

    def __init__(self, min_turn_s: float = 30.0):
        self.min_turn_s = min_turn_s
        self._waiting: dict[str, dict] = {}     # session → permission request held back

    def _drain(self) -> list[dict]:
        try:
            paths = sorted(p for p in QUEUE.glob("*.json") if not p.name.startswith("start-"))
        except OSError:
            return []
        items = []
        for p in paths:
            try:
                items.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
            try:
                p.unlink()
            except OSError:
                pass
        self._prune_starts()
        return items

    def _prune_starts(self) -> None:
        cutoff = time.time() - 24 * 3600
        try:
            for p in QUEUE.glob("start-*.json"):
                if p.stat().st_mtime < cutoff:
                    p.unlink()
        except OSError:
            pass

    def check(self) -> list[tuple[str, str, str]]:
        now = time.time()
        out: list[tuple[str, str, str]] = []
        for it in self._drain():
            if not isinstance(it, dict) or now - float(it.get("ts") or 0) > MAX_AGE:
                continue
            session = str(it.get("session") or "")
            if it.get("event") == "PermissionRequest":
                self._waiting[session] = it
                continue
            self._waiting.pop(session, None)        # the session moved on
            dur = it.get("duration")
            if dur is not None and float(dur) < self.min_turn_s:
                continue
            label = LABEL.get(it.get("source"), "An agent")
            proj = _project(it.get("cwd", ""))
            took = _duration(dur)
            msg = str(it.get("text") or "").strip() or "(it wrote no final message)"
            out.append((
                f"[AGENT_SESSION] The user's own {label} session in the project {proj} "
                f"finished a turn{' after ' + took if took else ''}. Its last message:\n"
                f"{msg[:1200]}\n\nTell the user in ONE short sentence what it did or "
                f"what it needs from them — the outcome first, no code, no paths. This "
                f"is news about a separate session: it does not change the project you "
                f"work in and asks you to do nothing.",
                f"{label} in {proj} finished" + (f" ({took})" if took else ""),
                f"relay:{session}"))
        for session, it in list(self._waiting.items()):
            if now - float(it.get("ts") or 0) > MAX_AGE:
                self._waiting.pop(session, None)
                continue
            if now - float(it["ts"]) < PERMISSION_WAIT:
                continue
            self._waiting.pop(session, None)
            transcript = it.get("transcript") or ""
            try:
                if transcript and os.path.getmtime(transcript) > float(it["ts"]) + 1.0:
                    continue                         # answered meanwhile
            except OSError:
                pass
            label = LABEL.get(it.get("source"), "An agent")
            proj = _project(it.get("cwd", ""))
            what = it.get("tool") or "a tool"
            detail = f" ({it['detail']})" if it.get("detail") else ""
            out.append((
                f"[AGENT_SESSION] The user's own {label} session in the project {proj} "
                f"is waiting for their permission to use {what}{detail}. Tell the user in "
                f"one short sentence that it is waiting for them. This is news about a "
                f"separate session: it does not change the project you work in.",
                f"{label} in {proj} is waiting for your permission",
                f"relay:{session}"))
        return out
