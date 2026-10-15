"""core.agents with stand-in CLIs: the ask/run gate, event parsing, resume,
cancel, usage limits, and the model/effort rules. No real agent is started."""
import json
import sys
import textwrap
import time

import pytest

from core import activity, agents, announce
from memory import config_manager as cm

CLAUDE_EVENTS = [
    {"type": "system", "subtype": "init", "session_id": "sess-claude-1"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/app.py"}}]}},
    {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "unifiedWindows": {
            "five_hour": {"utilization": 0.37, "resetsAt": 1792057800},
            "seven_day": {"utilization": 0.58, "resetsAt": 1792274400}}}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Fixed it."}]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "result": "I fixed the bug in app.py and the tests pass.", "session_id": "sess-claude-1",
     "permission_denials": [{"tool_name": "Bash"}]},
]
CODEX_EVENTS = [
    {"type": "thread.started", "thread_id": "thread-9"},
    {"type": "item.completed", "item": {"type": "error", "message": "config warning"}},
    {"type": "turn.started"},
    {"type": "item.started", "item": {"type": "command_execution",
                                      "command": "\"pwsh.exe\" -Command 'pytest -q'"}},
    {"type": "item.completed", "item": {"type": "file_change",
                                        "changes": [{"path": "a.py", "kind": "update"}]}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "Done: tests green."}},
    {"type": "turn.completed", "usage": {"input_tokens": 1}},
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "config" / "api_keys.json")
    agents.reset()
    announce.reset()
    activity.reset()
    monkeypatch.setattr(agents, "find_exe", lambda engine: sys.executable)
    monkeypatch.setattr(agents, "signed_in", lambda engine, exe: True)
    monkeypatch.setattr(agents, "refresh_usage", lambda engine: "")
    real_models = agents.models
    monkeypatch.setattr(agents, "models", lambda engine, refresh=False: [
        agents.Model("gpt-6-astra", "Astra", ["low", "medium"], "low", True)]
        if engine == "codex" else real_models(engine))
    yield
    j = agents.job()
    if j and not j.done and j.proc:
        agents.cancel()
    agents.reset()
    announce.reset()


def _fake_cli(tmp_path, monkeypatch, events, sleep=0.0):
    (tmp_path / "events.json").write_text(json.dumps(events), encoding="utf-8")
    script = tmp_path / "fake_agent.py"
    script.write_text(textwrap.dedent(f"""
        import json, pathlib, sys, time
        sys.stdin.read()
        events = json.loads((pathlib.Path(__file__).parent / "events.json").read_text())
        for ev in events:
            print(json.dumps(ev), flush=True)
            time.sleep({sleep})
    """), encoding="utf-8")
    seen = {}

    def argv(j, exe):
        seen["resume"] = j.resume_id
        seen["argv"] = agents.__dict__["_real_build_argv"](j, exe)
        return [sys.executable, str(script)]
    agents.__dict__.setdefault("_real_build_argv", agents.build_argv)
    monkeypatch.setattr(agents, "build_argv", argv)
    return seen


def _user_answers():
    activity.set_speaking(True)
    activity.set_speaking(False)
    time.sleep(0.05)  # Windows' monotonic clock ticks every ~16 ms
    activity.note_user_input()


def _wait_done():
    for _ in range(200):
        j = agents.job()
        if j and j.done:
            return j
        time.sleep(0.05)
    raise AssertionError("agent never finished")


def test_run_is_refused_until_the_user_answers(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, CLAUDE_EVENTS)
    assert "first" in agents.run()                         # nothing asked yet
    out = agents.ask("Fix the bug", tmp_path)
    assert out.startswith("[ASK_USER]") and "Claude Code" in out
    assert agents.run().startswith("[NOT_STARTED]")
    _user_answers()
    assert agents.run().startswith("[AGENT_STARTED]")


