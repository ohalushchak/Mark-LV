"""
memory/trust.py — did the user actually say what the model is saving?

WHY
    save_memory is called by the model, silently. Most saves are faithful, but
    a model also "remembers" what it inferred, misheard or read off the screen,
    and a wrong fact in the prompt is repeated with confidence in every session
    after. So a save is checked against what the user said or typed in the last
    15 minutes (core/activity.recent_user_text):

        backed     → active: it reaches the prompt, source "user", and the
                     utterance that backs it is kept as evidence;
        not backed → proposed: searchable, but tagged unconfirmed and kept out
                     of the prompt until the user accepts it on the MEMORY
                     panel (reyesservice34-hue's propose/accept).

HOW "BACKED" IS DECIDED
    Two checks; either one is enough.

    1. Words. Share of the value's content words found in an utterance, where
       words meet on a common prefix (inflection: "Львові" / "Львів") and are
       also compared transliterated, so "Bohdan" in a value meets "Богдан" in a
       transcript. TeddyDARKVADOR's gate was words-only and failed exactly
       there: any name heard in Cyrillic but saved in Latin made the fact
       "unsupported", so a Ukrainian speaker's facts never reached the prompt.

    2. Meaning. Cosine between "key: value" and each utterance, both embedded
       with the sentence-similarity prompt. This catches a translation or a
       paraphrase ("Lviv" for "я живу у Львові", "nuts" for "алергія на
       горіхи"). Calibrated with EmbeddingGemma on twelve facts a model would
       really save, each against the utterance it came from and against seven
       unrelated commands of a session: backed pairs scored 0.71–0.95,
       unrelated ones at most 0.66 (and that one was "Lviv" next to "яка
       погода у Львові?"). The floor is 0.69. With the document prompt the two
       groups overlapped (0.61 backed against 0.62 unrelated).

    A question backs nothing. Seen live: after "що в мене в нотатках про
    <a project>?" the model saved "Є нотатки про документи … <project>" as a fact, and the
    meaning check passed it — a question about a topic is close in meaning to
    a statement about it. Only what the user TOLD counts, so utterances that
    end in "?" or open with a question word are left out of both checks.

    What is never checked: the language observation (identity/language), which
    the prompt tells the model to save from how the user speaks, not from what
    they say.
"""

from __future__ import annotations

from memory import embedder as _emb
from memory.search import _STOP, tokens

MEANING_FLOOR = 0.69
WORD_SHARE = 0.5

_EXEMPT = {("identity", "language")}


def _latin(word: str) -> str:
    try:
        from core.wake_engines.translit import to_latin
        return to_latin(word).replace(" ", "")
    except Exception:
        return word


def _content_words(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in _STOP and (len(t) >= 3 or t.isdigit())]


def _meet(a: str, b: str) -> bool:
    if a == b:
        return True
    n = min(len(a), len(b), 5)
    if n >= 3 and a[:n] == b[:n] and abs(len(a) - len(b)) <= 3:
        return True
    la, lb = _latin(a), _latin(b)
    if la == lb:
        return True
    n = min(len(la), len(lb), 5)
    return n >= 4 and la[:n] == lb[:n] and abs(len(la) - len(lb)) <= 3


def word_support(value: str, utterance: str) -> float:
    """Share of the value's content words that the utterance contains."""
    words = _content_words(value)
    if not words:
        return 0.0
    heard = _content_words(utterance)
    found = sum(1 for w in words if any(_meet(w, h) for h in heard))
    return found / len(words)


_QUESTION_WORDS = {
    "що", "як", "де", "коли", "хто", "чому", "навіщо", "який", "яка", "яке",
    "які", "якого", "яку", "куди", "звідки", "скільки", "чи", "хіба",
    "what", "who", "where", "when", "why", "how", "which", "whose", "is", "are",
    "do", "does", "did", "can", "could", "will", "would", "should",
}


def is_question(utterance: str) -> bool:
    u = utterance.strip()
    if u.endswith("?"):
        return True
    words = tokens(u)
    return bool(words) and words[0] in _QUESTION_WORDS


def judge(category: str, key: str, value: str,
          utterances: list[str]) -> tuple[str, str]:
    """('active' | 'proposed', evidence) for a fact the model wants to save."""
    if (category, key) in _EXEMPT:
        return "active", ""
    utterances = [u for u in utterances if u and u.strip() and not is_question(u)]
    if not utterances:
        return "proposed", ""

    best_u, best_w = "", 0.0
    for u in utterances:
        s = word_support(value, u)
        if s > best_w:
            best_u, best_w = u, s
    if best_w >= WORD_SHARE:
        return "active", best_u[:240]

    emb = _emb.get()
    if emb is not None and emb.ready():
        try:
            text = f"{key.replace('_', ' ')}: {value}"
            vecs = emb.embed_statements([text] + utterances)
            sims = vecs[1:] @ vecs[0]
            i = int(sims.argmax())
            if float(sims[i]) >= MEANING_FLOOR:
                return "active", utterances[i][:240]
        except Exception as e:
            print(f"[Memory] ⚠️ Meaning check failed: {e}")
    return "proposed", ""
