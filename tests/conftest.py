"""Shared test isolation: nothing a test does may land in the user's real
error journal or self-repair folders under ~/.jarvis, or in their memory
database, or index their real notes vault."""
import pytest

from core import health, self_repair
from memory import embedder as memory_embedder
from memory import memory_manager, notes_index
from memory import search as memory_search
from memory import store as memory_store


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


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path, monkeypatch):
    memory_store.use(memory_store.Store(tmp_path / "memory.db"))
    monkeypatch.setattr(memory_manager, "LEGACY_JSON", tmp_path / "long_term.json")
    monkeypatch.setattr(memory_manager, "_ready_for", None)
    # Never start the background model load or indexer thread from a test.
    monkeypatch.setattr(memory_manager, "_started", True)
    # Recheck an unbacked save at once: a timer thread outliving the test would
    # open the real memory.db after this fixture has let go of the test store.
    monkeypatch.setattr(memory_manager, "RECHECK_S", 0)
    monkeypatch.setattr(notes_index, "vault_path", lambda: None)
    monkeypatch.setattr(notes_index, "_last_scan", 0.0)
    memory_embedder.use(None)
    memory_search.INDEX.changed()
    yield
    memory_embedder.use(None)
    memory_store.use(None)
