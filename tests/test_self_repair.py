"""Stages 4–5 of self-repair on a throwaway repository: the change check, the
whole loop with a stand-in agent, review, apply with undo, the memory of
rejected changes, and the restart watchdog's rollback. Needs git."""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import activity, agents, announce, restart_helper, self_repair as sr, undo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


# ── the change check ────────────────────────────────────────────────────────

# Built from parts so no key-shaped literal sits in the repository for secret
# scanners to flag; it is not a real key.
_FAKE_KEY = "AIza" + "SyA1234567890" + "abcdefghijklmnopqrstu"

def _ns(*entries):
    return "".join(e + "\0" for e in entries)


def test_protected_files_deletions_and_old_tests_are_refused():
    ns = _ns("M", "Core/Confirm.py", "D", "actions/old.py", "M", "tests/test_x.py",
             "A", "config/api_keys.json", "A", "../evil.py", "R100", "core/undo.py", "core/u2.py")
    problems, _ = sr.check_changes(ns, "", "", "")
    text = " | ".join(problems)
    assert "Core/Confirm.py: protected" in text                 # case does not matter
    assert "deleting files is not allowed" in text
    assert "existing tests may not be changed" in text
    assert "config/api_keys.json: protected" in text
    assert "outside the repository" in text
    assert "core/undo.py: may not be renamed" in text


def test_a_normal_change_with_a_new_test_passes_and_is_flagged():
    ns = _ns("M", "actions/weather_report.py", "A", "tests/test_weather_week.py", "M", "main.py")
    num = "3\t1\tactions/weather_report.py\x0020\t0\ttests/test_weather_week.py\x001\t1\tmain.py\x00"
    problems, files = sr.check_changes(ns, num, "", "+x = 1\n")
    assert problems == []
    flags = {f["path"]: f["flag"] for f in files}
    assert flags == {"actions/weather_report.py": "", "tests/test_weather_week.py": "new test",
                     "main.py": "core"}
    assert files[0]["added"] == 3 and files[0]["removed"] == 1


def test_binaries_symlinks_and_keys_are_refused():
    ns = _ns("A", "actions/logo.png", "A", "actions/link")
    num = "-\t-\tactions/logo.png\x001\t0\tactions/link\x00"
    problems, _ = sr.check_changes(ns, num, " create mode 120000 actions/link\n",
                                   "+KEY = '" + _FAKE_KEY + "'\n")
    text = " | ".join(problems)
    assert "binary" in text and "symbolic link" in text and "looks like a key" in text


