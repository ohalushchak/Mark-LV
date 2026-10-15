"""The SELF-REPAIR panel and the review overlay. Headless — Qt's offscreen platform."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QTextEdit

_app = QApplication.instance() or QApplication([])

import ui                                             # noqa: E402
from core import health, self_repair as sr            # noqa: E402


class _Sig:
    def __init__(self):
        self.lines = []

    def emit(self, line):
        self.lines.append(line)


@pytest.fixture
def panel(monkeypatch):
    sr.reset()
    health._listeners.clear()
    sr._listeners.clear()
    reviews = []
    owner = SimpleNamespace(_log_sig=_Sig(), _show_review_overlay=lambda: reviews.append(1))
    p = ui.RepairPanel(owner)
    p.reviews = reviews
    yield p
    health._listeners.clear()
    sr._listeners.clear()
    sr.reset()


def _repair(**kw):
    base = dict(id="1", kind="fix", task="weather crashes on an empty city", prompt="",
                fingerprint="f", state="review", step="ready for review",
                files=[{"path": "actions/weather_report.py", "status": "M", "added": 4,
                        "removed": 1, "flag": ""},
                       {"path": "main.py", "status": "M", "added": 1, "removed": 1,
                        "flag": "core"}],
                added=5, removed=2, before={"passed": 10, "failed": ["t::a"]},
                after={"passed": 11, "failed": []}, report="Guarded the empty city.")
    base.update(kw)
    return sr.Repair(**base)


def test_health_line_follows_the_tools(panel):
    panel.refresh()
    assert panel._health_lbl.text() == "● HEALTHY"
    health.record("weather_report", False, "crash", "boom")
    panel.refresh()
    assert panel._health_lbl.text() == "● 1 WARNING"
    for _ in range(5):
        health.record("weather_report", False, "crash", "boom")
    panel.refresh()
    assert panel._health_lbl.text() == "● 1 FAILING"
    assert "weather_report" in panel._health_lbl.toolTip()


def test_buttons_follow_the_repair_state(panel):
    panel.refresh()
    assert panel._state_lbl.isHidden() and panel._btns["review"].isHidden()
    with sr._lock:
        sr._current = _repair(state="agent", step="Claude Code is working")
    panel.refresh()
    assert not panel._btns["stop"].isHidden() and panel._btns["review"].isHidden()
    assert "Claude Code is working" in panel._state_lbl.text()
    with sr._lock:
        sr._current = _repair()
    panel.refresh()
    assert not panel._btns["review"].isHidden() and panel._btns["stop"].isHidden()
    assert "2 file(s), +5 −2" in panel._state_lbl.text()
    panel._click("review")
    assert panel.reviews == [1]
    with sr._lock:
        sr._current = _repair(state="applied", step="applied")
    panel.refresh()
    assert not panel._btns["restart"].isHidden()


def test_review_overlay_shows_files_tests_and_the_diff():
    diff = ("diff --git a/main.py b/main.py\n@@ -1 +1 @@\n-x = 1\n+x = <2>\n")
    ov = ui.ReviewOverlay(_repair(), diff)
    texts = " ".join(l.text() for l in ov.findChildren(ui.QLabel))
    assert "weather crashes on an empty city" in texts
    assert "⚠ core code" in texts and "1 fixed" in texts
    view = ov.findChildren(QTextEdit)[0]
    assert "x = <2>" in view.toPlainText()                # escaped, not swallowed as a tag
    got = []
    ov.decided.connect(got.append)
    buttons = {b.text().strip("▸ ").strip(): b for b in ov.findChildren(ui.QPushButton)}
    assert buttons["LATER"].isDefault()                   # Enter never applies
    buttons["APPLY"].click()
    assert got == ["apply"]


def test_a_waiting_question_shows_start(panel, monkeypatch):
    calls = []
    monkeypatch.setattr(sr, "run", lambda player=None, by_button=False: calls.append(by_button) or "")
    with sr._lock:
        sr._pending = {"task": "add a docstring", "asked_at": __import__("time").monotonic()}
    panel.refresh()
    assert not panel._btns["start"].isHidden() and not panel._btns["drop"].isHidden()
    assert "Waiting for your OK" in panel._state_lbl.text()
    panel._click("start")
    for _ in range(50):
        if calls:
            break
        __import__("time").sleep(0.02)
    assert calls == [True]
    with sr._lock:
        sr._pending = None
    panel.refresh()
    assert panel._btns["start"].isHidden()



def test_the_review_names_do_not_collide_with_the_document_review():
    """Seen live: show_review / _review_sig already belonged to the document
    review; the later definitions won and the self-repair review never opened."""
    import inspect
    assert list(inspect.signature(ui.JarvisUI.show_repair_review).parameters) == ["self"]
    assert "title" in inspect.signature(ui.JarvisUI.show_review).parameters
    assert hasattr(ui.MainWindow, "_repair_review_sig")
