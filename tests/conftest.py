"""Shared test isolation: nothing a test does may land in the user's real
error journal or self-repair folders under ~/.jarvis."""
import pytest

from core import health, self_repair


@pytest.fixture(autouse=True)
def _isolated_health(tmp_path, monkeypatch):
    monkeypatch.setattr(self_repair, "DIR", tmp_path / "self_repair")
    monkeypatch.setattr(health, "DIR", tmp_path / "health")
    monkeypatch.setattr(health, "JOURNAL", tmp_path / "health" / "errors.jsonl")
    monkeypatch.setattr(health, "_secret_values", [])
    health.reset()
    monkeypatch.setattr(health, "_secret_values", [])
    yield
    health.reset()
