"""
core/agent_hook.py — the hook Claude Code and Codex run; it tells JARVIS what
your agent sessions are doing.

Installed by core/agent_relay.py into ~/.claude/settings.json and
~/.codex/hooks.json (both CLIs share one hook format) as:

    "<python>" "<this file>" claude|codex

and called with the event as JSON on stdin:

    UserPromptSubmit   a turn starts — its time is noted, so a quick answer the
                       user watched arrive can be told apart from a long job
    Stop               a turn ended — its last message is queued
    PermissionRequest  the session is waiting for the user to allow a tool

Each event becomes one small JSON file in ~/.jarvis/agent_queue, which the
assistant drains (core/agent_relay.py). Standard library only and never an
error: a hook that fails or prints would get in the way of the user's own
session. Runs the assistant started itself (JARVIS_AGENT_RUN=1) are skipped —
their outcome is announced by core/agents.py already.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

QUEUE = Path.home() / ".jarvis" / "agent_queue"
_TEXT_CHARS = 2000


def _write(name: str, data: dict) -> None:
    QUEUE.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE / f".{name}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, QUEUE / name)          # the reader never sees half a file


def _safe(s: str) -> str:
    return "".join(c for c in str(s) if c.isalnum() or c in "-_")[:80] or "session"


def main() -> None:
    if os.environ.get("JARVIS_AGENT_RUN"):
        return
    source = (sys.argv[1] if len(sys.argv) > 1 else "claude").lower()
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    event = str(payload.get("hook_event_name") or "")
    session = _safe(payload.get("session_id") or "")
    now = time.time()
    if event == "UserPromptSubmit":
        _write(f"start-{source}-{session}.json", {"ts": now})
        return
    if event not in ("Stop", "PermissionRequest"):
        return
    item = {"source": source, "event": event, "session": session, "ts": now,
            "cwd": str(payload.get("cwd") or ""),
            "transcript": str(payload.get("transcript_path") or "")}
    if event == "Stop":
        if payload.get("stop_hook_active"):
            return                         # a hook made it continue; not a real end
        item["text"] = str(payload.get("last_assistant_message") or "")[:_TEXT_CHARS]
        try:
            start = json.loads((QUEUE / f"start-{source}-{session}.json")
                               .read_text(encoding="utf-8")).get("ts")
            item["duration"] = round(now - float(start), 1) if start else None
        except (OSError, ValueError, TypeError):
            item["duration"] = None
    else:
        tool = str(payload.get("tool_name") or "a tool")
        inp = payload.get("tool_input") or {}
        detail = ""
        if isinstance(inp, dict):
            for key in ("command", "file_path", "path", "url", "description"):
                if isinstance(inp.get(key), str) and inp[key].strip():
                    detail = " ".join(inp[key].split())[:160]
                    break
        item.update(tool=tool, detail=detail)
    _write(f"{time.time_ns()}-{uuid.uuid4().hex[:6]}.json", item)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