def test_a_claude_run_is_parsed_reported_and_announced(tmp_path, monkeypatch):
    seen = _fake_cli(tmp_path, monkeypatch, CLAUDE_EVENTS)
    agents.ask("Fix the bug", tmp_path)
    _user_answers()
    agents.run()
    j = _wait_done()
    assert j.ok and j.steps == 1 and j.session_id == "sess-claude-1"
    assert "src/app.py" in j.files
    assert "tests pass" in j.report and "1 step blocked" in j.report
    assert agents.usage("claude")["five_hour"]["pct"] == 37.0
    assert agents.usage("claude")["week"]["pct"] == 58.0
    item = announce.take()[0]
    assert item.text.startswith("[AGENT_RESULT] Claude Code finished")
    argv = seen["argv"]
    assert "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "auto"
    assert "--resume" not in argv


def test_a_follow_up_resumes_the_previous_session(tmp_path, monkeypatch):
    seen = _fake_cli(tmp_path, monkeypatch, CLAUDE_EVENTS)
    agents.ask("Fix the bug", tmp_path)
    _user_answers()
    agents.run()
    _wait_done()
    activity.reset()
    assert "continues the previous session" in agents.ask("Add tests", tmp_path, follow_up=True)
    _user_answers()
    agents.run()
    _wait_done()
    assert seen["resume"] == "sess-claude-1"
    assert seen["argv"][seen["argv"].index("--resume") + 1] == "sess-claude-1"


def test_a_codex_run(tmp_path, monkeypatch):
    agents.set_engine("codex")
    monkeypatch.setattr(agents, "models", lambda engine, refresh=False: [
        agents.Model("gpt-6-luna", "Luna", ["low", "medium"], "medium")])
    agents.set_model("gpt-6-luna")
    agents.set_effort("low")
    seen = _fake_cli(tmp_path, monkeypatch, CODEX_EVENTS)
    agents.ask("Make the tests pass", tmp_path)
    _user_answers()
    agents.run()
    j = _wait_done()
    assert j.ok and j.report == "Done: tests green." and j.session_id == "thread-9"
    assert j.steps == 2 and "a.py" in j.files
    argv = seen["argv"]
    assert argv[1] == "exec" and "workspace-write" in argv and argv[-1] == "-"
    assert 'model_reasoning_effort="low"' in argv and "gpt-6-luna" in argv


def test_codex_errors_during_the_turn_fail_the_job(tmp_path, monkeypatch):
    agents.set_engine("codex")
    events = CODEX_EVENTS[:3] + [{"type": "turn.failed",
                                  "error": {"message": "usage limit reached"}}]
    _fake_cli(tmp_path, monkeypatch, events)
    agents.ask("x", tmp_path)
    _user_answers()
    agents.run()
    j = _wait_done()
    assert not j.ok and "usage limit" in j.report


def test_cancel_stops_the_agent(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, CLAUDE_EVENTS * 20, sleep=0.2)
    agents.ask("Long job", tmp_path)
    _user_answers()
    agents.run()
    time.sleep(0.5)
    assert "still working" in agents.status()
    assert "busy" in agents.ask("Another", tmp_path).lower() or \
        "still working" in agents.ask("Another", tmp_path)
    assert "stopped" in agents.cancel()
    j = _wait_done()
    assert not j.ok and "cancelled" in j.report


def test_limits_block_or_warn_before_asking(tmp_path):
    agents._store_usage("claude", {"five_hour": {"pct": 100, "resets_at": 0}, "limited": True})
    out = agents.ask("x", tmp_path)
    assert "used up" in out and "Codex" in out and "Nothing was started" in out
    agents._store_usage("claude", {"five_hour": {"pct": 93, "resets_at": 0}, "limited": False})
    out = agents.ask("x", tmp_path)
    assert out.startswith("[ASK_USER]") and "93%" in out


