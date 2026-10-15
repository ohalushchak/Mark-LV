"""
actions/delegate_to_agent.py — the model's handle on core/agents.py.

ask → the user answers → run starts Claude Code or Codex in the current
project; status, cancel; and the panel's choices by voice (set engine, model,
effort; usage). The design and its safeguards are documented in
core/agents.py.
"""

from __future__ import annotations

from core import agents, projects


def _engine_name(value) -> str:
    v = str(value or "").strip().lower().replace("-", " ")
    if not v:
        return ""
    if "codex" in v or "chatgpt" in v or "openai" in v:
        return "codex"
    if "claude" in v or "anthropic" in v:
        return "claude"
    return v


def delegate_to_agent(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "ask").strip().lower()

    if action == "ask":
        # A named project is used for this job but does not become the
        # current one: in the second live test the model passed the project
        # of a session the relay had just announced ("Pedigree"), and the
        # agent went to work there instead of in the project in use.
        cur = projects.current()
        cwd, problem = projects.resolve(str(p.get("project") or ""), make_current=False)
        if cwd is None:
            return problem
        named = _engine_name(p.get("engine"))
        if named and named != agents.current()["engine"]:
            agents.set_engine(named)          # "pass it to Codex" picks Codex
        out = agents.ask(str(p.get("task") or ""), cwd,
                         follow_up=bool(p.get("follow_up")), player=player)
        if out.startswith("[ASK_USER]") and cur is not None and cwd.resolve() != cur.resolve():
            out += (f" IMPORTANT: this is the project {cwd.name}, not the current "
                    f"project {cur.name}. Say the project name clearly when asking; if "
                    f"the user did not name {cwd.name} themselves, ask again without "
                    f"'project' to use {cur.name}.")
        return out
    if action == "run":
        return agents.run(str(p.get("details") or ""), player=player, speak=speak)
    if action == "status":
        return agents.status()
    if action == "cancel":
        return agents.cancel()
    if action == "settings":
        cur = agents.current()
        eng = cur["engine"]
        models = ", ".join(m.id for m in agents.models(eng)) or "unknown"
        return (f"Agent: {agents.LABEL[eng]}; model: {cur['model'] or 'its default'}; "
                f"effort: {cur['effort'] or 'its default'}. Models available: {models}. "
                f"Efforts for this model: {', '.join(agents.efforts(eng, cur['model']))}. "
                + agents.describe_usage(eng))
    if action == "set":
        out = []
        engine = _engine_name(p.get("engine"))
        if engine:
            out.append(agents.set_engine(engine))
        if p.get("model") is not None and str(p.get("model")).strip() != "":
            model = str(p["model"]).strip()
            out.append(agents.set_model("" if model.lower() == "default" else model))
        if p.get("effort") is not None and str(p.get("effort")).strip() != "":
            effort = str(p["effort"]).strip()
            out.append(agents.set_effort("" if effort.lower() == "default" else effort))
        return " ".join(out) or "Say what to change: engine, model or effort."
    if action == "usage":
        engine = str(p.get("engine") or agents.current()["engine"]).strip().lower()
        engine = engine if engine in agents.ENGINES else agents.current()["engine"]
        if p.get("refresh"):
            return agents.refresh_usage(engine)
        return agents.describe_usage(engine)
    return "Unknown action. Use ask, run, status, cancel, settings, set or usage."


def _probe() -> tuple[bool, str]:
    """self_check: the selected agent is installed and signed in."""
    engine = agents.current()["engine"]
    exe = agents.find_exe(engine)
    if not exe:
        return False, f"{agents.LABEL[engine]} is not installed"
    if agents.signed_in(engine, exe) is False:
        return False, f"{agents.LABEL[engine]} is not signed in"
    return True, f"{agents.LABEL[engine]} is ready"

TOOL = {
    "name": "delegate_to_agent",
    "description": (
        "Hands a coding job in the current project to a coding agent — Claude "
        "Code or Codex — which works on its own in the background and reports "
        "back. Use it whenever the user asks Claude, Claude Code or Codex to do "
        "something with code: 'pass to Claude: add a subtract function with a "
        "test', 'let Codex run the tests and tell me', 'ask Claude Code to fix "
        "the build', or any large job ('add CSV export with tests', 'refactor the "
        "audio module'). Put the agent the user named in 'engine' (claude or "
        "codex); without one, the selected agent works. It spends the user's "
        "plan, so for a small change the user did not address to an agent, "
        "prefer project_workspace, run_command and git_project. Always two calls: "
        "action='ask' with the task (the tool tells you to ask the user), then "
        "action='run' only after the user agreed. follow_up=true continues the "
        "agent's previous session in this project ('now add tests for that'). "
        "status/cancel check or stop it. settings/set/usage show or change the "
        "agent (engine claude|codex), its model and effort, and its usage limits "
        "('switch to Codex', 'effort high', 'how much limit is left?'; "
        "refresh=true fetches fresh figures). Not for JARVIS's own code — fixing or "
        "improving the assistant itself is self_improve."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "description": "ask | run | status | cancel | settings | set | usage"},
            "task": {"type": "STRING",
                     "description": "ask: the full task for the agent, in English, with "
                                    "every detail the user gave"},
            "follow_up": {"type": "BOOLEAN",
                          "description": "ask: continue the agent's previous session"},
            "details": {"type": "STRING",
                        "description": "run: anything the user added when agreeing"},
            "project": {"type": "STRING",
                        "description": "ask: project name or folder; omit for the current one"},
            "engine": {"type": "STRING",
                       "description": "ask/set/usage: claude or codex — the agent the user named"},
            "model": {"type": "STRING",
                      "description": "set: model id (e.g. opus, sonnet, gpt-6-sol) or 'default'"},
            "effort": {"type": "STRING",
                       "description": "set: low | medium | high | xhigh | max (| ultra) or 'default'"},
            "refresh": {"type": "BOOLEAN", "description": "usage: fetch fresh figures"},
        },
    },
    "handler": delegate_to_agent,
    "probe": _probe,
}
