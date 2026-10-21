"""A stand-in for memory/embedder.Embedder: deterministic, instant, no model.

Words are mapped to concepts through a small bilingual table, so "сестра" and
"sister" land on the same dimension — enough to test that search crosses
languages without loading the real 1.24 GB model. Unknown words hash to their
own dimension."""

import hashlib

import numpy as np

from memory.search import tokens

DIM = 256

CONCEPTS = {
    "сестра": "sister", "сестри": "sister", "сестру": "sister", "sister": "sister",
    "дружина": "wife", "дружину": "wife", "wife": "wife",
    "кава": "coffee", "каву": "coffee", "coffee": "coffee",
    "львів": "lviv", "львові": "lviv", "lviv": "lviv",
    "собака": "dog", "пес": "dog", "пса": "dog", "dog": "dog",
    "робота": "job", "працюю": "job", "job": "job", "work": "job",
    "слак": "slack", "слеку": "slack", "slack": "slack", "месенджер": "slack",
    "японія": "japan", "японію": "japan", "japan": "japan",
    "горіхи": "nuts", "nuts": "nuts", "алергія": "allergy", "allergy": "allergy",
}


def _dim(word: str) -> int:
    concept = CONCEPTS.get(word, word)
    return int(hashlib.md5(concept.encode("utf-8")).hexdigest(), 16) % DIM


def vec(text: str) -> np.ndarray:
    v = np.zeros(DIM, np.float32)
    for w in tokens(text):
        v[_dim(w)] += 1.0
    n = np.linalg.norm(v)
    return v / n if n else v


class FakeEmbedder:
    label = "Fake"
    error = ""

    def __init__(self, model_id: str = "fake-model"):
        self.model_id = model_id
        self.calls = 0

    def ready(self):
        return True

    def loading(self):
        return False

    def start(self, on_ready=None):
        if on_ready:
            on_ready()

    def embed_docs(self, texts, titles=None):
        self.calls += 1
        return np.vstack([vec(t) for t in texts]) if texts else np.zeros((0, DIM), np.float32)

    def embed_query(self, text):
        self.calls += 1
        return vec(text)

    def embed_statements(self, texts):
        return self.embed_docs(texts)
