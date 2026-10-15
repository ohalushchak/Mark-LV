"""
core/self_repair.py — the assistant fixes or improves its own code; the user
decides whether the change goes in.

THE LOOP
    1. ask()      — the request is registered and the model asks the user out
                    loud (the same two-call gate as core/agents.py).
    2. run()      — only after the user answered. A separate git worktree of
                    this repository is made on a branch self-repair/<id>; the
                    running assistant and the user's checkout are not touched.
    3. The test suite runs there first, so "nothing new fails" has a baseline.
    4. The coding agent selected in the AGENT panel (Claude Code or Codex)
       works in the worktree with the rules in _SYSTEM.
    5. Its change is checked as data (check_changes): protected files, deleted
       files, binaries, symlinks, secrets — one bad path refuses the whole
       change. Then the suite runs again: any test failing now that passed
       before refuses it too.
    6. A change that passes waits for review on the HUD: the diff, the files,
       the tests before and after, the agent's report. APPLY and DISCARD are
       buttons — the model has no tool that applies a change.
    7. apply() — the branch is fast-forwarded into the user's checkout as one
       commit, with "undo" registered (git reset --keep to the old commit).
       The running process still has the old code loaded, so a restart is
       offered (core/restart_helper.py watches the new instance start and
       rolls the commit back if it cannot).

WHERE THE IDEAS CAME FROM
    Mixed from the forks, none of their code taken as is:
      * klcenka9/Mark-L — tests before and after on a branch, the review
        window, never re-proposing what the user rejected;
      * TeddyDARKVADOR/Jarvis_replacement_new — the whole change is refused on
        one disallowed path, tests can never be edited by the fixer, "target
        fixed, zero regressions", memory of failed attempts;
      * syamsulmuiin/Mark — validate in a separate copy before the live tree;
      * 10319joshuamotha/Robin-liv — at apply time the checkout must still be
        on the commit the fix was made from, and protected paths are compared
        case-insensitively.
    Their weak spots are avoided on purpose: no change is ever applied without
    the user (klcenka9 auto-applied "safe" code, dragonballls applied
    everything), the approval is a button and never a tool parameter or a
    phrase match (syamsulmuiin), and the user sees the diff before approving
    (Robin did not show it).

WHAT IT CANNOT DO
    It changes code only. A missing package, key or device is reported by
    self_check for the user to fix. It runs only from a git checkout: the
    packaged app has no repository to change and no tests to run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable

from core import activity, agents, announce, capabilities, health

REPO = Path(__file__).resolve().parent.parent
DIR = Path.home() / ".jarvis" / "self_repair"
ASK_TTL = 180.0
MAX_FILES = 25
MAX_DIFF_BYTES = 300_000
MAX_FAILED_TRIES = 2         # then the same task on the same code is refused

# Never changed by the fixer: the gates themselves (confirm, undo, the shell
# policy, argument checks), the loop that checks the fixer (this module, the
# restart watchdog, health and probes, the agent runner) and what tells agents
# how to behave. A change that could loosen a check must not be judged by
# that same check.
PROTECTED = {
    "core/confirm.py", "core/undo.py", "core/shell_policy.py", "core/action_loader.py",
    "core/plugin_loader.py", "core/tool_args.py", "core/health.py", "core/capabilities.py",
    "core/self_repair.py", "core/restart_helper.py", "core/agents.py", "core/activity.py",
    "core/prompt.txt", "actions/self_improve.py", "actions/self_check.py",
    "memory/config_manager.py", "agents.md", "claude.md", ".gitignore",
    "requirements.txt", "setup.py", "tests/conftest.py",
}
PROTECTED_DIRS = ("config/", ".github/", ".git/", ".claude/")
# Shown with a warning in the review: the code everything else runs on.
CORE_PREFIXES = ("core/", "main.py", "ui.py", "memory/")

_SYSTEM = (
    "You were started by the user's desktop voice assistant, JARVIS, to change "
    "JARVIS's own source code. You are in a separate git worktree of its repository: "
    "the running assistant is not affected until the user reviews your change on "
    "screen and approves it. Read AGENTS.md first and follow its conventions. "
    "Rules: keep the change as small as the task allows. Never modify these files: "
    + ", ".join(sorted(p for p in PROTECTED if "/" in p or p.endswith(".py")))
    + ", AGENTS.md, CLAUDE.md, or anything under config/ or .github/. You may add new "
    "test files named tests/test_*.py; never edit or delete existing tests. Do not "
    "delete files. Do not commit, push, create branches or change git settings, and "
    "do not install packages. Before you finish, run `python -m pytest tests -q` "
    "and make sure nothing fails that passed before. Nobody can answer questions "
    "while you work. Your final message is shown to the user: plain language, no "
    "markdown — what was wrong, what you changed, how you checked it. Two to five "
    "sentences."
)

RUNNING = ("preparing", "agent", "testing")

# Seen live: the model asked "shall we start?" and called run in the same turn,
# got refused, then told the user it had started. So the refusal says so.
PREMATURE = (
    "[NOT_STARTED] Nothing is running: you called run before the user answered your "
    "question. Do NOT say it has started. End your turn now and wait for their reply; "
    "if they agree, call {tool} with action='run' again.")


class RepairError(Exception):
    """Why a run stopped. `environment` marks a failure of the machine, not of
    the task (the agent's sandbox could not start): it does not count towards
    MAX_FAILED_TRIES."""

    def __init__(self, message: str, environment: bool = False):
        super().__init__(message)
        self.environment = environment


@dataclass
class Repair:
    id: str
    kind: str                     # fix | improve
    task: str                     # what the user asked, in a sentence
    prompt: str                   # what the agent is given
    fingerprint: str
    error_id: str = ""
    base: str = ""
    branch: str = ""
    worktree: str = ""
    state: str = "preparing"      # preparing agent testing review applied failed discarded
    step: str = ""
    engine: str = ""
    before: dict | None = None
    after: dict | None = None
    files: list = field(default_factory=list)
    added: int = 0
    removed: int = 0
    diff_hash: str = ""
    problem: str = ""
    report: str = ""
    commit: str = ""
    dirty: list = field(default_factory=list)
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    restart_offered: float = 0.0


_lock = threading.Lock()
_pending: dict | None = None
_current: Repair | None = None
_loaded = False
_cancel = threading.Event()
_listeners: list[Callable[[], None]] = []
_ui: dict = {"review": None, "restart": None}


# ── wiring ──────────────────────────────────────────────────────────────────

def bind(show_review: Callable[[], None] | None = None,
         restart: Callable[[], None] | None = None) -> None:
    """main.py: how to put the review on the HUD, and how to restart."""
    _ui["review"] = show_review
    _ui["restart"] = restart


def on_change(fn: Callable[[], None]) -> None:
    _listeners.append(fn)


def _changed() -> None:
    _save()
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


def available() -> str:
    """'' when self-repair can run here, else why not."""
    if getattr(sys, "frozen", False):
        return "Self-repair works only when JARVIS runs from its source code, not the packaged app."
    if not (REPO / ".git").exists():
        return "JARVIS is not running from a git checkout, so there is nothing to change safely."
    if not shutil.which("git"):
        return "git is not installed, and self-repair needs it."
    return ""


# ── git ─────────────────────────────────────────────────────────────────────

def _git(*args: str, cwd: Path | str | None = None, timeout: float = 120,
         check: bool = True) -> str:
    # REPO is read at call time, not bound as a default: tests point it at a
    # throwaway repository, and a default would quietly keep the real one.
    r = subprocess.run(["git", *args], cwd=str(cwd or REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       **_no_window())
    if check and r.returncode != 0:
        raise RepairError((r.stderr or r.stdout or f"git {args[0]} failed").strip()[:600])
    return r.stdout


def _no_window() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# ── checking a change ───────────────────────────────────────────────────────

_SECRET_RES = [re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
               re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
               re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")]


def _norm(path: str) -> str | None:
    """Repository-relative posix path, lower-cased for comparison; None if it
    points outside the repository."""
    p = str(path or "").replace("\\", "/").strip()
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return None
    parts = PurePosixPath(p).parts
    if any(part in ("..", "") for part in parts):
        return None
    return "/".join(parts).lower()


def _refusal(path: str, status: str) -> str:
    n = _norm(path)
    if n is None:
        return f"{path}: outside the repository"
    if n in PROTECTED or any(n.startswith(d) for d in PROTECTED_DIRS):
        return f"{path}: protected — the fixer may not change it"
    if n.startswith("tests/"):
        name = n.rsplit("/", 1)[-1]
        if status != "A" or not re.fullmatch(r"test_[a-z0-9_]+\.py", name):
            return f"{path}: existing tests may not be changed, only new tests/test_*.py added"
    try:
        from core.projects import is_secret
        if is_secret(Path(path)):
            return f"{path}: looks like a key or password file"
    except Exception:
        pass
    return ""


def parse_name_status(raw: str) -> list[tuple[str, list[str]]]:
    """`git diff --name-status -z -M` → [(status letter, [paths])]."""
    toks = raw.split("\0")
    out, i = [], 0
    while i < len(toks) and toks[i]:
        st = toks[i]
        n = 2 if st[:1] in ("R", "C") else 1
        out.append((st[:1], toks[i + 1:i + 1 + n]))
        i += 1 + n
    return out


def parse_numstat(raw: str) -> dict[str, tuple[int | None, int | None]]:
    """`git diff --numstat -z -M` → {new path: (added, removed)}; None = binary."""
    toks = raw.split("\0")
    out, i = {}, 0
    while i < len(toks) and toks[i]:
        a, r, path = (toks[i].split("\t", 2) + ["", ""])[:3]
        if path == "":                       # a rename: old and new follow
            path = toks[i + 2] if i + 2 < len(toks) else ""
            i += 3
        else:
            i += 1
        out[path] = (None if a == "-" else int(a or 0), None if r == "-" else int(r or 0))
    return out


def check_changes(name_status: str, numstat: str, summary: str, diff: str) -> tuple[list, list]:
    """Judge a staged change as data. Returns (problems, files) where each file
    is {"path", "status", "added", "removed", "flag"}. Any problem refuses the
    whole change."""
    problems: list[str] = []
    files: list[dict] = []
    counts = parse_numstat(numstat)
    for st, paths in parse_name_status(name_status):
        new = paths[-1] if paths else ""
        flag = ""
        if st == "D":
            problems.append(f"{new}: deleting files is not allowed")
        elif st in ("T", "U", "X"):
            problems.append(f"{new}: unexpected change type {st}")
        if st == "R":
            # The old name disappears, so it must be a file the fixer may change.
            if _refusal(paths[0], "M"):
                problems.append(f"{paths[0]}: may not be renamed")
            flag = f"renamed from {paths[0]}"
        why = _refusal(new, "A" if st in ("R", "C") else st)
        if why:
            problems.append(why)
        n = _norm(new) or ""
        if n.startswith("tests/"):
            flag = "new test"
        elif n.startswith(CORE_PREFIXES):
            flag = flag or "core"
        added, removed = counts.get(new, (0, 0))
        if added is None:
            problems.append(f"{new}: binary files are not accepted")
        files.append({"path": new, "status": st, "added": added or 0,
                      "removed": removed or 0, "flag": flag})
    if re.search(r"\b120000\b", summary):
        problems.append("a symbolic link was added")
    if "mode change" in summary:
        problems.append("a file mode was changed")
    if len(files) > MAX_FILES:
        problems.append(f"{len(files)} files changed; more than {MAX_FILES} is too large to review")
    if len(diff.encode("utf-8", "replace")) > MAX_DIFF_BYTES:
        problems.append("the change is too large to review")
    added_lines = "\n".join(l for l in diff.splitlines() if l.startswith("+"))
    if any(rx.search(added_lines) for rx in _SECRET_RES):
        problems.append("the change contains something that looks like a key")
    return list(dict.fromkeys(problems)), files


def _staged(wt: Path, base: str) -> tuple[str, str, str, str]:
    """name-status, numstat, summary and diff of the worktree's index against
    the base commit — the same before and after the change is committed."""
    _git("add", "-A", cwd=wt)
    ns = _git("diff", "--cached", "--name-status", "-z", "-M", base, cwd=wt)
    num = _git("diff", "--cached", "--numstat", "-z", "-M", base, cwd=wt)
    summ = _git("diff", "--cached", "--summary", "-M", base, cwd=wt)
    diff = _git("diff", "--cached", "-M", base, cwd=wt)
    return ns, num, summ, diff


def _hash(diff: str) -> str:
    return hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest()


# ── history: what was tried, rejected, applied ─────────────────────────────

def _history() -> list[dict]:
    try:
        lines = (DIR / "history.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for raw in lines:
        try:
            out.append(json.loads(raw))
        except ValueError:
            pass
    return out


def _remember(r: Repair, outcome: str) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        entry = {"id": r.id, "at": datetime.now().isoformat(timespec="seconds"),
                 "outcome": outcome, "fingerprint": r.fingerprint, "kind": r.kind,
                 "task": r.task[:200], "base": r.base[:12], "commit": r.commit[:12],
                 "files": [f["path"] for f in r.files], "problem": r.problem[:400]}
        with (DIR / "history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:                                   # pragma: no cover
        print(f"[SelfRepair] could not write history: {e}")


def _fingerprint(kind: str, key: str, base: str) -> str:
    gist = re.sub(r"\W+", " ", key.lower()).strip()[:200]
    return hashlib.sha1(f"{kind}|{gist}|{base}".encode()).hexdigest()[:12]


def _refused_before(fp: str) -> str:
    past = [h for h in _history() if h.get("fingerprint") == fp]
    if any(h.get("outcome") == "rejected" for h in past):
        return ("The user already rejected a change for exactly this on the current code. "
                "Tell them; it will not be proposed again unless they describe it differently.")
    failed = [h for h in past if h.get("outcome") == "failed"]
    if len(failed) >= MAX_FAILED_TRIES:
        return (f"This was already tried {len(failed)} times on the current code and failed "
                f"(last: {failed[-1].get('problem', '')[:200]}). Tell the user it needs a human "
                f"look or a different description.")
    return ""


# ── persistence of the current repair ───────────────────────────────────────

def _save() -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        with _lock:
            r = _current
        path = DIR / "current.json"
        if r is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(asdict(r), ensure_ascii=False, indent=1),
                            encoding="utf-8")
    except Exception as e:                                   # pragma: no cover
        print(f"[SelfRepair] could not save state: {e}")


def _load() -> None:
    """Once per process: a review survives a restart; a run that was cut off
    by one is marked failed and its worktree removed."""
    global _current, _loaded
    with _lock:
        if _loaded:
            return
        _loaded = True
    try:
        data = json.loads((DIR / "current.json").read_text(encoding="utf-8"))
        known = set(Repair.__dataclass_fields__)
        r = Repair(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError, TypeError):
        return
    if r.state in RUNNING:
        r.state, r.problem = "failed", "interrupted when JARVIS was closed"
        _cleanup(r)
    elif r.state == "review" and not Path(r.worktree).is_dir():
        r.state, r.problem = "failed", "its working copy is gone"
    r.restart_offered = 0.0
    with _lock:
        _current = r


def current() -> Repair | None:
    _load()
    with _lock:
        return _current


def diff_text(r: Repair | None = None) -> str:
    r = r or current()
    if r is None:
        return ""
    try:
        return (DIR / r.id / "patch.diff").read_text(encoding="utf-8")
    except OSError:
        return ""


# ── 1. ask ──────────────────────────────────────────────────────────────────

def _busy() -> str:
    r = current()
    if r and r.state in RUNNING:
        return (f"A self-repair is already in progress ({r.task[:80]}, {r.step or r.state}). "
                f"Tell the user; they can wait or cancel it.")
    if r and r.state == "review":
        return ("A previous change is still waiting for the user's review on the HUD. "
                "They should apply or discard it first (self_improve action='review' shows it).")
    j = agents.job()
    if j and not j.done:
        return (f"{agents.LABEL[j.engine]} is busy with another task ({j.task[:80]}). "
                f"Self-repair needs it; try again when it is done.")
    return ""


def _error_prompt(e: dict) -> str:
    lines = [f"Fix this error in JARVIS (error id {e['id']}, seen {e.get('count', 1)} time(s), "
             f"last at {e.get('at', '?')}).",
             f"Kind: {e.get('kind')}" + (f"; tool: {e['tool']}" if e.get("tool") else ""),
             f"Message: {e.get('message', '')}"]
    if e.get("where"):
        lines.append(f"Where: {e['where']}")
    if e.get("args"):
        lines.append(f"Tool arguments: {e['args']}")
    if e.get("trace"):
        lines.append(f"Traceback:\n{e['trace'][-3000:]}")
    lines.append("Find the root cause and fix it. If a regression test fits, add one as a "
                 "new tests/test_*.py file.")
    return "\n".join(lines)


def _pick_error(error_id: str) -> dict | None:
    if error_id:
        return health.get(error_id)
    for e in health.recent(limit=50, current_version_only=True):
        if e.get("kind") in ("crash", "thread", "asyncio", "reported"):
            return e
    return None


def ask(kind: str, task: str = "", error_id: str = "", player=None) -> str:
    global _pending
    why = available() or _busy()
    if why:
        return why
    kind = "fix" if kind == "fix" else "improve"
    task = str(task or "").strip()
    err = None
    if kind == "fix":
        err = _pick_error(str(error_id or "").strip())
        if err is None:
            if error_id:
                return f"No error with id {error_id!r} is in the journal. Check self_check action='errors'."
            if not task:
                return ("No code error is recorded on the current version. Ask the user what "
                        "is wrong, then call again with kind 'improve' and the description.")
            kind = "improve"
    if kind == "improve" and not task:
        return "Say what to change: call again with 'task' describing it in full, in English."
    engine = agents.current()["engine"]
    exe = agents.find_exe(engine)
    if not exe:
        return f"{agents.LABEL[engine]} is not installed, so self-repair cannot run."
    if agents.signed_in(engine, exe) is False:
        return f"{agents.LABEL[engine]} is not signed in, so self-repair cannot run."
    base = _git("rev-parse", "HEAD").strip()
    if err is not None:
        summary = task or f"{err.get('tool') or err['kind']}: {err['message'][:120]}"
        prompt = _error_prompt(err) + (f"\n\nThe user added: {task}" if task else "")
        fp = _fingerprint("fix", err["id"], base)
    else:
        summary, prompt = task, f"Improve JARVIS as follows:\n{task}"
        fp = _fingerprint("improve", task, base)
    refused = _refused_before(fp)
    if refused:
        return refused
    dirty = [l[3:] for l in _git("status", "--porcelain", "--untracked-files=no").splitlines() if l]
    with _lock:
        _pending = {"kind": kind, "task": summary, "prompt": prompt, "fingerprint": fp,
                    "error_id": err["id"] if err else "", "asked_at": time.monotonic(),
                    "dirty": dirty}
    _changed()
    note = (f" Note for the user: they have uncommitted edits ({', '.join(dirty[:4])}); the "
            f"change is made against the last commit.") if dirty else ""
    return (f"[ASK_USER] Nothing has started. Ask the user now, in ONE short sentence in "
            f"their language, whether to let {agents.LABEL[engine]} {'fix' if kind == 'fix' else 'make'}"
            f" this in a separate copy of your code — name the task in a few words so they "
            f"can catch a misunderstanding: {summary[:140]}. Mention that nothing "
            f"changes until they approve the result on screen.{note} Then END YOUR TURN — "
            f"do not call action='run' in this turn; the user's spoken answer must come "
            f"first (they can also press START on the HUD). If they agree, call "
            f"self_improve with action='run'; if not, drop it.")


# ── 2.–5. run ───────────────────────────────────────────────────────────────

def run(details: str = "", player=None, by_button: bool = False) -> str:
    """Start the asked repair. From the model only after the user spoke since
    the question; from the HUD's START button the click is the answer."""
    global _pending, _current
    with _lock:
        p = _pending
    if p is None or time.monotonic() - p["asked_at"] > ASK_TTL:
        with _lock:
            _pending = None
        return ("Nothing is waiting for the user's approval. Call self_improve with action "
                "'fix' or 'improve' first, then wait for their answer.")
    if not by_button and not activity.user_answered_since(p["asked_at"]):
        return PREMATURE.format(tool="self_improve")
    busy = _busy()
    if busy:
        return busy
    rid = datetime.now().strftime("%Y%m%d-%H%M%S")
    prompt = p["prompt"] + (f"\n\nWhen approving, the user added: {details}" if details else "")
    r = Repair(id=rid, kind=p["kind"], task=p["task"], prompt=prompt,
               fingerprint=p["fingerprint"], error_id=p["error_id"], dirty=p["dirty"],
               engine=agents.current()["engine"], step="making a separate copy")
    with _lock:
        _pending = None
        _current = r
    _cancel.clear()
    threading.Thread(target=_pipeline, args=(r, player), name="self-repair",
                     daemon=True).start()
    _changed()
    return ("[REPAIR_STARTED] It is underway in a separate copy: tests, then the agent, then "
            "tests again. Tell the user in a few words; the result is announced when it is "
            "ready for their review.")


def _step(r: Repair, state: str, step: str, player=None) -> None:
    r.state, r.step = state, step
    if player is not None:
        try:
            player.write_log(f"SYS: Self-repair — {step}")
        except Exception:
            pass
    print(f"[SelfRepair] {state}: {step}")
    _changed()


def _pipeline(r: Repair, player) -> None:
    try:
        _prepare(r, player)
        _check_cancel()
        _step(r, "preparing", "running the tests before the change", player)
        r.before = _tests(r.worktree)
        if not r.before.get("total"):
            raise RepairError(f"the tests could not run before the change: "
                              f"{r.before.get('error', '')[:300]}")
        _check_cancel()
        _step(r, "agent", f"{agents.LABEL[r.engine]} is working", player)
        _run_agent(r, player)
        _check_cancel()
        _step(r, "testing", "checking the change and running the tests", player)
        _judge(r)
        _remember(r, "ready")
        _announce_ready(r)
        _step(r, "review", "ready for review", player)
    except RepairError as e:
        _fail(r, str(e), player, environment=e.environment)
    except Exception as e:
        health.log_error("crash", f"self-repair: {type(e).__name__}: {e}", tool="self_improve",
                         exc=e)
        _fail(r, f"{type(e).__name__}: {e}", player)


def _check_cancel() -> None:
    if _cancel.is_set():
        raise RepairError("cancelled by the user")


def _prepare(r: Repair, player) -> None:
    _git("worktree", "prune", check=False)
    r.base = _git("rev-parse", "HEAD").strip()
    r.branch = f"self-repair/{r.id}"
    wt = DIR / "wt" / r.id
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", r.branch, str(wt), r.base, timeout=300)
    r.worktree = str(wt)
    _changed()


def _tests(wt: str) -> dict:
    return capabilities.run_tests(cwd=Path(wt))


def _run_agent(r: Repair, player) -> None:
    done = threading.Event()
    box: dict = {}

    def on_done(j):
        box["job"] = j
        done.set()

    why = agents.start_owned(f"Self-repair: {r.task[:120]}", Path(r.worktree), _SYSTEM,
                             on_done, player=player)
    if why:
        raise RepairError(why)
    done.wait()
    j = box["job"]
    r.report = (j.report or "")[:4000]
    if j.killed:
        raise RepairError(f"the agent was stopped ({j.killed})")
    if r.report.startswith(agents.SANDBOX_HINT[:40]):
        raise RepairError(agents.SANDBOX_HINT, environment=True)
    if not j.ok:
        raise RepairError(f"the agent did not finish: {r.report[:300]}")


def _judge(r: Repair) -> None:
    wt = Path(r.worktree)
    ns, num, summ, diff = _staged(wt, r.base)
    if not ns.strip("\0").strip():
        raise RepairError("the agent changed nothing" + (f". Its report: {r.report[:300]}"
                                                          if r.report else ""))
    problems, files = check_changes(ns, num, summ, diff)
    r.files = files
    r.added = sum(f["added"] for f in files)
    r.removed = sum(f["removed"] for f in files)
    if problems:
        raise RepairError("the change was refused: " + "; ".join(problems[:6]))
    r.after = _tests(r.worktree)
    if not r.after.get("total"):
        raise RepairError(f"the tests could not run after the change: "
                          f"{r.after.get('error', '')[:300]}")
    new = sorted(set(r.after["failed"]) - set(r.before["failed"]))
    if new:
        raise RepairError(f"{len(new)} test(s) fail now that passed before: {', '.join(new[:5])}")
    if r.after["total"] < r.before["total"]:
        raise RepairError("fewer tests ran after the change than before")
    r.diff_hash = _hash(diff)
    (DIR / r.id).mkdir(parents=True, exist_ok=True)
    (DIR / r.id / "patch.diff").write_text(diff, encoding="utf-8")


def _fail(r: Repair, problem: str, player, environment: bool = False) -> None:
    r.problem = problem
    try:
        (DIR / r.id).mkdir(parents=True, exist_ok=True)
        (DIR / r.id / "diagnostics.txt").write_text(
            f"Task: {r.task}\nProblem: {problem}\n\nAgent report:\n{r.report}\n\n"
            f"Tests before: {json.dumps(r.before)}\nTests after: {json.dumps(r.after)}\n",
            encoding="utf-8")
    except OSError:
        pass
    _remember(r, "cancelled" if "cancelled" in problem
              else "blocked" if environment else "failed")
    _cleanup(r)
    # Seen live: after a failed run the model reached for git_project and
    # delegate_to_agent, which work on the current project (a different one),
    # and the user was left thinking self-repair had used the wrong project.
    announce.say(
        f"[NOTIFICATION] The self-repair '{r.task[:160]}' stopped without a change to "
        f"review: {problem[:500]}. Nothing in the running code was touched. Tell the user "
        f"in one or two short sentences, with the reason. This was JARVIS's own code in a "
        f"separate copy: do not use git_project, project_workspace or delegate_to_agent "
        f"for it — they work on the user's current project. To retry, use self_improve "
        f"again (after switching the agent in the AGENT panel if the agent was the problem).",
        note=f"Self-repair stopped — {r.task[:50]}", key="self_repair")
    # Last, so whoever watches the state sees "failed" only once it is all done.
    _step(r, "failed", f"stopped: {problem[:120]}", player)


def _announce_ready(r: Repair) -> None:
    t = r.after or {}
    fixed = sorted(set((r.before or {}).get("failed", [])) - set(t.get("failed", [])))
    announce.say(
        f"[NOTIFICATION] The self-repair '{r.task[:160]}' is ready for the user's review: "
        f"{len(r.files)} file(s) changed (+{r.added} −{r.removed}); tests: {t.get('passed', 0)} "
        f"passed, none newly failing" + (f", {len(fixed)} fixed" if fixed else "") + ". "
        f"Nothing is applied yet. Tell the user in one short sentence and offer to show it "
        f"(self_improve action='review' puts it on screen; they decide there).",
        note=f"Self-repair ready for review — {r.task[:50]}", key="self_repair")


def _cleanup(r: Repair) -> None:
    """Remove the worktree and its branch. Never raises."""
    try:
        if r.worktree:
            _git("worktree", "remove", "--force", r.worktree, check=False, timeout=60)
            shutil.rmtree(r.worktree, ignore_errors=True)
        _git("worktree", "prune", check=False)
        if r.branch:
            _git("branch", "-D", r.branch, check=False)
    except Exception:
        pass


# ── review, apply, discard ──────────────────────────────────────────────────

def review() -> str:
    r = current()
    if r is None or r.state != "review":
        return "No change is waiting for review."
    show = _ui.get("review")
    if show is None:
        return describe(r)
    show()
    return ("[REVIEW_SHOWN] The change is on the HUD with its diff and test results. The user "
            "decides there with APPLY or DISCARD; you cannot apply it. Say one short sentence.")


def apply() -> str:
    """Only ever called from the HUD's APPLY button."""
    r = current()
    if r is None or r.state != "review":
        return "No change is waiting for review."
    try:
        wt = Path(r.worktree)
        _, _, _, diff = _staged(wt, r.base)
        if _hash(diff) != r.diff_hash:
            raise RepairError("the change is no longer what was reviewed; discard it and run again")
        head = _git("rev-parse", "HEAD").strip()
        if head != r.base:
            raise RepairError("JARVIS's code has new commits since the change was made; "
                              "discard it and run it again")
        if _git("rev-parse", "HEAD", cwd=wt).strip() == r.base:
            msg = (f"Self-repair: {r.task[:60]}\n\n"
                   f"Made by {agents.LABEL.get(r.engine, r.engine)} through JARVIS self_improve "
                   f"and approved on the HUD. Tests: {r.after.get('passed', 0)} passed, none newly "
                   f"failing.")
            _git("commit", "-q", "-m", msg, cwd=wt)
        commit = _git("rev-parse", "HEAD", cwd=wt).strip()
        _git("merge", "--ff-only", "-q", r.branch)
    except RepairError as e:
        r.problem = str(e)
        _changed()
        return f"Not applied: {e}"
    r.commit = commit
    r.problem = ""
    from core.undo import push_undo
    base = r.base
    push_undo(f"self-repair: {r.task[:60]}", lambda: _undo_apply(base, commit))
    _cleanup(r)
    r.state, r.step = "applied", "applied — restart to load it"
    r.restart_offered = time.monotonic()
    _write_applied(base, commit)
    _remember(r, "applied")
    _changed()
    announce.say(
        f"[NOTIFICATION] The user applied the self-repair '{r.task[:160]}' (commit "
        f"{commit[:8]}). The running assistant still has the old code loaded. Ask the user "
        f"in one short sentence whether to restart now; if they agree, call self_improve "
        f"with action='restart'.",
        note="Self-repair applied — restart to load it", key="self_repair")
    return f"Applied as commit {commit[:8]}."


def _undo_apply(base: str, commit: str) -> str:
    head = _git("rev-parse", "HEAD").strip()
    if head != commit:
        return "Not undone: there are newer commits on top of it."
    _git("reset", "--keep", base)
    _clear_applied()
    return "The code is back as it was; restart to load it."


def discard(reason: str = "rejected by the user") -> str:
    r = current()
    if r is None or r.state != "review":
        return "No change is waiting for review."
    r.problem = reason
    _cleanup(r)
    r.state, r.step = "discarded", "discarded"
    _remember(r, "rejected")
    _changed()
    return "Discarded. The same change will not be proposed again on this code."


def cancel() -> str:
    global _pending
    with _lock:
        had = _pending is not None
        _pending = None
    r = current()
    if r and r.state in RUNNING:
        _cancel.set()
        j = agents.job()
        if j and not j.done and r.state == "agent":
            agents.cancel()
        return "Stopping the self-repair. Nothing in the running code was changed."
    return "Dropped; nothing was started." if had else "No self-repair is in progress."


# ── 5. restart ──────────────────────────────────────────────────────────────

def _write_applied(base: str, commit: str) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "applied.json").write_text(json.dumps({"base": base, "commit": commit}),
                                      encoding="utf-8")


