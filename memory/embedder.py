"""
memory/embedder.py — turns text into vectors, locally, for search by meaning.

WHY THIS MODEL
    Facts are stored in the words the user said them in (mostly Ukrainian, some
    English) and asked about in either language, so the model has to match
    "де живе моя сестра?" to "Сестра Олена живе у Львові" and "where does my
    sister live?" to the same row. Measured on this machine (2026-10-21) over
    25 such facts, 31 answerable and 8 unanswerable questions in both languages:

        model                               top-1   top-3   query   load
        google/embeddinggemma-300m (768)    29/31   30/31   21 ms   20 s
        intfloat/multilingual-e5-large      28/31   31/31   32 ms   33 s
        Qwen/Qwen3-Embedding-0.6B-Q         27/31   31/31  720 ms   19 s
        paraphrase-multilingual-MiniLM-L12  28/31   29/31    3 ms    7 s

    EmbeddingGemma also separates "nothing stored about that" best (median best
    score 0.20 for unanswerable questions against 0.47 for the right fact),
    while e5's scores bunch between 0.77 and 0.83 and leave no usable
    threshold. Truncating its 768 dimensions (Matryoshka) cost accuracy (27/31
    at 256), so vectors stay full size. The 1.24 GB download happens once, into
    config/embed_models (gitignored).

    It runs through fastembed on ONNX Runtime — CPU only, no torch — which the
    wake-word engines already pulled in. The vectors are L2-normalised here, so
    a dot product is the cosine.

THE PREFIXES MATTER
    EmbeddingGemma was trained with task prompts: a query is embedded as
    "task: search result | query: …" and a stored text as
    "title: … | text: …". Leaving them out measurably hurts retrieval, and the
    two sides must use their own prompt — a query embedded as a document no
    longer lines up with the documents. Comparing two statements (memory/
    trust.py: does this utterance say what the fact says?) has its own prompt,
    "task: sentence similarity | query: …", which separated backed from
    unrelated pairs cleanly where the document prompt did not.

LOADING AND CPU
    Loading takes ~20 s, so it happens on a background thread at startup
    (start()). Until it is ready, ready() is False and search falls back to its
    full-text half instead of waiting — recall must never stall the voice.

    A query embeds in ~20 ms; stored text costs more. The user's vault (83
    notes, 1,712 chunks) took 207 s to embed the first time, once, in the
    background (memory/search.Indexer). ONNX Runtime would take every core for
    that, next to the audio threads, so the model gets half of them.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from memory.store import _base_dir

CACHE_DIR = _base_dir() / "config" / "embed_models"


@dataclass(frozen=True)
class Spec:
    name: str            # fastembed model id
    label: str           # what the HUD calls it
    query: str           # prompt template for a search query
    doc: str             # prompt template for stored text; may use {title}
    similarity: str      # prompt template for comparing two statements


MODELS: dict[str, Spec] = {
    "gemma": Spec("google/embeddinggemma-300m", "EmbeddingGemma",
                  "task: search result | query: {text}",
                  "title: {title} | text: {text}",
                  "task: sentence similarity | query: {text}"),
}
DEFAULT_MODEL = "gemma"


def _normalise(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


class Embedder:
    """fastembed behind a lock, loaded in the background."""

    def __init__(self, key: str = DEFAULT_MODEL, cache_dir: Path = CACHE_DIR):
        self.spec = MODELS.get(key) or MODELS[DEFAULT_MODEL]
        self.cache_dir = Path(cache_dir)
        self._model = None
        self._lock = threading.Lock()
        self._loading = False
        self.error = ""
        self.load_s = 0.0

    @property
    def model_id(self) -> str:
        """What a vector is tagged with; a different id means re-embed."""
        return self.spec.name

    @property
    def label(self) -> str:
        return self.spec.label

    def ready(self) -> bool:
        return self._model is not None

    def loading(self) -> bool:
        return self._loading

    def start(self, on_ready=None) -> None:
        """Load on a background thread; calls on_ready() once loaded."""
        if self._model is not None or self._loading:
            return
        self._loading = True

        def _load():
            try:
                self.load()
                if on_ready:
                    on_ready()
            except Exception as e:      # recorded; search stays full-text only
                self.error = f"{type(e).__name__}: {e}"[:300]
                print(f"[Memory] ⚠️ Semantic search unavailable — {self.error}")
            finally:
                self._loading = False

        threading.Thread(target=_load, name="embedder-load", daemon=True).start()

    def load(self) -> None:
        if self._model is not None:
            return
        t = time.perf_counter()
        from fastembed import TextEmbedding
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        model = TextEmbedding(self.spec.name, cache_dir=str(self.cache_dir),
                              threads=max(1, (os.cpu_count() or 2) // 2))
        list(model.embed([self.spec.query.format(text="warm up")]))
        self._model = model
        self.load_s = time.perf_counter() - t
        print(f"[Memory] 🧭 Search by meaning ready ({self.spec.label}, "
              f"{self.load_s:.0f} s)")

    def embed_docs(self, texts: list[str], titles: list[str] | None = None) -> np.ndarray:
        titles = titles or ["none"] * len(texts)
        prompts = [self.spec.doc.format(title=(ti or "none"), text=tx)
                   for tx, ti in zip(texts, titles)]
        return self._embed(prompts)

    def embed_statements(self, texts: list[str]) -> np.ndarray:
        return self._embed([self.spec.similarity.format(text=t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([self.spec.query.format(text=text)])[0]

    def _embed(self, prompts: list[str]) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("embedding model is not loaded")
        with self._lock:
            return _normalise(np.asarray(list(self._model.embed(prompts)), dtype=np.float32))


# ── The process-wide embedder ─────────────────────────────────────────────────

_embedder: Embedder | None = None


def get() -> Embedder | None:
    """None when search by meaning is switched off or fastembed is missing."""
    return _embedder


def use(embedder) -> None:
    """Install an embedder (main.py at startup; tests install a fake)."""
    global _embedder
    _embedder = embedder


def available() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except Exception:
        return False