# ── the loop ────────────────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "jarvis"
    (root / "actions").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "actions" / "calc.py").write_text("def add(a, b):\n    return a - b\n",
                                               encoding="utf-8")
    (root / "tests" / "test_calc.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
        "from actions.calc import add\n\n"
        "def test_runs():\n    assert callable(add)\n\n"
        "def test_add():\n    assert add(2, 2) == 4\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "first")
    monkeypatch.setattr(sr, "REPO", root)
    monkeypatch.setattr(sr, "DIR", tmp_path / "state")
    monkeypatch.setattr(agents, "find_exe", lambda engine: sys.executable)
    monkeypatch.setattr(agents, "signed_in", lambda engine, exe: True)
    sr.reset()
    announce.reset()
    activity.reset()
    undo.clear()
    yield root
    r = sr.current()
    if r:
        sr._cleanup(r)
    sr.reset()
    announce.reset()
    undo.clear()


def _agent(monkeypatch, edit):
    """A stand-in for Claude Code / Codex: edits the worktree, reports, done."""
    seen = {}

    def start_owned(task, cwd, system, on_done, player=None):
        seen.update(task=task, cwd=Path(cwd), system=system)
        edit(Path(cwd))
        on_done(agents.Job(engine="claude", task=task, cwd=str(cwd), done=True, ok=True,
                           report="Changed a - b to a + b; the tests pass."))
        return ""
    monkeypatch.setattr(agents, "start_owned", start_owned)
    return seen


def _approve():
    # Windows' monotonic clock ticks every ~16 ms: space the events out.
    time.sleep(0.05)
    activity.set_speaking(True)
    time.sleep(0.05)
    activity.set_speaking(False)
    time.sleep(0.05)
    activity.note_user_input()


def _wait(states=("review", "failed"), timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = sr.current()
        if r and r.state in states:
            return r
        time.sleep(0.1)
    raise AssertionError(f"still {sr.current() and sr.current().state}")


def test_fix_review_apply_and_undo(repo, monkeypatch):
    seen = _agent(monkeypatch, lambda wt: (wt / "actions" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"))
    out = sr.ask("improve", task="make add() add instead of subtract")
    assert out.startswith("[ASK_USER]")
    assert "not answered yet" in sr.run()                 # the user has not spoken
    _approve()
    assert sr.run().startswith("[REPAIR_STARTED]")
    r = _wait()
    assert r.state == "review", r.problem
    assert seen["cwd"] != repo and "AGENTS.md" in seen["system"]
    assert r.before["failed"] == ["tests.test_calc::test_add"] and r.after["failed"] == [], (r.before, r.after)
    assert [f["path"] for f in r.files] == ["actions/calc.py"]
    assert "+    return a + b" in sr.diff_text()
    assert "a - b" in (repo / "actions" / "calc.py").read_text()      # live code untouched
    assert "ready for the user's review" in announce.take()[0].text

    assert sr.apply() .startswith("Applied as commit")
    assert "a + b" in (repo / "actions" / "calc.py").read_text()
    assert _git(repo, "log", "-1", "--format=%s").startswith("Self-repair:")
    assert not Path(r.worktree).exists()
    assert "self-repair/" not in _git(repo, "branch")
    assert json.loads((sr.DIR / "applied.json").read_text())["base"] == r.base
    assert "Ask the user in one short sentence whether to restart" in announce.take()[0].text
    assert "back as it was" in undo.undo_last()
    assert _git(repo, "log", "-1", "--format=%s") == "first"


def test_a_protected_file_refuses_the_whole_change(repo, monkeypatch):
    def edit(wt):
        (wt / "core").mkdir()
        (wt / "core" / "confirm.py").write_text("ALWAYS = True\n", encoding="utf-8")
        (wt / "actions" / "calc.py").write_text("def add(a, b):\n    return a + b\n",
                                                encoding="utf-8")
    _agent(monkeypatch, edit)
    sr.ask("improve", task="fix add")
    _approve()
    sr.run()
    r = _wait()
    assert r.state == "failed" and "core/confirm.py: protected" in r.problem
    assert not Path(r.worktree).exists()
    assert "stopped without a change" in announce.take()[0].text
    assert (sr.DIR / r.id / "diagnostics.txt").exists()


def test_a_new_failure_refuses_the_change(repo, monkeypatch):
    _agent(monkeypatch, lambda wt: (wt / "actions" / "calc.py").write_text(
        "add = None\n", encoding="utf-8"))
    sr.ask("improve", task="break it")
    _approve()
    sr.run()
    r = _wait()
    assert r.state == "failed" and "fail now that passed before" in r.problem


def test_a_rejected_change_is_not_proposed_again(repo, monkeypatch):
    _agent(monkeypatch, lambda wt: (wt / "actions" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"))
    sr.ask("improve", task="make add add")
    _approve()
    sr.run()
    _wait()
    assert sr.discard().startswith("Discarded")
    assert "already rejected" in sr.ask("improve", task="Make add add!")


def test_apply_refuses_when_the_code_moved_on(repo, monkeypatch):
    _agent(monkeypatch, lambda wt: (wt / "actions" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"))
    sr.ask("improve", task="make add add")
    _approve()
    sr.run()
    _wait()
    (repo / "notes.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "-q", "-m", "meanwhile")
    assert "new commits" in sr.apply()
    assert sr.current().state == "review"


def test_fix_takes_the_latest_error_from_the_journal(repo, monkeypatch):
    from core import health
    health.log_error("crash", "ZeroDivisionError: division by zero", tool="calc")
    out = sr.ask("fix")
    assert out.startswith("[ASK_USER]") and "calc" in out
    assert "division by zero" in sr._pending["prompt"]


def test_a_review_survives_a_restart(repo, monkeypatch):
    _agent(monkeypatch, lambda wt: (wt / "actions" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"))
    sr.ask("improve", task="make add add")
    _approve()
    sr.run()
    rid = _wait().id
    with sr._lock:
        sr._current, sr._loaded = None, False          # a new process
    r = sr.current()
    assert r.id == rid and r.state == "review"


# ── the restart watchdog ────────────────────────────────────────────────────

def test_watchdog_rolls_back_a_change_that_breaks_startup(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "actions" / "calc.py").write_text("broken(\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "Self-repair: broke it")
    commit = _git(repo, "rev-parse", "HEAD")
    state = tmp_path / "wd"
    state.mkdir()
    (state / "applied.json").write_text(json.dumps({"base": base, "commit": commit}))
    out = restart_helper.watch([sys.executable, "-c", "import sys; sys.exit(3)"],
                               str(repo), state, watch_s=20)
    assert out == "rolled back"
    assert _git(repo, "rev-parse", "HEAD") == base
    note = json.loads((state / "rolled_back.json").read_text())
    assert note["exit"] == 3 and note["commit"] == commit


def test_watchdog_stops_once_the_new_instance_is_up(repo, tmp_path):
    state = tmp_path / "wd"
    state.mkdir()
    marker = (state / "started.ok").as_posix()
    code = (f"import pathlib, time; pathlib.Path({marker!r}).write_text('1'); "
            f"time.sleep(1)")
    assert restart_helper.watch([sys.executable, "-c", code], str(repo), state,
                                watch_s=20) == "started"


def test_startup_note_reports_a_rollback(repo):
    sr.DIR.mkdir(parents=True, exist_ok=True)
    (sr.DIR / "rolled_back.json").write_text(json.dumps({"commit": "abcdef123", "exit": 1}))
    assert "rolled back automatically" in sr.startup_note()
    assert sr.startup_note() == ""