def _clear_applied() -> None:
    (DIR / "applied.json").unlink(missing_ok=True)


def restart(player=None, by_button: bool = False) -> str:
    r = current()
    fn = _ui.get("restart")
    if fn is None:
        return "Restarting is not available right now."
    if not by_button:
        if r is None or r.state != "applied" or not r.restart_offered:
            return "There is no applied change waiting for a restart."
        if not activity.user_answered_since(r.restart_offered):
            return "Ask the user first, and restart only after they agree."
    if r is not None and r.state == "applied":
        r.step = "restarting"
        _changed()
    fn()
    return ("[RESTARTING] Say one very short sentence that you are restarting; you will be "
            "back in a few seconds.")


def spawn_restart() -> None:
    """Start core/restart_helper.py, which waits for this process to exit,
    starts JARVIS again with the same command and watches it come up. The
    caller exits right after."""
    helper = REPO / "core" / "restart_helper.py"
    cmd = [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    argv = [sys.executable, str(helper), "--pid", str(os.getpid()), "--repo", str(REPO),
            "--state", str(DIR), "--", *cmd]
    DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # Out of the console's job, if allowed, so closing this process does
        # not take the helper with it; same console, so the log stays visible.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(argv, cwd=str(REPO),
                             creationflags=flags | 0x01000000)   # CREATE_BREAKAWAY_FROM_JOB
            return
        except OSError:
            subprocess.Popen(argv, cwd=str(REPO), creationflags=flags)
            return
    subprocess.Popen(argv, cwd=str(REPO), start_new_session=True)


