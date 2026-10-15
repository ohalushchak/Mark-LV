"""The AGENT panel: engine buttons, model and effort lists, limits, the job
line and the compact card's agent line. Headless — Qt's offscreen platform."""

import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import ui                                             # noqa: E402
from core import agents                               # noqa: E402
from memory import config_manager as cm               # noqa: E402

CODEX_MODELS = [agents.Model("gpt-6-astra", "GPT-6-Astra", ["low", "high", "ultra"], "low", True),
                agents.Model("gpt-6-luna", "GPT-6-Luna", ["low", "medium"], "medium")]


class _Sig:
    def __init__(self):
        self.lines = []

    def emit(self, line):
        self.lines.append(line)


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "config" / "api_keys.json")
    monkeypatch.setattr(agents, "models", lambda engine, refresh=False: list(CODEX_MODELS)
                        if engine == "codex" else
                        [agents.Model(m, label, list(agents.CLAUDE_EFFORTS))
                         for m, label in agents.CLAUDE_MODELS])
    monkeypatch.setattr(agents, "refresh_usage", lambda engine: "refreshed")
    agents.reset()
    agents._listeners.clear()
    card_lines = []
    owner = SimpleNamespace(_log_sig=_Sig(), _tray=SimpleNamespace(refresh=lambda: None),
                            _compact=SimpleNamespace(set_agent_line=card_lines.append))
    p = ui.AgentPanel(owner)
    p.card_lines = card_lines
    for _ in range(40):                  # let the model-list worker land
        _app.processEvents()
        if p._models.get("codex"):
            break
        time.sleep(0.02)
    yield p
    agents._listeners.clear()
    agents.reset()


def _items(cb):
    return [cb.itemData(i) for i in range(cb.count())]


def test_engine_buttons_switch_and_lists_follow(panel):
    assert agents.current()["engine"] == "claude"
    assert _items(panel._model_cb) == ["", "fable", "opus", "sonnet", "haiku"]
    assert _items(panel._effort_cb) == ["", "low", "medium", "high", "xhigh", "max"]
    panel.pick("codex")
    panel.refresh()
    assert agents.current()["engine"] == "codex"
    assert _items(panel._model_cb) == ["", "gpt-6-astra", "gpt-6-luna"]
    # "Default" for Codex is the account's ★ model, so its efforts are shown.
    assert _items(panel._effort_cb) == ["", "low", "high", "ultra"]
    assert "⚡" in panel._effort_cb.itemText(3)


def test_effort_list_depends_on_the_model(panel):
    agents.set_engine("codex")
    agents.set_model("gpt-6-luna")
    panel.refresh()
    assert _items(panel._effort_cb) == ["", "low", "medium"]
    agents.set_model("gpt-6-astra")
    agents.set_effort("ultra")
    panel.refresh()
    assert panel._effort_cb.currentData() == "ultra"
    agents.set_model("gpt-6-luna")                # ultra unavailable → model default
    panel.refresh()
    assert panel._effort_cb.currentData() == "medium"


def test_settings_are_kept_per_engine(panel):
    agents.set_model("opus")
    agents.set_effort("max")
    panel.pick("codex")
    panel.pick("claude")
    panel.refresh()
    assert panel._model_cb.currentData() == "opus"
    assert panel._effort_cb.currentData() == "max"


def test_usage_bars_and_the_job_line(panel):
    agents._store_usage("claude", {"five_hour": {"pct": 37, "resets_at": 0},
                                   "week": {"pct": 92, "resets_at": 0}})
    panel.refresh()
    assert panel._bar_5h._value == 37 and panel._bar_week._value == 92
    assert panel._job_box.isHidden()
    job = agents.Job(engine="claude", task="Add CSV export", cwd=".")
    job.steps, job.last_step = 4, "Edit: app.py"
    agents._job = job
    panel.refresh()
    assert not panel._job_box.isHidden() and "Add CSV export" in panel._job_lbl.text()
    assert not panel._stop_btn.isHidden()
    assert panel.card_lines[-1].startswith("● Claude Code working")
    job.done, job.ok = True, True
    panel.refresh()
    assert panel._job_lbl.text().startswith("✓") and panel._stop_btn.isHidden()
    assert panel.card_lines[-1] == ""


def test_the_compact_card_shows_the_agent_line():
    from test_compact import _owner
    card = ui.CompactCard(_owner())
    assert card._agent.isHidden()
    card.set_agent_line("● Codex working · 2 min")
    assert not card._agent.isHidden() and "Codex" in card._agent.text()
    card.set_agent_line("")
    assert card._agent.isHidden()