def test_usage_conversions():
    codex = agents.usage_from_codex({"ordinaryUsageAllowed": True, "rateLimits": {
        "planType": "plus", "rateLimitReachedType": None,
        "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": 1},
        "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 2}}})
    assert codex["five_hour"]["pct"] == 12 and codex["week"]["pct"] == 40
    assert codex["plan"] == "plus" and not codex["limited"]
    claude = agents.usage_from_claude_event({"status": "rejected", "unifiedWindows": {
        "five_hour": {"utilization": 1.0, "resetsAt": 5}}})
    assert claude["five_hour"]["pct"] == 100.0 and claude["limited"]


def test_model_and_effort_rules(monkeypatch):
    monkeypatch.setattr(agents, "models", lambda engine, refresh=False: [
        agents.Model("big", "Big", ["low", "high", "ultra"], "high"),
        agents.Model("small", "Small", ["low", "medium"], "medium")]
        if engine == "codex" else
        [agents.Model(m, m, list(agents.CLAUDE_EFFORTS)) for m, _ in agents.CLAUDE_MODELS])
    agents.set_engine("codex")
    agents.set_model("big")
    agents.set_effort("ultra")
    assert "not available" in agents.set_model("small")
    assert agents.current()["effort"] == "medium"         # fell back to the model's default
    assert "offers these efforts" in agents.set_effort("ultra")
    assert "has no model" in agents.set_model("nonexistent")
    agents.set_engine("claude")
    agents.set_model("opus")
    agents.set_effort("max")
    agents.set_engine("codex")
    assert agents.current()["model"] == "small"           # kept per engine
    agents.set_engine("claude")
    assert (agents.current()["model"], agents.current()["effort"]) == ("opus", "max")


def test_the_child_environment_keeps_the_plan(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", "C:/git/bash.exe")
    env = agents._child_env()
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env
    assert "CLAUDE_CODE_ENTRYPOINT" not in env
    assert env["CLAUDE_CODE_GIT_BASH_PATH"] == "C:/git/bash.exe"
    assert env["JARVIS_AGENT_RUN"] == "1"


def test_codex_default_means_the_accounts_default_model(tmp_path, monkeypatch):
    # Codex's config.toml can name a model the account is refused; "Default"
    # passes the app-server's ★ model explicitly instead.
    agents.set_engine("codex")
    monkeypatch.setattr(agents, "models", lambda engine, refresh=False: [
        agents.Model("gpt-6-astra", "Astra", ["low"], "low", True),
        agents.Model("gpt-6-luna", "Luna", ["low"], "low")])
    seen = _fake_cli(tmp_path, monkeypatch, CODEX_EVENTS)
    agents.ask("x", tmp_path)
    _user_answers()
    agents.run()
    _wait_done()
    argv = seen["argv"]
    assert argv[argv.index("-m") + 1] == "gpt-6-astra"


def test_naming_an_agent_in_ask_switches_to_it(tmp_path, monkeypatch):
    from actions import delegate_to_agent as dta
    from core import projects
    monkeypatch.setattr(projects, "current", lambda: tmp_path)
    assert agents.current()["engine"] == "claude"
    out = dta.delegate_to_agent({"action": "ask", "task": "run the tests",
                                 "engine": "Codex"})
    assert out.startswith("[ASK_USER]") and "Codex" in out
    assert agents.current()["engine"] == "codex"
    assert dta._engine_name("Claude Code") == "claude"
    assert dta._engine_name("chatgpt") == "codex"


def test_delegating_to_another_project_keeps_the_current_one(tmp_path, monkeypatch):
    # Live test: the relay announced "Pedigree", the model passed it as the
    # project, and the agent went to work there.
    from actions import delegate_to_agent as dta
    from core import projects
    (tmp_path / "code" / "Sandbox").mkdir(parents=True)
    (tmp_path / "code" / "Pedigree").mkdir(parents=True)
    monkeypatch.setattr(projects, "search_roots", lambda: [tmp_path / "code"])
    projects.set_current(tmp_path / "code" / "Sandbox")
    out = dta.delegate_to_agent({"action": "ask", "task": "x", "project": "Pedigree"})
    assert "not the current project Sandbox" in out
    assert projects.current().name == "Sandbox"
    out = dta.delegate_to_agent({"action": "ask", "task": "x"})
    assert "not the current project" not in out
