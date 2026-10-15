"""
core/agents.py — hand a large coding job to Claude Code or Codex.

WHAT IT IS FOR
    Gemini Live is the assistant's voice: quick, good at picking one tool. It
    is not built to work through a job — read a project, decide, edit, run the
    tests, try again. Coding agents are, and two of them are already installed
    and signed in on this machine: Claude Code (`claude -p`) and Codex
    (`codex exec`). This module runs either one headless in the current
    project, on the user's own plan, in the background, and hands the outcome
    back to the conversation when it is done.

    Built on the rayhao-han/Mark-LV fork's claude_agent, extended to a second
    engine; session resume and defensive event parsing come from the
    deepakchouguledatascience/Mark-LIII fork's opencode_agent.

ALWAYS ASKED, AND THE MODEL CANNOT ANSWER FOR THE USER
    Two calls, never one: ask() registers the task and tells the model to ask
    the user; run() starts it — refused unless the user spoke or typed AFTER
    the assistant finished asking (core/activity.py keeps that order). The task
    is fixed at ask(), so what the user agreed to is what runs. The gate cannot
    tell "yes" from "no" in every language — that part is the model's
    judgement — but it guarantees the question was asked and answered.

INSIDE THE TASK
    Nobody can answer the agent's questions while it works:
      * Claude Code runs in its `auto` permission mode with prompts off: a
        classifier lets ordinary work through and blocks risky steps, which
        are reported rather than worked around.
      * Codex runs with the workspace-write sandbox: it can change files in the
        project, nothing outside it.
    The agent's changes are NOT on the undo stack; git_project's diff shows
    them. Only one agent job runs at a time, so two cannot edit one project.

PLAN, NOT API BILLING
    The user chose their subscription, so API keys and endpoint overrides in
    the environment are removed before starting either CLI. Variables set by a
    surrounding Claude Code session are removed too: with them, the CLI refuses
    to start (the assistant is often launched from such a terminal).
    JARVIS_AGENT_RUN=1 is set so the session-relay hook (core/agent_relay.py)
    does not announce these runs a second time.

USAGE LIMITS
    Each Claude run reports the 5-hour and weekly windows in a
    `rate_limit_event`; there is no free way to read them otherwise, so a
    refresh is a minimal haiku request. Codex's limits come from its
    app-server (`account/rateLimits/read`), which costs nothing. The last known
    values are kept in the config so the panel has numbers after a restart.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core import activity, announce, jobs

_OS = platform.system()

ENGINES = ("claude", "codex")
LABEL = {"claude": "Claude Code", "codex": "Codex"}

ASK_TTL = 180.0          # seconds an unanswered question stays valid
_REPORT_CHARS = 1500     # how much of the agent's report goes back to the model
_STEP_LOG_GAP = 2.5      # min seconds between progress lines in the activity log
WARN_PCT = 90.0          # warn before starting at this much of a window used

# Claude Code takes these aliases for "the latest of that family".
CLAUDE_MODELS = [("fable", "Fable"), ("opus", "Opus"), ("sonnet", "Sonnet"),
                 ("haiku", "Haiku")]
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# Efforts that burn through a plan noticeably faster; the panel marks them.
COSTLY_EFFORTS = {"max", "ultra"}

_STRIP_ENV = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
              "CLAUDECODE", "CLAUDE_PID", "CLAUDE_AGENT_SDK_VERSION",
              "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY"}
_KEEP_ENV = {"CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CODE_OAUTH_TOKEN"}

_SYSTEM = (
    "You were started by the user's desktop voice assistant to do one task the "
    "user approved out loud. Nobody can answer questions or approve prompts while "
    "you work: make sensible choices and finish the job. If a step is blocked or "
    "impossible, say so rather than working around it. Do not delete or overwrite "
    "the user's existing files unless the task names them, and do not commit or "
    "push unless the task says so. Your final message is shown to the user and "
    "summarised aloud, so end with plain language and no markdown: what you did, "
    "where the results are, and anything they need to do. Two to five sentences."
)


# ── models and efforts ──────────────────────────────────────────────────────

@dataclass
class Model:
    id: str
    label: str
    efforts: list[str]
    default_effort: str = ""
    is_default: bool = False


_codex_models: list[Model] = []
_codex_models_at = 0.0
_models_lock = threading.Lock()


def _codex_rpc(calls: list[tuple[str, dict]], timeout: float = 20.0) -> list[dict | None]:
    """Ask Codex's app-server a few questions over stdio JSON-RPC. Returns one
    result (or None) per call. Starting it takes ~0.2 s and costs no usage."""
    exe = find_exe("codex")
    if not exe:
        return [None] * len(calls)
    try:
        proc = subprocess.Popen([exe, "app-server"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=_child_env(), **jobs.hidden_window_flags())
    except OSError:
        return [None] * len(calls)
    results: dict[int, dict] = {}
    done = threading.Event()
    want = set(range(2, 2 + len(calls)))

    def reader():
        try:
            for raw in proc.stdout:
                try:
                    msg = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                mid = msg.get("id")
                if isinstance(mid, int) and ("result" in msg or "error" in msg):
                    results[mid] = msg
                    if mid == 1:
                        init_done.set()
                    if want <= results.keys():
                        done.set()
        except Exception:
            pass

    init_done = threading.Event()
    threading.Thread(target=reader, daemon=True).start()

    def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        proc.stdin.flush()
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "jarvis", "version": "1"}}})
        if init_done.wait(timeout):
            send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            for n, (method, params) in enumerate(calls, 2):
                send({"jsonrpc": "2.0", "id": n, "method": method, "params": params})
            done.wait(timeout)
    except Exception:
        pass
    finally:
        try:
            proc.kill()
        except Exception:
            pass
    return [(results.get(n) or {}).get("result") for n in range(2, 2 + len(calls))]


def _codex_models_from_cache() -> list[Model]:
    try:
        data = json.loads((Path.home() / ".codex" / "models_cache.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for m in data.get("models") or []:
        if not isinstance(m, dict) or m.get("visibility") == "hide":
            continue
        eff = [e.get("effort") for e in m.get("supported_reasoning_levels") or []
               if isinstance(e, dict) and e.get("effort")]
        out.append(Model(m.get("slug", ""), m.get("display_name") or m.get("slug", ""),
                         eff, m.get("default_reasoning_level") or ""))
    return [m for m in out if m.id]


def models(engine: str, refresh: bool = False) -> list[Model]:
    """The models this engine offers. Codex's list comes from its app-server,
    which knows what the signed-in account may use — the local cache can list
    models the account is refused (seen: the config's default model was)."""
    if engine == "claude":
        return [Model(mid, label, list(CLAUDE_EFFORTS)) for mid, label in CLAUDE_MODELS]
    global _codex_models, _codex_models_at
    with _models_lock:
        fresh = _codex_models and time.monotonic() - _codex_models_at < 600
        if fresh and not refresh:
            return list(_codex_models)
    got: list[Model] = []
    res = _codex_rpc([("model/list", {})])[0]
    for m in (res or {}).get("data") or []:
        if not isinstance(m, dict) or m.get("hidden"):
            continue
        eff = [e.get("reasoningEffort") for e in m.get("supportedReasoningEfforts") or []
               if isinstance(e, dict) and e.get("reasoningEffort")]
        got.append(Model(m.get("model") or m.get("id", ""),
                         m.get("displayName") or m.get("id", ""), eff,
                         m.get("defaultReasoningEffort") or "", bool(m.get("isDefault"))))
    if not got:
        got = _codex_models_from_cache()
    with _models_lock:
        if got:
            _codex_models, _codex_models_at = got, time.monotonic()
        return list(_codex_models)


def _model(engine: str, model_id: str) -> Model | None:
    for m in models(engine):
        if m.id == model_id:
            return m
    return None


def efforts(engine: str, model_id: str) -> list[str]:
    if engine == "claude":
        return list(CLAUDE_EFFORTS)
    m = _model(engine, model_id) if model_id else next(
        (x for x in models(engine) if x.is_default), None)
    return list(m.efforts) if m else ["low", "medium", "high", "xhigh", "max"]


# ── settings ────────────────────────────────────────────────────────────────

_listeners: list[Callable[[], None]] = []


def on_change(fn: Callable[[], None]) -> None:
    """fn() runs (on any thread) whenever settings, usage or the job change."""
    _listeners.append(fn)


def _changed() -> None:
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


def current() -> dict:
    from memory.config_manager import get_agent_settings
    s = get_agent_settings()
    eng = s["engine"]
    return {"engine": eng, "model": s[f"{eng}_model"], "effort": s[f"{eng}_effort"],
            "timeout_min": s["timeout_min"]}


def set_engine(engine: str) -> str:
    from memory.config_manager import save_agent_settings
    engine = str(engine or "").strip().lower()
    if engine not in ENGINES:
        return "Unknown agent. Choose claude or codex."
    save_agent_settings(engine=engine)
    _changed()
    return f"The coding agent is now {LABEL[engine]}."


def set_model(model_id: str, engine: str | None = None) -> str:
    """Set the model ('' = the CLI's own default). An effort the new model does
    not support falls back to that model's default."""
    from memory.config_manager import get_agent_settings, save_agent_settings
    engine = engine or current()["engine"]
    model_id = str(model_id or "").strip()
    if model_id and _model(engine, model_id) is None:
        names = ", ".join(m.id for m in models(engine))
        return f"{LABEL[engine]} has no model '{model_id}'. Available: {names}."
    s = get_agent_settings()
    eff = s[f"{engine}_effort"]
    note = ""
    allowed = efforts(engine, model_id)
    if eff and eff not in allowed:
        m = _model(engine, model_id)
        new_eff = (m.default_effort if m and m.default_effort in allowed else "")
        note = f" Effort {eff} is not available for it, so it is now " \
               f"{new_eff or 'the default'}."
        eff = new_eff
    save_agent_settings(**{f"{engine}_model": model_id, f"{engine}_effort": eff})
    _changed()
    return f"{LABEL[engine]} will use {model_id or 'its default model'}.{note}"


def set_effort(effort: str, engine: str | None = None) -> str:
    from memory.config_manager import get_agent_settings, save_agent_settings
    engine = engine or current()["engine"]
    effort = str(effort or "").strip().lower()
    model_id = get_agent_settings()[f"{engine}_model"]
    allowed = efforts(engine, model_id)
    if effort and effort not in allowed:
        return f"That model offers these efforts: {', '.join(allowed)}."
    save_agent_settings(**{f"{engine}_effort": effort})
    _changed()
    return f"{LABEL[engine]} effort set to {effort or 'its default'}."


# ── finding and checking the CLIs ───────────────────────────────────────────

def find_exe(engine: str) -> str | None:
    from memory.config_manager import load_api_keys
    override = str((load_api_keys().get("agents") or {}).get(f"{engine}_path") or "")
    if override and Path(override).is_file():
        return override
    exe = shutil.which(engine)
    if exe:
        return exe
    home = Path.home()
    extra = {"claude": [home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude",
                        home / ".claude" / "local" / "claude"],
             "codex": [Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd"]}
    for p in extra.get(engine, []):
        if p.is_file():
            return str(p)
    return None


def _child_env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in _STRIP_ENV and (not k.startswith("CLAUDE_CODE_") or k in _KEEP_ENV)}
    env["JARVIS_AGENT_RUN"] = "1"
    return env


def signed_in(engine: str, exe: str) -> bool | None:
    """True / False when the CLI says so; None when it cannot be read."""
    argv = [exe, "auth", "status"] if engine == "claude" else [exe, "login", "status"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=20,
                           env=_child_env(), stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace", **jobs.hidden_window_flags())
    except Exception:
        return None
    if engine == "claude":
        try:
            return bool(json.loads(r.stdout).get("loggedIn"))
        except ValueError:
            return r.returncode == 0 if r.returncode in (0, 1) else None
    return r.returncode == 0 and "logged in" in (r.stdout + r.stderr).lower()


def _login_hint(engine: str, exe: str) -> str:
    return f'"{exe}" auth login' if engine == "claude" else f'"{exe}" login'


# ── usage limits ────────────────────────────────────────────────────────────

_usage: dict[str, dict] = {}
_usage_lock = threading.Lock()


def _load_usage() -> None:
    from memory.config_manager import get_agent_settings
    try:
        saved = get_agent_settings().get("usage") or {}
    except Exception:
        saved = {}
    with _usage_lock:
        for eng in ENGINES:
            if isinstance(saved.get(eng), dict) and eng not in _usage:
                _usage[eng] = saved[eng]


def usage(engine: str) -> dict:
    """{'five_hour': {'pct', 'resets_at'}, 'week': {...}, 'plan', 'limited',
    'updated'} — empty when nothing is known yet."""
    _load_usage()
    with _usage_lock:
        return json.loads(json.dumps(_usage.get(engine) or {}))


def _store_usage(engine: str, data: dict) -> None:
    data["updated"] = time.time()
    with _usage_lock:
        _usage[engine] = data
        snapshot = json.loads(json.dumps(_usage))
    try:
        from memory.config_manager import save_agent_settings
        save_agent_settings(usage=snapshot)
    except Exception:
        pass
    _changed()


def usage_from_claude_event(info: dict) -> dict:
    """A Claude Code `rate_limit_event`'s rate_limit_info → our shape."""
    out: dict = {}
    windows = info.get("unifiedWindows") or {}
    for key, name in (("five_hour", "five_hour"), ("seven_day", "week")):
        w = windows.get(key)
        if isinstance(w, dict) and w.get("utilization") is not None:
            out[name] = {"pct": round(float(w["utilization"]) * 100, 1),
                         "resets_at": int(w.get("resetsAt") or 0)}
    if not out and info.get("rateLimitType") == "five_hour" and info.get("utilization") is not None:
        out["five_hour"] = {"pct": round(float(info["utilization"]) * 100, 1),
                            "resets_at": int(info.get("resetsAt") or 0)}
    out["limited"] = str(info.get("status") or "").lower() not in ("allowed", "allowed_warning", "")
    return out


def usage_from_codex(res: dict) -> dict:
    """Codex app-server `account/rateLimits/read` result → our shape."""
    rl = (res or {}).get("rateLimits") or {}
    out: dict = {"plan": str(rl.get("planType") or "")}
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        mins = int(w.get("windowDurationMins") or 0)
        name = "five_hour" if mins and mins <= 600 else "week"
        out[name] = {"pct": float(w.get("usedPercent") or 0),
                     "resets_at": int(w.get("resetsAt") or 0)}
    out["limited"] = bool(rl.get("rateLimitReachedType")) or \
        res.get("ordinaryUsageAllowed") is False
    return out


def refresh_usage(engine: str) -> str:
    """Fetch fresh limits. Codex: free. Claude: a minimal haiku request."""
    if engine == "codex":
        res = _codex_rpc([("account/rateLimits/read", {})])[0]
        if not res:
            return "Codex did not report its limits (is it installed and signed in?)."
        _store_usage("codex", usage_from_codex(res))
        return describe_usage("codex")
    exe = find_exe("claude")
    if not exe:
        return "Claude Code is not installed."
    argv = [exe, "-p", "--output-format", "stream-json", "--verbose", "--model", "haiku",
            "--effort", "low", "--permission-mode", "plan", "--permission-prompts", "none"]
    try:
        r = subprocess.run(argv, input="Reply with the single word: ok", capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=90,
                           cwd=tempfile.gettempdir(), env=_child_env(),
                           **jobs.hidden_window_flags())
    except Exception as e:
        return f"Could not ask Claude Code for its limits: {e}"
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "rate_limit_event":
            _store_usage("claude", usage_from_claude_event(ev.get("rate_limit_info") or {}))
            return describe_usage("claude")
    return "Claude Code did not report its limits."


def _clock(epoch: int) -> str:
    if not epoch:
        return ""
    t = time.localtime(epoch)
    if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d"):
        return time.strftime("%H:%M", t)
    return time.strftime("%a %H:%M", t)


def describe_usage(engine: str) -> str:
    u = usage(engine)
    if not any(k in u for k in ("five_hour", "week")):
        return f"No usage figures for {LABEL[engine]} yet."
    parts = []
    for key, name in (("five_hour", "5-hour window"), ("week", "weekly window")):
        w = u.get(key)
        if w:
            parts.append(f"{name} {w['pct']:.0f}% used"
                         + (f", resets {_clock(w.get('resets_at', 0))}" if w.get("resets_at") else ""))
    age = int(time.time() - float(u.get("updated") or 0)) // 60
    when = "just now" if age < 1 else f"{age} min ago"
    limited = " The limit is reached right now." if u.get("limited") else ""
    return f"{LABEL[engine]}: {'; '.join(parts)} (as of {when}).{limited}"


def _limit_problem(engine: str) -> tuple[str, str]:
    """('blocked'|'warn'|'', sentence)."""
    u = usage(engine)
    other = LABEL["codex" if engine == "claude" else "claude"]
    for key, name in (("five_hour", "5-hour"), ("week", "weekly")):
        w = u.get(key) or {}
        pct = float(w.get("pct") or 0)
        reset = _clock(int(w.get("resets_at") or 0))
        if u.get("limited") or pct >= 100:
            return "blocked", (f"{LABEL[engine]}'s {name} limit is used up"
                               + (f" until {reset}" if reset else "")
                               + f". Offer {other} instead.")
        if pct >= WARN_PCT:
            return "warn", (f"{LABEL[engine]} has used {pct:.0f}% of its {name} limit"
                            + (f" (resets {reset})" if reset else "")
                            + f"; mention it and offer {other} as the alternative.")
    return "", ""


# ── the job ─────────────────────────────────────────────────────────────────

@dataclass
class Job:
    engine: str
    task: str
    cwd: str
    model: str = ""
    effort: str = ""
    details: str = ""
    resume_id: str = ""
    started: float = field(default_factory=time.monotonic)
    proc: subprocess.Popen | None = None
    steps: int = 0
    last_step: str = ""
    done: bool = False
    ok: bool = False
    report: str = ""
    killed: str = ""
    session_id: str = ""
    denied: int = 0
    files: set = field(default_factory=set)
    ended: float = 0.0
    # Set for a job another module owns (core/self_repair.py): its own rules
    # instead of _SYSTEM, and its own handling of the outcome instead of the
    # announcement below — that module announces once it has checked the work.
    system: str = ""
    on_done: Callable[["Job"], None] | None = None

    @property
    def minutes(self) -> float:
        return ((self.ended or time.monotonic()) - self.started) / 60

    @property
    def elapsed(self) -> str:
        secs = (self.ended or time.monotonic()) - self.started
        return f"{secs:.0f} s" if secs < 90 else f"{secs / 60:.0f} min"


_lock = threading.Lock()
_pending: dict | None = None
_job: Job | None = None
_sessions: dict[tuple[str, str], str] = {}    # (engine, project) → last session id
_player = None                                 # set by the action for HUD output


def job() -> Job | None:
    with _lock:
        return _job


def state() -> dict:
    """A snapshot for the panel."""
    cur = current()
    j = job()
    return {
        **cur,
        "usage": {e: usage(e) for e in ENGINES},
        "job": None if j is None else {
            "engine": j.engine, "task": j.task, "done": j.done, "ok": j.ok,
            "steps": j.steps, "last_step": j.last_step, "minutes": j.minutes,
            "killed": j.killed},
    }


def _busy() -> str:
    j = job()
    if j and not j.done:
        return (f"{LABEL[j.engine]} is still working on an earlier task ({j.task[:80]}), "
                f"started {j.elapsed} ago. Tell the user; they can wait or ask "
                f"you to cancel it.")
    return ""


def ask(task: str, cwd: Path, follow_up: bool = False, player=None) -> str:
    global _pending
    task = str(task or "").strip()
    if not task:
        return "No task given. Call again with 'task' describing exactly what the user wants done."
    busy = _busy()
    if busy:
        return busy
    cur = current()
    engine = cur["engine"]
    exe = find_exe(engine)
    if not exe:
        _log(player, f"SYS: {LABEL[engine]} was not found. Install it, or switch the agent "
                     f"in the AGENT panel.")
        return (f"{LABEL[engine]} is not installed on this computer, so nothing can be "
                f"handed off. Tell the user in one sentence.")
    if signed_in(engine, exe) is False:
        _log(player, f"SYS: {LABEL[engine]} is not signed in. Run once in a terminal:  "
                     f"{_login_hint(engine, exe)}")
        return (f"{LABEL[engine]} is installed but not signed in, so nothing was started. "
                f"Tell the user in one sentence; the command is in the log.")
    level, sentence = _limit_problem(engine)
    if level == "blocked":
        return f"Nothing was started. {sentence} Ask the user whether to switch."
    resume = _sessions.get((engine, str(cwd))) if follow_up else ""
    with _lock:
        _pending = {"task": task, "asked_at": time.monotonic(), "engine": engine,
                    "cwd": str(cwd), "resume": resume or ""}
    how = f"{LABEL[engine]}" + (f" ({cur['model']}" + (f", effort {cur['effort']}" if cur['effort'] else "") + ")"
                                if cur["model"] else "")
    cont = " It continues the previous session in this project." if resume else ""
    warn = f" Also: {sentence}" if level == "warn" else ""
    return (
        f"[ASK_USER] Nothing has started. Ask the user now, in ONE short sentence in "
        f"their language and your own words, whether to hand this to {how} in the "
        f"project {Path(cwd).name}. Name the task in a few words.{cont}{warn} Then END "
        f"YOUR TURN — do not call action='run' in this turn; their spoken answer must "
        f"come first. If they agree, call delegate_to_agent with "
        f"action='run', putting anything they added in 'details'. If they decline, "
        f"drop it."
    )


def run(details: str = "", player=None, speak=None) -> str:
    global _pending, _job
    with _lock:
        p = _pending
    if p is None or time.monotonic() - p["asked_at"] > ASK_TTL:
        with _lock:
            _pending = None
        return ("Nothing is waiting for the user's approval, so nothing was started. Call "
                "delegate_to_agent with action='ask' first, then wait for the answer.")
    if not activity.user_answered_since(p["asked_at"]):
        return ("[NOT_STARTED] Nothing is running: you called run before the user answered "
                "your question. Do NOT say it has started. End your turn now and wait for "
                "their reply; if they agree, call delegate_to_agent with action='run' again.")
    engine = p["engine"]
    exe = find_exe(engine)
    if not exe:
        return f"{LABEL[engine]} is no longer available. Nothing was started."
    cur = current()
    model = cur["model"] if cur["engine"] == engine else ""
    if engine == "codex" and not model:
        # "Default" means the account's default, named explicitly: the model in
        # Codex's own config.toml can be one the account is refused (seen on
        # this machine: gpt-6.1-sol with a ChatGPT sign-in).
        model = next((m.id for m in models("codex") if m.is_default), "")
    with _lock:
        if _job and not _job.done:
            return _busy() or "An agent is busy."
        _pending = None
        j = Job(engine=engine, task=p["task"], cwd=p["cwd"], model=model,
                effort=cur["effort"] if cur["engine"] == engine else "",
                details=str(details or "").strip(), resume_id=p["resume"])
        _job = j
    _log(player, f"SYS: {LABEL[engine]} started: {j.task[:90]}")
    threading.Thread(target=_work, args=(j, exe, player), name="coding-agent",
                     daemon=True).start()
    _changed()
    return (f"[AGENT_STARTED] {LABEL[engine]} is working on it in the background. Tell "
            f"the user in a few words that it is underway. Do not wait for it: the "
            f"outcome is announced later.")


def status() -> str:
    j = job()
    if j is None:
        return "No agent has been given a task this session."
    if not j.done:
        latest = f" Latest step: {j.last_step}." if j.last_step else ""
        return (f"{LABEL[j.engine]} is still working on: {j.task[:120]}. "
                f"{j.elapsed} in, {j.steps} step(s) so far.{latest}")
    state_ = "finished" if j.ok else "did not finish"
    return f"{LABEL[j.engine]}'s last task ({j.task[:120]}) {state_}. Its report: {j.report[:700]}"


def start_owned(task: str, cwd: Path, system: str, on_done: Callable[[Job], None],
                player=None) -> str:
    """Start a job for another module, which has already asked the user.
    Returns '' when started, else a sentence saying why not. The selected
    engine, model and effort apply, as for any job."""
    global _job
    cur = current()
    engine = cur["engine"]
    exe = find_exe(engine)
    if not exe:
        return f"{LABEL[engine]} is not installed on this computer."
    level, sentence = _limit_problem(engine)
    if level == "blocked":
        return sentence
    model = cur["model"]
    if engine == "codex" and not model:
        model = next((m.id for m in models("codex") if m.is_default), "")
    with _lock:
        if _job and not _job.done:
            return _busy() or "An agent is busy."
        j = Job(engine=engine, task=task, cwd=str(cwd), model=model, effort=cur["effort"],
                system=system, on_done=on_done)
        _job = j
    _log(player, f"SYS: {LABEL[engine]} started: {task[:90]}")
    threading.Thread(target=_work, args=(j, exe, player), name="coding-agent",
                     daemon=True).start()
    _changed()
    return ""


def cancel() -> str:
    global _pending
    with _lock:
        j = _job
        had_question = _pending is not None
        _pending = None
    if j and not j.done and j.proc is not None:
        _kill(j, "cancelled by the user")
        return f"{LABEL[j.engine]} has been stopped. Anything it already changed stays changed."
    if had_question:
        return "Dropped. The agent was never started."
    return "No agent is working on anything."


# ── running a job ───────────────────────────────────────────────────────────

def build_argv(j: Job, exe: str) -> list[str]:
    if j.engine == "claude":
        argv = [exe, "-p", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "auto", "--permission-prompts", "none",
                "--append-system-prompt", j.system or _SYSTEM]
        if j.model:
            argv += ["--model", j.model]
        if j.effort:
            argv += ["--effort", j.effort]
        if j.resume_id:
            argv += ["--resume", j.resume_id]
        return argv
    argv = [exe, "exec"]
    if j.resume_id:
        # `exec resume` takes neither --sandbox nor -C: the sandbox goes in as
        # a config value and the folder is the process's working directory.
        argv += ["resume", j.resume_id, "-c", 'sandbox_mode="workspace-write"']
    else:
        argv += ["--sandbox", "workspace-write", "-C", j.cwd]
    argv += ["--json", "--skip-git-repo-check"]
    if j.model:
        argv += ["-m", j.model]
    if j.effort:
        argv += ["-c", f'model_reasoning_effort="{j.effort}"']
    return argv + ["-"]


def _prompt(j: Job) -> str:
    text = j.task
    if j.details:
        text += f"\n\nWhen approving, the user added: {j.details}"
    if j.engine == "codex":
        text = f"{j.system or _SYSTEM}\n\nTask:\n{text}"   # codex exec has no system-prompt flag
    return text


def _work(j: Job, exe: str, player) -> None:
    try:
        ok, report = _run(j, exe, player)
    except Exception as e:
        ok, report = False, f"{LABEL[j.engine]} could not be run: {e}"
    j.ended = time.monotonic()
    j.ok, j.report, j.done = ok, report, True
    if j.session_id:
        _sessions[(j.engine, j.cwd)] = j.session_id
    if j.on_done is not None:
        _log(player, f"SYS: {LABEL[j.engine]} {'finished' if ok else 'did not finish'} "
                     f"after {j.elapsed}.")
        try:
            j.on_done(j)
        except Exception as e:
            _log(player, f"ERR: handling {LABEL[j.engine]}'s result failed — {e}")
        _changed()
        return
    _log(player, f"SYS: {LABEL[j.engine]} {'finished' if ok else 'did not finish'} "
                 f"after {j.elapsed} — report on screen.")
    if player is not None:
        try:
            player.show_content(f"{LABEL[j.engine].upper()} — {j.task[:30]}", report)
        except Exception:
            pass
    files = ""
    if j.files:
        names = sorted(Path(f).name for f in j.files)
        files = f"\nFiles it changed: {', '.join(names[:12])}" + (" …" if len(names) > 12 else "")
    verdict = "finished" if ok else "did NOT finish"
    announce.say(
        f"[AGENT_RESULT] {LABEL[j.engine]} {verdict} the task '{j.task[:200]}' after "
        f"{j.elapsed} and {j.steps} step(s).{files}\n\nIts report:\n"
        f"{report[:_REPORT_CHARS]}\n\nSummarise for the user in one or two short spoken "
        f"sentences: the outcome first, then anything they need to do. No markdown, no "
        f"paths. If files changed, mention they can ask what changed (a git diff); the "
        f"undo tool does not cover an agent's changes.",
        note=f"{LABEL[j.engine]}: {'done' if ok else 'stopped'} — {j.task[:50]}")
    if j.engine == "codex":
        try:
            refresh_usage("codex")
        except Exception:
            pass
    _changed()


def _run(j: Job, exe: str, player) -> tuple[bool, str]:
    proc = subprocess.Popen(
        build_argv(j, exe), cwd=j.cwd, env=_child_env(),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        **jobs.hidden_window_flags())
    j.proc = proc
    err_tail: deque[str] = deque(maxlen=20)
    threading.Thread(target=lambda: err_tail.extend(proc.stderr), daemon=True).start()
    try:
        proc.stdin.write(_prompt(j))
        proc.stdin.close()
    except Exception:
        pass
    timeout_s = float(current()["timeout_min"]) * 60
    timer = threading.Timer(timeout_s, _kill, args=(j, "timed out"))
    timer.daemon = True
    timer.start()
    parse = _parse_claude if j.engine == "claude" else _parse_codex
    st = {"result": None, "last_text": "", "last_log": 0.0, "error": ""}
    try:
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                step = parse(j, ev, st)
                if step:
                    j.steps += 1
                    j.last_step = step
                    print(f"[Agent] → {step}")
                    now = time.monotonic()
                    if now - st["last_log"] >= _STEP_LOG_GAP:
                        st["last_log"] = now
                        _log(player, f"SYS: {LABEL[j.engine]} → {step}")
                    _changed()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        timer.cancel()

    if j.killed:
        return False, (f"{LABEL[j.engine]} was stopped ({j.killed}) after {j.steps} steps. "
                       f"Anything it already changed stays changed.")
    if j.engine == "claude":
        res = st["result"]
        if res is None:
            tail = " ".join(s.strip() for s in err_tail)[-400:]
            return False, (f"Claude Code exited without a result (code {proc.returncode})."
                           + (f" {tail}" if tail else ""))
        report = str(res.get("result") or st["last_text"] or "").strip() \
            or "Claude Code finished but wrote no report."
        denied = res.get("permission_denials") or []
        if denied:
            report += (f"\n\n({len(denied)} step{'s' if len(denied) != 1 else ''} blocked "
                       f"by Claude Code's safety check.)")
        ok = not res.get("is_error") and res.get("subtype", "success") == "success"
        return ok, report
    if st["error"]:
        return False, f"Codex stopped with an error: {st['error'][:600]}"
    if st["result"] is None:
        tail = " ".join(s.strip() for s in err_tail)[-400:]
        return False, f"Codex exited without finishing (code {proc.returncode}). {tail}".strip()
    return True, st["last_text"] or "Codex finished but wrote no report."


_STEP_KEYS = ("command", "file_path", "path", "pattern", "query", "url", "description")


def _describe_tool(name: str, inp: dict) -> str:
    for key in _STEP_KEYS:
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            detail = " ".join(val.split())
            return f"{name}: {detail[:67] + '...' if len(detail) > 70 else detail}"
    return name


def _parse_claude(j: Job, ev: dict, st: dict) -> str:
    kind = ev.get("type")
    if kind == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
        j.session_id = str(ev["session_id"])
    elif kind == "assistant":
        step = ""
        for block in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                inp = block.get("input") or {}
                step = _describe_tool(block.get("name") or "tool", inp)
                if block.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if isinstance(fp, str):
                        j.files.add(fp)
            elif block.get("type") == "text" and str(block.get("text", "")).strip():
                st["last_text"] = block["text"].strip()
        return step
    elif kind == "rate_limit_event":
        _store_usage("claude", usage_from_claude_event(ev.get("rate_limit_info") or {}))
    elif kind == "result":
        st["result"] = ev
        if ev.get("session_id"):
            j.session_id = str(ev["session_id"])
    return ""


def _inner_command(cmd: str) -> str:
    """Codex wraps each command as `pwsh.exe -Command '…'`; show the inside."""
    for marker in (" -Command ", " -c "):
        if marker in cmd:
            return cmd.split(marker, 1)[1].strip().strip("'\"")
    return cmd


def _parse_codex(j: Job, ev: dict, st: dict) -> str:
    kind = ev.get("type")
    if kind == "thread.started" and ev.get("thread_id"):
        j.session_id = str(ev["thread_id"])
    elif kind == "turn.started":
        st["turn"] = True
    elif kind == "item.started":
        item = ev.get("item") or {}
        if item.get("type") == "command_execution":
            return _describe_tool("Run", {"command": _inner_command(str(item.get("command") or ""))})
    elif kind == "item.completed":
        item = ev.get("item") or {}
        t = item.get("type")
        if t == "agent_message" and str(item.get("text") or "").strip():
            st["last_text"] = str(item["text"]).strip()
        elif t == "file_change":
            changes = item.get("changes") or []
            paths = [c.get("path") for c in changes if isinstance(c, dict) and c.get("path")]
            j.files.update(paths)
            if paths:
                return f"Edit: {Path(paths[0]).name}" + (f" (+{len(paths) - 1})" if len(paths) > 1 else "")
        elif t == "error" and st.get("turn"):
            # Errors before the turn are config warnings (malformed agent
            # roles, plugin hooks); only ones during the work matter.
            st["error"] = str(item.get("message") or "")
    elif kind in ("turn.completed",):
        st["result"] = ev
    elif kind in ("turn.failed", "error"):
        err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
        st["error"] = str(err.get("message") or ev.get("message") or "unknown error")
    return ""


def _kill(j: Job, why: str) -> None:
    j.killed = why
    proc = j.proc
    if proc is None or proc.poll() is not None:
        return
    jobs.kill_tree(proc.pid)


def _log(player, message: str) -> None:
    print(f"[Agent] {message}")
    if player is not None:
        try:
            player.write_log(message)
        except Exception:
            pass


def reset() -> None:
    """Tests only."""
    global _pending, _job
    with _lock:
        _pending, _job = None, None
    _sessions.clear()
    with _usage_lock:
        _usage.clear()
