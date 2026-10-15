"""Stages 1–2 of self-repair: argument checks before a tool runs, honest
"no result" reporting, the error journal and per-tool health."""
import json
import threading

import pytest

from core import health, tool_args
from core.action_loader import NO_RESULT, ActionRecord, ActionRegistry

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "action": {"type": "STRING", "enum": ["open", "close"]},
        "count": {"type": "INTEGER"},
        "ratio": {"type": "NUMBER"},
        "force": {"type": "BOOLEAN"},
        "paths": {"type": "ARRAY", "items": {"type": "STRING"}},
        "opts": {"type": "OBJECT", "properties": {"n": {"type": "INTEGER"}},
                 "required": ["n"]},
    },
    "required": ["action"],
}


def test_harmless_spellings_are_repaired():
    args, problem = tool_args.check(
        {"action": "OPEN", "count": "5", "ratio": "0.5", "force": "yes",
         "paths": '["a.txt", "b.txt"]', "opts": '{"n": 2.0}', "extra": 1}, SCHEMA)
    assert problem == ""
    assert args == {"action": "open", "count": 5, "ratio": 0.5, "force": True,
                    "paths": ["a.txt", "b.txt"], "opts": {"n": 2}, "extra": 1}
    args, _ = tool_args.check({"action": "close", "paths": "one.txt", "count": None}, SCHEMA)
    assert args == {"action": "close", "paths": ["one.txt"]}


def test_meaning_changing_mistakes_are_refused():
    assert "missing required action" in tool_args.check({}, SCHEMA)[1]
    assert "one of open, close" in tool_args.check({"action": "delete"}, SCHEMA)[1]
    assert "whole number" in tool_args.check({"action": "open", "count": "many"}, SCHEMA)[1]
    assert "whole number" in tool_args.check({"action": "open", "count": 2.5}, SCHEMA)[1]
    assert "true or false" in tool_args.check({"action": "open", "force": "maybe"}, SCHEMA)[1]
    assert "opts.n" in tool_args.check({"action": "open", "opts": {}}, SCHEMA)[1]


def test_unknown_schemas_never_block():
    assert tool_args.check({"x": 1}, None) == ({"x": 1}, "")
    assert tool_args.check({"x": 1}, {"type": "OBJECT"}) == ({"x": 1}, "")


def test_refusal_stops_the_model_after_two_retries():
    tool_args.reset()
    assert "call t again now" in tool_args.refusal("t", "missing x")
    assert "call t again now" in tool_args.refusal("t", "missing x")
    assert "stop retrying" in tool_args.refusal("t", "missing x")
    tool_args.accepted("t")
    assert "call t again now" in tool_args.refusal("t", "missing x")
    tool_args.reset()


def _registry(handler, name="demo"):
    rec = ActionRecord(name=name, description="d", handler=handler, valid=True)
    return ActionRegistry({name: rec}, logger=lambda m: None)


def test_no_result_is_not_reported_as_done():
    reg = _registry(lambda parameters: None)
    assert reg.run("demo", {}) == NO_RESULT
    assert health.status("demo") == "ok"


def test_a_crash_goes_to_the_journal_with_secrets_hidden(monkeypatch):
    monkeypatch.setattr(health, "_secret_values", ["very-secret-config-value"])

    def boom(parameters):
        raise RuntimeError("bad key " + "AIza" + "SyA1234567890abcdefghijklmnop and "
                           "very-secret-config-value for someone@example.com")
    reg = _registry(boom)
    out = reg.run("demo", {"token": "abc"})
    assert out.startswith("Tool 'demo' failed")
    [entry] = health.recent()
    assert entry["kind"] == "crash" and entry["tool"] == "demo"
    assert entry["type"] == "RuntimeError"
    assert "tests/test_tool_health.py" in entry["where"]
    text = json.dumps(entry)
    assert "AIzaSy" not in text and "very-secret" not in text and "example.com" not in text
    assert "Traceback" in entry["trace"]
    assert health.get(entry["id"][:5])["id"] == entry["id"]


def test_reported_failures_count_and_status_turns_red():
    from core import health as h

    def handler(parameters):
        if parameters.get("fail"):
            h.failed("disk not found")
            return "I could not find that disk."
        return "Done for real."
    reg = _registry(handler)
    for _ in range(4):
        reg.run("demo", {})
    assert health.status("demo") == "ok"
    reg.run("demo", {"fail": True})
    assert health.status("demo") == "warn"
    reg.run("demo", {"fail": True})
    assert health.status("demo") == "bad"            # 2 of 6 > 20 %
    assert health.summary()["bad"] == ["demo"]
    assert health.recent()[0]["message"] == "disk not found"


def test_failure_looking_results_are_noticed():
    reg = _registry(lambda parameters: "Error: the camera is busy")
    reg.run("demo", {})
    assert health.status("demo") == "warn"
    assert health.looks_failed("Sir, the 'printer' plugin failed: offline")
    assert not health.looks_failed("Errors fixed: none needed")


def test_the_same_error_is_written_once_a_minute_but_counted():
    reg = _registry(lambda parameters: 1 / 0)
    reg.run("demo", {})
    reg.run("demo", {})
    lines = health.JOURNAL.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert health.describe_tool("demo") == "demo: 2 of the last 2 calls failed"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_thread_crashes_reach_the_journal():
    old_sys, old_thread = __import__("sys").excepthook, threading.excepthook
    try:
        health.install_hooks()
        t = threading.Thread(target=lambda: 1 / 0, name="worker-x")
        t.start()
        t.join()
    finally:
        __import__("sys").excepthook, threading.excepthook = old_sys, old_thread
    [entry] = health.recent()
    assert entry["kind"] == "thread" and "[worker-x] ZeroDivisionError" in entry["message"]


def test_main_refuses_a_malformed_call_before_anything_runs():
    import asyncio
    from types import SimpleNamespace
    from test_wake_flow import _jarvis
    ran = []
    j = _jarvis()
    j._tool_schemas = {"demo": SCHEMA}
    j._inline_names = set()
    j._action_registry = SimpleNamespace(
        has=lambda n: True, scheduling=lambda n: None,
        run=lambda n, a, ctx: ran.append(a) or "Opened.")
    j._plugin_registry = SimpleNamespace(has=lambda n: False, scheduling=lambda n: None)
    tool_args.reset()
    bad = asyncio.run(j._execute_tool(SimpleNamespace(name="demo", args={"count": 1}, id="1")))
    assert bad.response["result"].startswith("[ARGS_INVALID] Nothing ran: missing required action")
    assert ran == [] and health.status("demo") == "warn"
    good = asyncio.run(j._execute_tool(
        SimpleNamespace(name="demo", args={"action": "Open", "count": "2"}, id="2")))
    assert good.response["result"] == "Opened."
    assert ran == [{"action": "open", "count": 2}]
    tool_args.reset()
