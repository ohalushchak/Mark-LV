"""
self_improve — the assistant fixes an error in its own code or makes a change
to itself, through the coding agent, with the user deciding.

The work happens in core/self_repair.py: a separate copy of the code, tests
before and after, the agent's change checked against protected files, then a
review on the HUD. This tool can ask, start, report, show and stop — it has no
way to apply a change: that is the HUD's APPLY button.
"""

from __future__ import annotations

from core import health, self_repair


def self_improve(parameters: dict, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "status").strip().lower()
    try:
        if action in ("fix", "improve"):
            return self_repair.ask(action, task=str(p.get("task") or ""),
                                   error_id=str(p.get("error_id") or ""), player=player)
        if action == "run":
            return self_repair.run(details=str(p.get("details") or ""), player=player)
        if action == "review":
            return self_repair.review()
        if action == "discard":
            return self_repair.discard()
        if action == "cancel":
            return self_repair.cancel()
        if action == "restart":
            return self_repair.restart(player=player)
        return self_repair.describe()
    except Exception as e:
        health.failed(e)
        return f"Self-repair failed: {e}"


TOOL = {
    "name": "self_improve",
    "description": (
        "Changes JARVIS's OWN code: fixes an error the assistant had, or improves "
        "one of its features, by handing the job to the coding agent (Claude Code "
        "or Codex) in a separate copy of the code. Use for 'fix that error', 'fix "
        "yourself', 'repair the weather tool', 'improve yourself so that…', 'add to "
        "your weather a weekly forecast'. Always two calls: action='fix' (optional "
        "error_id from self_check; without one, the latest error) or 'improve' with "
        "the task in English — the tool tells you to ask the user — then 'run' only "
        "after they agreed. The result is announced when it is ready; 'review' puts "
        "the diff on the HUD, where ONLY the user applies or discards it — never "
        "claim it is applied. 'status' reports progress, 'cancel' stops it, "
        "'discard' throws a ready change away at the user's request, 'restart' "
        "reloads JARVIS after an applied change, only once the user agreed. For "
        "code in the user's own projects use delegate_to_agent; to find out what "
        "is broken use self_check."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "enum": ["fix", "improve", "run", "status", "review", "discard",
                                "cancel", "restart"],
                       "description": "fix | improve | run | status | review | discard | "
                                      "cancel | restart"},
            "task": {"type": "STRING",
                     "description": "improve: the change in full, in English; fix: anything "
                                    "the user said about the problem"},
            "error_id": {"type": "STRING",
                         "description": "fix: the error id from self_check; omit for the latest"},
            "details": {"type": "STRING",
                        "description": "run: anything the user added when agreeing"},
        },
    },
    "handler": self_improve,
}
