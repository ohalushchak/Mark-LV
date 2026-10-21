"""
memory/search.py — find stored text by its words AND by its meaning.

HYBRID, AND WHY BOTH HALVES
    Full-text (FTS5, BM25) is exact and instant but blind to meaning and to
    language: "сестра" never finds "sister", and Ukrainian inflection breaks
    exact words. Vectors (memory/embedder.py) bridge both, but rank a near miss
    above an exact name. So each query runs both, keeps only hits that pass
    their own gate, and fuses the two rankings with reciprocal rank fusion
    (score = Σ 1 / (60 + rank)). RRF scores are ranks, not confidence, so the
    gates decide "nothing relevant": a vector hit counts only at cosine ≥ the
    kind's floor, an FTS hit only if it matched.

    The floors were calibrated with EmbeddingGemma on Ukrainian/English facts.
    Short facts — the way the model saves them ("manager: Андрій Коваленко") —
    score low in absolute terms: the right fact 0.23–0.44 for a question,
    unrelated questions 0.04–0.17 against the same store; sentence-long facts
    score higher on both sides (right 0.47 median, unrelated at most 0.32 for
    "як приготувати борщ?" → a fact about varenyky). So a vector hit counts at
    cosine ≥ 0.20, but only the top three of those, plus any at ≥ 0.30. A
    loosely related fact offered to recall is harmless — the model judges it —
    while a missed one makes the assistant say "I don't know" about something
    it was told. Deleting is the opposite case and uses its own, stricter bar
    (memory_manager.find_about). A right fact below the floor is often caught
    by the full-text half instead: "асистент*" matches "асистента".
    Prefixing the category ("relationships — manager: …") lifted the right
    fact to first place for "who is my boss" and "яку каву я п'ю".

THE FULL-TEXT QUERY
    Built here, never passed through: every token is double-quoted, so "e-mail",
    NOT, AND, *, quotes or a colon in what the user said cannot become FTS5
    syntax (the injection found in maseko-lucky-9's builder). Tokens are OR-ed,
    and long words are cut to a prefix so inflections meet: "сестри" → "сест"*,
    "дружину" → "дружи"*. Question words and pronouns are dropped — in an OR
    query "що" or "мій" would match half the store. Apostrophes are unified
    first: unicode61 splits on the ASCII ' but keeps ʼ (U+02BC) inside a word,
    so "м'ята" and "мʼята" would otherwise be different words.

VECTORS
    VectorIndex keeps one normalised matrix per kind in RAM, rebuilt lazily
    when the store's vectors change. At a few thousand rows the scan is 1–3 ms;
    if the store ever grows by orders of magnitude, this class is the one place
    to swap in sqlite-vec or another engine.

    Indexer embeds rows that have no vector for the current model (or whose
    text changed since — tracked by hash) on a background thread, in batches,
    so nothing slow ever runs on the asyncio or Qt loop.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass

import numpy as np

from memory import embedder as _emb
from memory import store as _store

RRF_K = 60

# Cosine floors per kind, for EmbeddingGemma (see the module docstring): a
# hit at or above FLOORS counts if it is among the first WEAK_KEEP such hits,
# any hit at or above STRONG counts regardless.
FLOORS = {"fact": 0.20, "session": 0.25, "chunk": 0.30}
STRONG = {"fact": 0.30, "session": 0.35, "chunk": 0.40}
WEAK_KEEP = 3

_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "‘": "'", "ʹ": "'"})

_STOP = {
    # Ukrainian question words, pronouns, particles
    "що", "як", "де", "коли", "хто", "чому", "який", "яка", "яке", "які", "якій",
    "якого", "якому", "яку", "якої", "якою", "яким", "якими", "куди", "звідки",
    "скільки", "чи", "таке", "такий", "така", "такі", "мій", "моя", "моє",
    "мої", "мого", "моєї", "моїй", "моєму", "мене", "мені", "мною", "я", "ти",
    "ми", "ви", "він", "вона", "воно", "вони", "це", "той", "та", "те", "ті",
    "в", "у", "на", "з", "із", "зі", "до", "по", "про", "за", "від", "для",
    "і", "й", "та", "а", "але", "або", "не", "ні", "так", "ж", "же", "би", "б",
    "є", "був", "була", "було", "були", "там", "тут", "мабуть", "ще", "вже",
    "розкажи", "скажи", "нагадай", "пам'ятаєш", "знаєш",
    # English
    "what", "who", "where", "when", "why", "how", "which", "is", "are", "was",
    "were", "do", "does", "did", "my", "me", "i", "you", "your", "the", "a",
    "an", "of", "to", "in", "on", "at", "for", "and", "or", "about", "it",
    "that", "this", "tell", "remember", "know", "with",
}


def normalise_text(text: str) -> str:
    return (text or "").translate(_APOSTROPHES).casefold()


def tokens(text: str) -> list[str]:
    """Word tokens the way FTS5's unicode61 sees them (letters and digits)."""
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", normalise_text(text))


