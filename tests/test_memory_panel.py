"""The MEMORY panel: suggestions with ✓ / ✕, history marks, and the memory
settings (search by meaning, notes vault). Headless — Qt's offscreen platform."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

_app = QApplication.instance() or QApplication([])

import ui                                             # noqa: E402
from memory import config_manager as cfg              # noqa: E402
from memory import embedder                           # noqa: E402
from memory import memory_manager as mm               # noqa: E402
from memory import notes_index as ni                  # noqa: E402


@pytest.fixture
def settings(monkeypatch):
    """Keep the panel's setting writes out of the real config file."""
    state = {"semantic": True, "vault": ""}
    monkeypatch.setattr(cfg, "get_semantic_memory_enabled", lambda: state["semantic"])
    monkeypatch.setattr(cfg, "save_semantic_memory_enabled",
                        lambda v: state.__setitem__("semantic", bool(v)))
    monkeypatch.setattr(cfg, "get_notes_vault", lambda: state["vault"])
    monkeypatch.setattr(cfg, "save_notes_vault", lambda v: state.__setitem__("vault", v))
    monkeypatch.setattr(embedder, "available", lambda: False)
    return state


def _texts(w):
    return [l.text() for l in w.findChildren(QLabel)]


def _buttons(w, text):
    return [b for b in w.findChildren(QPushButton) if b.text() == text and b.isVisibleTo(w)]


def _settle():
    for _ in range(3):
        _app.processEvents()


def test_suggestions_come_first_and_can_be_confirmed(settings):
    mm.save_fact("notes", "dog", "Бакс", source="user")
    mm.save_fact("notes", "dog", "Рекс", source="user")
    p = mm.save_fact("identity", "job", "QA engineer", status="proposed")
    ov = ui.MemoryOverlay()
    text = " ".join(_texts(ov))
    assert "1 suggestion(s) to confirm" in text and "memory/memory.db" in text
    assert "↺1" in text                                  # dog replaced one value
    ok = _buttons(ov, "✓")
    assert len(ok) == 1
    ok[0].click()
    _settle()
    assert mm.load_memory()["identity"]["job"]["value"] == "QA engineer"
    assert not _buttons(ov, "✓")
    assert p.id


def test_rejecting_a_suggestion_drops_it(settings):
    mm.save_fact("notes", "car", "Skoda", status="proposed")
    ov = ui.MemoryOverlay()
    rejects = [b for b in ov.findChildren(QPushButton)
               if b.text() == "✕" and b.toolTip().startswith("Not true")]
    rejects[0].click()
    _settle()
    assert mm.stats()["proposed"] == 0


def test_settings_lines_and_semantic_toggle(settings, monkeypatch):
    monkeypatch.setattr(ni, "vault_path", lambda: None)
    ov = ui.MemoryOverlay()
    text = " ".join(_texts(ov))
    assert "Search by meaning: unavailable" in text
    assert "Notes: no vault" in text
    _buttons(ov, "TURN OFF")[0].click()
    _settle()
    assert settings["semantic"] is False
    assert "Search by meaning: off" in " ".join(_texts(ov))


def test_choosing_a_vault(settings, monkeypatch, tmp_path):
    v = tmp_path / "Brain"
    v.mkdir()
    (v / "Note.md").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(ni, "vault_path", lambda: v if settings["vault"] else None)
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *a, **k: str(v))
    ov = ui.MemoryOverlay()
    _buttons(ov, "CHOOSE…")[0].click()
    _settle()
    assert settings["vault"] == str(v)
    ni.rescan(force=True)
    ov._rebuild()
    _settle()
    text = " ".join(_texts(ov))
    assert "Notes: Brain (chosen) — 1 notes" in text
    _buttons(ov, "AUTO")[0].click()
    _settle()
    assert settings["vault"] == ""
