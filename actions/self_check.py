"""
self_check — the assistant answers "what is broken?" about itself.

Read-only. It reports from core/health.py (the error journal and each tool's
recent success rate) and core/capabilities.py (probes of every tool, the
environment and, on request, the test suite). Fixing is a separate tool,
self_improve, which always asks first; this one never changes anything.
"""

from __future__ import annotations

from core import capabilities, health


def _ago(iso: str) -> str:
    return iso.replace("T", " ") if iso else "?"


def _status() -> str:
    s = health.summary()
    lines = []
    if s["bad"]:
        lines.append("Failing often: " + "; ".join(health.describe_tool(t) for t in s["bad"]))
    if s["warn"]:
        lines.append("Failed at least once recently: " + ", ".join(s["warn"]))
    if not s["bad"] and not s["warn"]:
        lines.append(f"No tool has failed this session ({s['ok']} used without a problem).")
    lines.append(f"Errors recorded on the current code version ({s['version'] or 'unknown'}): "
                 f"{s['errors']}.")
    if s["latest"]:
        e = s["latest"]
        lines.append(f"Latest: [{e['id']}] {e.get('tool') or e['kind']} — {e['message'][:160]} "
                     f"({_ago(e['at'])}, {e['count']}×).")
    last = capabilities.last()
    pr = last.get("probes")
    if pr:
        bad = [i for i in pr.get("items", []) if not i["ok"]]
        lines.append(f"Last self-check {_ago(pr['at'])}: {len(bad)} problem(s)"
                     + (": " + "; ".join(f"{i['name']} — {i['detail']}" for i in bad[:6])
                        if bad else "."))
    else:
        lines.append("No self-check has been run yet (action='probe').")
    t = last.get("tests")
    if t:
        lines.append(f"Last test run {_ago(t.get('at', ''))}: {t.get('passed', 0)} passed, "
                     f"{len(t.get('failed') or [])} failed"
                     + (f" ({', '.join(t['failed'][:4])})" if t.get("failed") else "") + ".")
    return "\n".join(lines)


def _errors(limit: int) -> str:
    errs = health.recent(limit=max(1, min(limit or 8, 30)))
    if not errs:
        return "The error journal is empty."
    rows = [f"[{e['id']}] {_ago(e['at'])} {e['kind']} {e.get('tool') or '-'}: "
            f"{e['message'][:140]}" + (f" ({e['count']}×)" if e["count"] > 1 else "")
            for e in errs]
    return ("Most recent first. Name an id to see the detail, or pass it to self_improve "
            "to fix it.\n" + "\n".join(rows))


def _error(err_id: str) -> str:
    e = health.get(err_id)
    if e is None:
        return f"No error with id {err_id!r} in the journal."
    out = [f"[{e['id']}] {e['kind']} in {e.get('tool') or '-'} at {_ago(e['at'])}, "
           f"code version {e.get('version') or '?'}, seen {e['count']}×.",
           f"Message: {e['message']}"]
    if e.get("where"):
        out.append(f"Where: {e['where']}")
    if e.get("args"):
        out.append(f"Arguments: {e['args']}")
    if e.get("trace"):
        out.append("Traceback (end):\n" + e["trace"][-1500:])
    return "\n".join(out)


def _probe(tests: bool, player) -> str:
    if player is not None:
        player.write_log("SYS: Self-check running…")
    result = capabilities.run_probes()
    text = capabilities.describe(result)
    if tests:
        t = capabilities.run_tests()
        capabilities.store_tests(t)
        if t.get("error") and not t.get("total"):
            text += f"\nTests: could not run — {t['error'][:300]}"
        else:
            text += (f"\nTests: {t['passed']} passed, {len(t['failed'])} failed, "
                     f"{t['skipped']} skipped in {t.get('seconds', 0)} s.")
            if t["failed"]:
                text += "\nFailing: " + ", ".join(t["failed"][:8])
    if player is not None:
        bad = sum(1 for i in result["items"] if not i["ok"])
        player.write_log(f"SYS: Self-check done — {bad} problem(s).")
    return (text + "\n\nTell the user the outcome in one or two short sentences: what "
            "is broken, if anything. Offer self_improve only for a code error, not for a "
            "missing package, key or device — those the user fixes: tell them the install "
            "command from the result; do not run installs yourself.")


def self_check(parameters: dict, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "status").strip().lower()
    try:
        if action == "errors":
            return _errors(int(p.get("limit") or 8))
        if action == "error":
            return _error(str(p.get("error_id") or ""))
        if action == "probe":
            return _probe(bool(p.get("tests")), player)
        return _status()
    except Exception as e:
        health.failed(e)
        return f"Self-check failed: {e}"


TOOL = {
    "name": "self_check",
    "description": (
        "The assistant's own health: what is broken in JARVIS itself and why. Use "
        "for 'what's broken?', 'do you have errors?', 'check yourself', 'run a "
        "self-diagnostic', 'why did that fail?', 'show the last error'. "
        "action='status' (default) summarises failing tools, recent errors and the "
        "last checks; 'errors' lists the error journal with ids; 'error' with "
        "error_id shows one in detail (traceback); 'probe' checks every tool, the "
        "packages, git, disk, key and audio devices (tests=true also runs the test "
        "suite, ~20 s). Read-only. Not for the computer's CPU/RAM (system_status). "
        "To fix a code error, use self_improve."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["status", "errors", "error", "probe"],
                       "description": "status | errors | error | probe"},
            "error_id": {"type": "STRING", "description": "error: the id from the journal"},
            "limit": {"type": "INTEGER", "description": "errors: how many (default 8)"},
            "tests": {"type": "BOOLEAN", "description": "probe: also run the test suite"},
        },
    },
    "handler": self_check,
    "behavior": "NON_BLOCKING",
    "scheduling": "WHEN_IDLE",
}