def startup_note() -> str:
    """On launch: what the restart watchdog left behind, as an announcement
    text ('' when nothing happened)."""
    path = DIR / "rolled_back.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    path.unlink(missing_ok=True)
    return (f"[NOTIFICATION] After the last self-repair JARVIS could not start (exit "
            f"{data.get('exit')}), so the change (commit {str(data.get('commit'))[:8]}) was "
            f"rolled back automatically and this is the previous code. Tell the user in one "
            f"short sentence.")


def mark_started() -> None:
    """The new instance is up (its Live session connected): the watchdog may stop."""
    global _current
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        (DIR / "started.ok").write_text(str(os.getpid()), encoding="utf-8")
        # This code has proven it starts: a later failure to start must not
        # roll it back.
        _clear_applied()
    except OSError:
        pass
    r = current()
    if r is not None and r.state == "applied":
        # The applied change is now the running code: nothing is left to show.
        with _lock:
            _current = None
        _changed()


# ── describing ──────────────────────────────────────────────────────────────

def describe(r: Repair | None = None) -> str:
    r = r or current()
    if r is None:
        return "No self-repair has been run."
    lines = [f"Self-repair {r.id} ({r.kind}): {r.task}", f"State: {r.state} — {r.step}"]
    if r.files:
        lines.append("Files: " + ", ".join(
            f"{f['path']} (+{f['added']} −{f['removed']}{', ' + f['flag'] if f['flag'] else ''})"
            for f in r.files))
    if r.before:
        lines.append(f"Tests before: {r.before.get('passed', 0)} passed, "
                     f"{len(r.before.get('failed') or [])} failed.")
    if r.after:
        lines.append(f"Tests after: {r.after.get('passed', 0)} passed, "
                     f"{len(r.after.get('failed') or [])} failed.")
    if r.problem:
        lines.append(f"Problem: {r.problem}")
    if r.report:
        lines.append(f"Agent's report: {r.report[:800]}")
    return "\n".join(lines)


def pending() -> dict | None:
    """The repair asked about and not yet started, while it is still valid."""
    with _lock:
        p = _pending
    if p is None or time.monotonic() - p["asked_at"] > ASK_TTL:
        return None
    return p


def drop_pending() -> str:
    global _pending
    with _lock:
        had, _pending = _pending is not None, None
    _changed()
    return "Dropped; nothing was started." if had else "Nothing was waiting."


def state() -> dict:
    """A snapshot for the HUD panel."""
    r = current()
    p = pending()
    if p is not None and (r is None or r.state not in RUNNING + ("review",)):
        return {"state": "asked", "task": p["task"],
                "left": max(0, int(ASK_TTL - (time.monotonic() - p["asked_at"])))}
    if r is None:
        return {"state": "idle"}
    return {"state": r.state, "step": r.step, "task": r.task, "files": len(r.files),
            "added": r.added, "removed": r.removed, "problem": r.problem}


def reset() -> None:
    """Tests only."""
    global _pending, _current, _loaded
    with _lock:
        _pending, _current, _loaded = None, None, True
    _cancel.clear()
    _ui.update(review=None, restart=None)
