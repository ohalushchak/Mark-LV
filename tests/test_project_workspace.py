"""project_workspace: reads, exact edits, new files, secrets, search."""
import pytest

from actions import project_workspace as pw
from core import projects, undo
from memory import config_manager as cm


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "config" / "api_keys.json")
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text(
        "def add(a, b):\n    return a - b\n\nKEY = 'F9'\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.js").write_text("KEY", encoding="utf-8")
    monkeypatch.setattr(projects, "current", lambda: root)
    pw._reads.clear()
    undo.clear()
    yield root
    undo.clear()


def run(**kw):
    return pw.project_workspace(kw)


def test_read_numbers_lines(proj):
    out = run(action="read", path="src/app.py")
    assert "2:     return a - b" in out and "4 lines" in out
    assert "showing 2–2" in run(action="read", path="src/app.py", line=2, count=1)


def test_patch_needs_a_read_and_a_unique_match(proj):
    args = dict(action="patch", path="src/app.py", old_text="a - b", new_text="a + b")
    assert "Read src/app.py first" in run(**args)
    run(action="read", path="src/app.py")
    assert "occurs 0 times" in run(**{**args, "old_text": "nope"}) or \
        "not found" in run(**{**args, "old_text": "nope"})
    out = run(**args)
    assert out.startswith("Edited src/app.py at line 2")
    assert "a + b" in (proj / "src" / "app.py").read_text(encoding="utf-8")
    assert "undone" in undo.undo_last()
    assert "a - b" in (proj / "src" / "app.py").read_text(encoding="utf-8")


def test_an_edit_made_meanwhile_is_never_overwritten(proj):
    run(action="read", path="src/app.py")
    (proj / "src" / "app.py").write_text("changed in the editor\n", encoding="utf-8")
    out = run(action="patch", path="src/app.py", old_text="changed", new_text="x")
    assert "changed since you last read it" in out


def test_ambiguous_text_is_refused(proj):
    (proj / "dup.txt").write_text("same\nsame\n", encoding="utf-8")
    run(action="read", path="dup.txt")
    out = run(action="patch", path="dup.txt", old_text="same", new_text="x")
    assert "occurs 2 times" in out


def test_windows_line_endings_still_match(proj):
    (proj / "crlf.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    run(action="read", path="crlf.txt")
    out = run(action="patch", path="crlf.txt", old_text="one\ntwo", new_text="1\n2")
    assert out.startswith("Edited")
    assert (proj / "crlf.txt").read_bytes() == b"1\r\n2\r\nthree\r\n"


def test_create_never_overwrites_and_undoes(proj):
    assert "already exists" in run(action="create", path="README.md", content="x")
    out = run(action="create", path="pkg/new/mod.py", content="x = 1\n")
    assert out.startswith("Created pkg/new/mod.py")
    assert "Removed" in undo.undo_last()
    assert not (proj / "pkg").exists()


def test_secrets_and_escapes_are_refused(proj):
    assert "keys or passwords" in run(action="read", path=".env")
    assert "outside the project" in run(action="read", path="../outside.txt")
    assert ".env" not in run(action="list")


def test_search_skips_dependencies_and_secrets(proj):
    out = run(action="search", query="key")
    assert "src/app.py:4" in out and "node_modules" not in out and ".env" not in out
    assert "does not occur" in run(action="search", query="zzz-none")
    assert "README.md" in run(action="list", pattern="*.md")


def test_open_sets_the_current_project(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "config" / "api_keys.json")
    (tmp_path / "code" / "Demo-App").mkdir(parents=True)
    (tmp_path / "code" / "Demo-App" / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(projects, "search_roots", lambda: [tmp_path / "code"])
    out = run(action="open", project="demo app")
    assert "Demo-App" in out and "Node.js" in out and "current project" in out
    assert projects.current() == (tmp_path / "code" / "Demo-App").resolve()
