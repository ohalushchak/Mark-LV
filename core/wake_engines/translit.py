"""Spell a phrase the way it sounds, in English letters.

The open-vocabulary spotter (sherpa) has only an English acoustic model and an
English sub-word vocabulary. It can still spot a Ukrainian name, as long as it
is written the way an English reader would pronounce it: "Марко" -> "MARKO",
"Джарвіс" -> "JARVIS". The table follows English spelling habits rather than
the official passport transliteration — "kh" would be read as two sounds, so
х is "h"; ий at the end of a word is one sound, so it is "iy".

Sounds English does not have (и, щ, soft consonants) only come out
approximately, which is why the settings let the user type `spoken_as` by
hand.
"""
from __future__ import annotations

import re

_MULTI = {
    "дж": "j", "зг": "zgh", "ий": "iy", "ій": "iy",
}
_SINGLE = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
    "є": "ye", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "yi", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ь": "", "ю": "yu", "я": "ya",
    # Russian letters, so a Russian name does not come out half-empty.
    "ы": "y", "э": "e", "ё": "yo", "ъ": "",
    "'": "", "’": "", "ʼ": "",
}


def to_latin(text: str) -> str:
    """Lower-case Latin spelling of `text`; Latin input passes through."""
    s = (text or "").lower()
    out, i = [], 0
    while i < len(s):
        two = s[i:i + 2]
        if two in _MULTI:
            out.append(_MULTI[two])
            i += 2
            continue
        out.append(_SINGLE.get(s[i], s[i]))
        i += 1
    # Keep letters, digits and spaces only; the spotter's vocabulary has no
    # punctuation and an apostrophe would only become an unknown token.
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", "".join(out)).split())
