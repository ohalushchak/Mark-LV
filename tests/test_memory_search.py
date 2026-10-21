"""Search by words and by meaning (memory/search.py), with a fake embedder
that maps a few Ukrainian and English words to the same concepts."""

import numpy as np
import pytest

from memory import embedder
from memory import memory_manager as mm
from memory import search
from memory import store
from memory_fakes import FakeEmbedder


@pytest.fixture
def emb():
    e = FakeEmbedder()
    embedder.use(e)
    yield e
    embedder.use(None)


def _index():
    for kind in ("fact", "session", "chunk"):
        search.embed_pending(kind)


# ── The full-text query builder ───────────────────────────────────────────────

def test_fts_query_quotes_every_term_and_drops_question_words():
    q = search.fts_query('Де живе моя сестра? NOT "x" e-mail')
    assert q is not None
    for part in q.split(" OR "):
        assert part.startswith('"')
    assert '"де"' not in q and '"моя"' not in q
    assert '"сест"*' in q          # сестра → prefix, meets сестри / сестрі
    assert '"not"' in q            # an operator word is just a word


def test_fts_query_unifies_apostrophes_and_splits_like_fts5():
    assert search.fts_query("м’ята") == search.fts_query("м'ята") == search.fts_query("мʼята")


def test_fts_query_of_only_stopwords_is_none():
    assert search.fts_query("що це таке?") is None
    assert search.fts_query("") is None


# ── Hybrid ranking ────────────────────────────────────────────────────────────

def test_meaning_crosses_languages(emb):
    mm.save_fact("relationships", "sister", "Олена, живе у Львові")
    mm.save_fact("preferences", "coffee", "black, no sugar")
    _index()
    assert "Олена" in mm.search_memory("where does my sister live")
    assert "black" in mm.search_memory("яку каву я п'ю")


def test_unrelated_question_finds_nothing(emb):
    mm.save_fact("relationships", "sister", "Олена")
    _index()
    assert mm.search_memory("capital of France").startswith("Nothing stored")


def test_weak_hits_are_capped_strong_ones_are_not(emb, monkeypatch):
    for i in range(6):
        mm.save_fact("notes", f"n{i}", f"thing {i}")
    _index()
    q = emb.embed_query("x")
    fake = [(i + 1, 0.22) for i in range(6)]
    monkeypatch.setattr(search.INDEX, "search", lambda *a, **k: fake)
    hits = search.hybrid("fact", "facts_fts", "zzz")
    assert len(hits) == search.WEAK_KEEP
    fake[:] = [(i + 1, 0.5) for i in range(6)]
    assert len(search.hybrid("fact", "facts_fts", "zzz")) == 6
    assert q.shape == (256,)


def test_proposed_and_active_are_searched_superseded_is_not(emb):
    mm.save_fact("notes", "dog", "бігль Бакс")
    mm.save_fact("notes", "dog", "такса Рекс")        # бігль is history now
    _index()
    out = mm.search_memory("собака")
    assert "Рекс" in out and "Бакс" not in out


def test_full_text_still_answers_while_the_model_loads():
    mm.save_fact("notes", "dog", "бігль Бакс")
    assert "Бакс" in mm.search_memory("Бакс")


# ── Vectors kept in step ──────────────────────────────────────────────────────

def test_vectors_are_tagged_and_redone_for_another_model(emb):
    mm.save_fact("notes", "a", "кава")
    assert search.embed_pending("fact") == 1
    assert search.embed_pending("fact") == 0                 # nothing pending
    embedder.use(FakeEmbedder("other-model"))
    assert search.embed_pending("fact") == 1                 # re-embedded, not dropped
    row = store.get().one("SELECT model FROM vectors WHERE kind = 'fact'")
    assert row["model"] == "other-model"


def test_an_edited_text_is_re_embedded(emb):
    s = mm.save_fact("notes", "a", "кава")
    search.embed_pending("fact")
    store.get().run("UPDATE facts SET value = 'чай' WHERE id = ?", (s.id,))
    assert search.embed_pending("fact") == 1


def test_superseded_value_loses_its_vector(emb):
    a = mm.save_fact("notes", "a", "кава")
    search.embed_pending("fact")
    mm.save_fact("notes", "a", "чай")
    assert store.get().one("SELECT COUNT(*) FROM vectors WHERE ref = ?", (a.id,))[0] == 0


def test_a_row_deleted_mid_embedding_gets_no_vector(emb, monkeypatch):
    s = mm.save_fact("notes", "a", "кава")
    real = emb.embed_docs

    def embed_then_delete(texts, titles=None):
        store.get().run("DELETE FROM facts WHERE id = ?", (s.id,))
        return real(texts, titles)

    monkeypatch.setattr(emb, "embed_docs", embed_then_delete)
    search.embed_pending("fact")
    assert store.get().one("SELECT COUNT(*) FROM vectors")[0] == 0


def test_vector_index_caches_until_a_write(emb):
    mm.save_fact("notes", "a", "кава")
    search.embed_pending("fact")
    model = emb.model_id
    assert search.INDEX.count("fact", model) == 1
    mm.save_fact("notes", "b", "чай")
    search.embed_pending("fact")
    assert search.INDEX.count("fact", model) == 2


def test_dimension_mismatch_is_ignored_not_raised(emb):
    mm.save_fact("notes", "a", "кава")
    search.embed_pending("fact")
    assert search.INDEX.search("fact", emb.model_id, np.ones(7, np.float32)) == []


def test_indexer_runs_its_tasks_then_embeds(emb):
    seen = []
    ix = search.Indexer()
    ix.add_task(lambda: seen.append("task"))
    mm.save_session_summary("Говорили про Слак.")
    ix.run_once()
    assert seen == ["task"]
    assert store.get().one("SELECT COUNT(*) FROM vectors WHERE kind = 'session'")[0] == 1


def test_sessions_are_found_by_meaning(emb):
    mm.save_session_summary("Налаштовували повідомлення в Слеку.")
    mm.save_session_summary("Обирали квитки в Японію.")
    _index()
    out = mm.search_sessions("slack")
    assert "Слеку" in out and "Японію" not in out