def fts_query(text: str) -> str | None:
    """An FTS5 MATCH expression for free text, or None if nothing searchable."""
    terms: list[str] = []
    for t in tokens(text):
        if t in _STOP or len(t) < 2:
            continue
        for part in t.split("'"):           # unicode61 splits on the ASCII '
            if len(part) < 2:
                continue
            if len(part) >= 6:
                terms.append(f'"{part[:len(part) - 2]}"*')
            elif len(part) >= 4:
                terms.append(f'"{part[:len(part) - 1]}"*')
            else:
                terms.append(f'"{part}"')
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return " OR ".join(out) if out else None


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


# ── Vectors ───────────────────────────────────────────────────────────────────

class VectorIndex:
    """Normalised vectors per kind, cached in RAM, scanned with numpy."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], tuple[int, np.ndarray, np.ndarray]] = {}
        self.version = 0          # bumped by every vector write

    def changed(self) -> None:
        with self._lock:
            self.version += 1

    def _matrix(self, kind: str, model: str) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            hit = self._cache.get((kind, model))
            if hit and hit[0] == self.version:
                return hit[1], hit[2]
            version = self.version
        rows = _store.get().all(
            "SELECT ref, vec FROM vectors WHERE kind = ? AND model = ?", (kind, model))
        if rows:
            ids = np.fromiter((r["ref"] for r in rows), dtype=np.int64, count=len(rows))
            mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
        else:
            ids, mat = np.zeros(0, np.int64), np.zeros((0, 1), np.float32)
        with self._lock:
            self._cache[(kind, model)] = (version, ids, mat)
        return ids, mat

    def search(self, kind: str, model: str, query: np.ndarray,
               k: int = 20, allowed: set[int] | None = None) -> list[tuple[int, float]]:
        ids, mat = self._matrix(kind, model)
        if not len(ids) or mat.shape[1] != query.shape[0]:
            return []
        scores = mat @ query
        order = np.argsort(-scores)
        out = []
        for i in order:
            ref = int(ids[i])
            if allowed is not None and ref not in allowed:
                continue
            out.append((ref, float(scores[i])))
            if len(out) >= k:
                break
        return out

    def count(self, kind: str, model: str) -> int:
        return len(self._matrix(kind, model)[0])


INDEX = VectorIndex()


# ── Hybrid search ─────────────────────────────────────────────────────────────

@dataclass
class Hit:
    ref: int
    score: float          # fused RRF score, for ordering only
    cosine: float | None  # None when the vector half did not score it
    lexical: bool         # matched by full-text


def fts_hits(fts_table: str, query: str, k: int = 20,
             allowed: set[int] | None = None) -> list[int]:
    q = fts_query(query)
    if not q:
        return []
    try:
        rows = _store.get().all(
            f"SELECT rowid FROM {fts_table} WHERE {fts_table} MATCH ? "
            f"ORDER BY rank LIMIT ?", (q, k * 3 if allowed is not None else k))
    except Exception as e:           # a query we failed to escape: say so, carry on
        print(f"[Memory] ⚠️ Full-text query failed ({q!r}): {e}")
        return []
    refs = [int(r[0]) for r in rows]
    if allowed is not None:
        refs = [r for r in refs if r in allowed]
    return refs[:k]


def hybrid(kind: str, fts_table: str, query: str, k: int = 8,
           allowed: set[int] | None = None, floor: float | None = None) -> list[Hit]:
    """Fused ranking of one kind of row. `allowed` limits which refs may be
    returned (e.g. only active and proposed facts)."""
    floor = FLOORS.get(kind, 0.3) if floor is None else floor
    lexical = fts_hits(fts_table, query, k=20, allowed=allowed)

    semantic: list[tuple[int, float]] = []
    emb = _emb.get()
    if emb is not None and emb.ready() and query.strip():
        try:
            q = emb.embed_query(query)
            semantic = INDEX.search(kind, emb.model_id, q, k=20, allowed=allowed)
        except Exception as e:
            print(f"[Memory] ⚠️ Search by meaning failed: {e}")
    cos = dict(semantic)
    strong = STRONG.get(kind, floor + 0.1)
    passed = [(ref, s) for i, (ref, s) in enumerate(semantic)
              if s >= floor and (i < WEAK_KEEP or s >= strong)]

    fused: dict[int, float] = {}
    for rank, ref in enumerate(lexical):
        fused[ref] = fused.get(ref, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, (ref, _s) in enumerate(passed):
        fused[ref] = fused.get(ref, 0.0) + 1.0 / (RRF_K + rank + 1)

    lex = set(lexical)
    hits = [Hit(ref, sc, cos.get(ref), ref in lex) for ref, sc in fused.items()]
    hits.sort(key=lambda h: (-h.score, -(h.cosine or 0.0)))
    return hits[:k]


def semantic_ready() -> bool:
    emb = _emb.get()
    return emb is not None and emb.ready()


# ── Background embedding ──────────────────────────────────────────────────────

# kind → (SQL returning id + text + title for rows of that kind)
_SOURCES = {
    "fact":    "SELECT id, category || ' — ' || replace(key, '_', ' ') || ': ' || value AS text, "
               "'none' AS title FROM facts WHERE status IN ('active', 'proposed')",
    "session": "SELECT id, summary AS text, 'none' AS title FROM sessions",
    "chunk":   "SELECT c.id, CASE WHEN c.heading != '' THEN c.heading || char(10) "
               "ELSE '' END || c.text AS text, n.title AS title "
               "FROM chunks c JOIN notes n ON n.id = c.note_id",
}


def pending(kind: str, model: str) -> list[tuple[int, str, str, str]]:
    """Rows of `kind` whose vector is missing, from another model, or stale."""
    rows = _store.get().all(_SOURCES[kind])
    have = {r["ref"]: (r["model"], r["hash"]) for r in _store.get().all(
        "SELECT ref, model, hash FROM vectors WHERE kind = ?", (kind,))}
    out = []
    for r in rows:
        h = text_hash(r["text"])
        if have.get(r["id"]) != (model, h):
            out.append((r["id"], r["text"], r["title"], h))
    return out


def embed_pending(kind: str, batch: int = 8, should_stop=lambda: False) -> int:
    """Embed every pending row of `kind`. Runs on a worker thread.

    Small batches on purpose: the model is shared behind one lock, and a spoken
    recall that arrives during the first indexing of a vault waits for the
    batch in progress — eight chunks, about a second, not sixteen."""
    emb = _emb.get()
    if emb is None or not emb.ready():
        return 0
    todo = pending(kind, emb.model_id)
    done = 0
    for i in range(0, len(todo), batch):
        if should_stop():
            break
        part = todo[i:i + batch]
        vecs = emb.embed_docs([t for _, t, _, _ in part], [ti for _, _, ti, _ in part])
        with _store.get().tx() as db:
            for (ref, _t, _ti, h), v in zip(part, vecs):
                # The row may have been deleted while we were embedding it.
                db.execute(
                    "INSERT OR REPLACE INTO vectors(kind, ref, model, hash, vec) "
                    "SELECT ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM "
                    + {"fact": "facts", "session": "sessions", "chunk": "chunks"}[kind]
                    + " WHERE id = ?)",
                    (kind, ref, emb.model_id, h, np.asarray(v, np.float32).tobytes(), ref))
        INDEX.changed()
        done += len(part)
    return done


class Indexer:
    """One background thread that keeps vectors in step with the rows.

    poke() wakes it after any write; it embeds whatever is pending and sleeps.
    Hooks registered with add_task() run on the same thread first (the notes
    rescan), so file reading never happens on the voice loop either."""

    def __init__(self):
        self._event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop = False
        self._tasks: list = []
        self.busy = False

    def add_task(self, fn) -> None:
        self._tasks.append(fn)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="memory-indexer", daemon=True)
        self._thread.start()
        self.poke()

    def stop(self) -> None:
        self._stop = True
        self._event.set()

    def poke(self) -> None:
        self._event.set()

    def run_once(self) -> None:
        for fn in list(self._tasks):
            try:
                fn()
            except Exception as e:
                print(f"[Memory] ⚠️ Index task failed: {e}")
        t0, done = time.perf_counter(), {}
        for kind in ("fact", "session", "chunk"):
            try:
                done[kind] = embed_pending(kind, should_stop=lambda: self._stop)
            except Exception as e:
                print(f"[Memory] ⚠️ Embedding {kind}s failed: {e}")
        if sum(done.values()) >= 20:          # a vault (re)index, not one fact
            parts = ", ".join(f"{n} {k}s" for k, n in done.items() if n)
            print(f"[Memory] 🧭 Indexed by meaning: {parts} "
                  f"({time.perf_counter() - t0:.0f} s)")

    def _run(self) -> None:
        while not self._stop:
            self._event.wait(timeout=300)
            self._event.clear()
            if self._stop:
                break
            self.busy = True
            try:
                self.run_once()
            finally:
                self.busy = False


INDEXER = Indexer()
