"""The agent-session relay: the hook script, the queue, and installing the hook
into Claude Code's and Codex's settings without touching anything else."""
import json
import os
import subprocess
import sys
import time

import pytest

from core import agent_relay as ar
from memory import config_manager as cm

HOOK = ar.HOOK


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "config" / "api_keys.json")
    queue = tmp_path / ".jarvis" / "agent_queue"
    monkeypatch.setattr(ar, "QUEUE", queue)
    return tmp_path


def _hook(home, source, payload, **env):
    e = {k: v for k, v in os.environ.items() if k != "JARVIS_AGENT_RUN"}
    e.update(USERPROFILE=str(home), HOME=str(home), **env)
    r = subprocess.run([sys.executable, str(HOOK), source], input=json.dumps(payload),
                       capture_output=True, text=True, env=e, timeout=30)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_the_hook_queues_a_finished_turn_with_its_duration(home):
    _hook(home, "claude", {"hook_event_name": "UserPromptSubmit", "session_id": "s1"})
    _hook(home, "claude", {"hook_event_name": "Stop", "session_id": "s1",
                           "cwd": "D:/Projects/Mark-LIV",
                           "last_assistant_message": "All tests pass."})
    relay = ar.Relay(min_turn_s=0)
    due = relay.check()
    assert len(due) == 1
    text, note, key = due[0]
    assert "Claude Code" in text and "Mark-LIV" in text and "All tests pass." in text
    assert key == "relay:s1" and "finished" in note
    assert relay.check() == []                    # drained


def test_quick_turns_are_not_announced(home):
    _hook(home, "codex", {"hook_event_name": "UserPromptSubmit", "session_id": "s2"})
    _hook(home, "codex", {"hook_event_name": "Stop", "session_id": "s2", "cwd": "x",
                          "last_assistant_message": "hi"})
    assert ar.Relay(min_turn_s=30).check() == []


def test_the_assistants_own_runs_are_skipped(home):
    _hook(home, "claude", {"hook_event_name": "Stop", "session_id": "s3"},
          JARVIS_AGENT_RUN="1")
    assert not ar.QUEUE.exists() or not list(ar.QUEUE.glob("*.json"))


def test_bad_input_never_fails_the_hook(home):
    e = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}
    r = subprocess.run([sys.executable, str(HOOK), "claude"], input="not json",
                       capture_output=True, text=True, env=e, timeout=30)
    assert r.returncode == 0 and r.stdout == ""


def test_permission_prompts_wait_and_drop_when_answered(home, monkeypatch):
    transcript = home / "t.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    old = time.time() - 60
    os.utime(transcript, (old, old))
    _hook(home, "claude", {"hook_event_name": "PermissionRequest", "session_id": "s4",
                           "cwd": "proj", "tool_name": "Bash",
                           "tool_input": {"command": "npm install"},
                           "transcript_path": str(transcript)})
    relay = ar.Relay()
    assert relay.check() == []                     # held back for 15 s
    monkeypatch.setattr(ar, "PERMISSION_WAIT", 0.0)
    due = relay.check()
    assert len(due) == 1 and "waiting for their permission to use Bash" in due[0][0]
    assert "npm install" in due[0][0]
    # Answered: the transcript moved on after the request.
    _hook(home, "claude", {"hook_event_name": "PermissionRequest", "session_id": "s5",
                           "cwd": "proj", "tool_name": "Edit",
                           "transcript_path": str(transcript)})
    relay.check()
    time.sleep(1.2)
    transcript.write_text("{}\n{}\n", encoding="utf-8")
    future = time.time() + 5
    os.utime(transcript, (future, future))
    assert relay.check() == []


def test_install_adds_and_removes_only_our_entries(home, monkeypatch):
    claude = home / ".claude" / "settings.json"
    codex = home / ".codex" / "hooks.json"
    claude.parent.mkdir()
    codex.parent.mkdir()
    other = {"hooks": [{"type": "command", "command": "C:/orca/hook.exe claude"}]}
    claude.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [other]}}), encoding="utf-8")
    codex.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "*", **other}]}}),
                     encoding="utf-8")
    monkeypatch.setattr(ar, "settings_files", lambda: {"claude": claude, "codex": codex})

    out = ar.install(True)
    assert "on" in out and ar.installed() == {"claude": True, "codex": True}
    data = json.loads(claude.read_text(encoding="utf-8"))
    assert data["model"] == "opus" and data["hooks"]["Stop"][0] == other
    assert len(data["hooks"]["Stop"]) == 2 and set(ar.EVENTS) <= set(data["hooks"])
    assert (claude.parent / "settings.json.jarvis-backup").exists()
    assert cm.get_agent_settings()["relay_enabled"]
    assert "already on" in ar.install(True)

    ar.install(False)
    data = json.loads(claude.read_text(encoding="utf-8"))
    assert data == {"model": "opus", "hooks": {"Stop": [other]}}
    assert json.loads(codex.read_text(encoding="utf-8")) == \
        {"hooks": {"PreToolUse": [{"matcher": "*", **other}]}}
    assert not cm.get_agent_settings()["relay_enabled"]


def test_install_leaves_a_broken_settings_file_alone(home, monkeypatch):
    claude = home / ".claude" / "settings.json"
    claude.parent.mkdir()
    claude.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(ar, "settings_files", lambda: {"claude": claude,
                                                       "codex": home / "nope" / "hooks.json"})
    out = ar.install(True)
    assert "not valid JSON" in out and "not installed here" in out
    assert claude.read_text(encoding="utf-8") == "{ not json"
