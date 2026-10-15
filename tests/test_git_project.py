"""git_project on throwaway repositories (needs git on PATH)."""
import shutil
import subprocess
import time

import pytest

from actions import git_project as gp
from core import announce, confirm, projects, undo
from memory import config_manager as cm

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "config" / "api_keys.json")
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "first")
    monkeypatch.setattr(projects, "current", lambda: root)
    announce.reset()
    undo.clear()
    yield root
    undo.clear()
    announce.reset()


def test_status_and_diff(repo):
    assert "clean" in gp.git_project({"action": "status"})
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "new.txt").write_text("x", encoding="utf-8")
    st = gp.git_project({"action": "status"})
    assert "a.txt" in st and "new.txt" in st
    diff = gp.git_project({"action": "diff"})
    assert "+two" in diff and "1 file changed" in diff
    assert "No staged changes" in gp.git_project({"action": "diff", "staged": True})


def test_commit_named_files_only_and_undo(repo):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "b.txt").write_text("other\n", encoding="utf-8")
    out = gp.git_project({"action": "commit", "paths": ["b.txt"], "message": "add b"})
    assert out.startswith("Committed")
    assert _git(repo, "show", "--name-only", "--format=", "HEAD") == "b.txt"
    assert "a.txt" in _git(repo, "status", "--short")          # left alone
    assert "undone" in undo.undo_last()
    assert _git(repo, "log", "-1", "--format=%s") == "first"


def test_commit_all_takes_tracked_changes_and_names_the_rest(repo):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("u", encoding="utf-8")
    out = gp.git_project({"action": "commit", "all": True, "message": "update a"})
    assert "Committed" in out and "untracked.txt" in out
    assert "untracked.txt" in _git(repo, "status", "--short")


def test_key_files_are_never_committed(repo):
    (repo / ".env").write_text("SECRET=1", encoding="utf-8")
    out = gp.git_project({"action": "commit", "paths": [".env"], "message": "oops"})
    assert "will not commit" in out
    _git(repo, "add", "-f", ".env")
    out = gp.git_project({"action": "commit", "all": True, "message": "oops"})
    assert "did not commit" in out and _git(repo, "log", "-1", "--format=%s") == "first"


def test_branch_switch_and_undo(repo):
    assert "Created and switched" in gp.git_project({"action": "branch", "branch": "feat/x"})
    assert _git(repo, "branch", "--show-current") == "feat/x"
    assert "not a valid branch" in gp.git_project({"action": "branch", "branch": "-bad"})
    assert "Switched back" in undo.undo_last()
    assert _git(repo, "branch", "--show-current") == "main"


def test_push_waits_for_the_button(repo, tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    shown = {}
    confirm.bind(lambda t, d: shown.update(title=t, detail=d), lambda: None)
    try:
        out = gp.git_project({"action": "push"})
        assert out.startswith("[CONFIRMATION_PENDING]") and "first" in shown["detail"]
        assert _git(remote, "branch") == ""                    # nothing yet
        confirm.resolve(True)
        for _ in range(100):
            if announce.pending():
                break
            time.sleep(0.1)
        assert "succeeded" in announce.take()[0].text
        assert "main" in _git(remote, "branch")
        assert "nothing to push" in gp.git_project({"action": "push"})
    finally:
        confirm.resolve(False)
        confirm.bind(None, None)


def test_not_a_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "c.json")
    monkeypatch.setattr(projects, "current", lambda: tmp_path)
    assert "not a git repository" in gp.git_project({"action": "status"})
