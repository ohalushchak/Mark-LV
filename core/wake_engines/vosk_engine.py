"""Vosk with a grammar restricted to the wake phrase.

The only engine here that knows Ukrainian words natively: the small Ukrainian
Kaldi model recognises "марко" as a Ukrainian word, not as an English
approximation of one. The grammar limits it to the phrase plus "[unk]" — without
"[unk]" every utterance would be forced onto the phrase.

Two costs, both inherent to a recogniser rather than a keyword model: the
acoustic model runs continuously (noticeably more CPU than the other two
engines), and a result only arrives at the end of an utterance. The second is
hidden from the user: the hit reports when the phrase ended, and the detector
hands the model everything said after it.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core.wake_engines import (
    Hit, Logger, SAMPLE_RATE, WakeEngine, WakeEngineError, WakeSettings,
    phrase_alternatives,
)

MODELS = {
    "uk": "vosk-model-small-uk-v3-nano",     # ~73 MB
    "en": "vosk-model-small-en-us-0.15",     # ~40 MB
}
MODEL_BASE_URL = "https://alphacephei.com/vosk/models/"


def min_confidence(sensitivity: float) -> float:
    """Per-word confidence a hit needs: 0.95 strict, 0.8 default, 0.65 eager."""
    return 0.95 - 0.3 * max(0.0, min(1.0, sensitivity))


def match(words: list[dict], phrases: list[list[str]], min_conf: float) -> dict | None:
    """The last word of the first phrase found, in order, in a Vosk result."""
    seq = [w.get("word", "") for w in words]
    for ph in phrases:
        n = len(ph)
        for i in range(len(seq) - n + 1):
            if seq[i:i + n] == ph:
                part = words[i:i + n]
                if min(float(w.get("conf", 0.0)) for w in part) >= min_conf:
                    return part[-1]
    return None


class VoskEngine(WakeEngine):
    id = "vosk"
    title = "Vosk — native Ukrainian or English words"
    pip_packages = ("vosk",)
    import_names = ("vosk",)

    def __init__(self, settings: WakeSettings):
        super().__init__(settings)
        self._model = None
        self._rec = None
        self._fed = 0                       # samples since the recogniser was made
        self._phrases: list[list[str]] = []
        self.min_conf = min_confidence(settings.sensitivity)

    @staticmethod
    def model_dir(settings: WakeSettings) -> Path:
        return Path(settings.models_dir) / MODELS.get(settings.vosk_lang, MODELS["uk"])

    @classmethod
    def models_present(cls, settings: WakeSettings) -> bool:
        try:
            d = cls.model_dir(settings)
            return (d / "am").is_dir() and (d / "conf").is_dir()
        except Exception:
            return False

    @classmethod
    def download(cls, settings: WakeSettings, log: Logger) -> None:
        from core.wake_engines._download import install_archive
        name = cls.model_dir(settings).name
        install_archive(MODEL_BASE_URL + name + ".zip", Path(settings.models_dir), name, log)

    def load(self) -> None:
        try:
            from vosk import Model, SetLogLevel
        except Exception as e:
            raise WakeEngineError(f"vosk is not installed — {e}") from e
        SetLogLevel(-1)
        try:
            self._model = Model(str(self.model_dir(self.settings)))
        except Exception as e:
            raise WakeEngineError(f"could not load the Vosk model — {e}") from e

        self._phrases = [p.split() for p in phrase_alternatives(self.settings.phrase)]
        if not self._phrases:
            raise WakeEngineError("the wake phrase is empty")
        # A word outside the model's vocabulary is silently dropped from the
        # grammar, and the engine would then never fire. Say so instead.
        missing = sorted({w for ph in self._phrases for w in ph
                          if self._model.vosk_model_find_word(w) < 0})
        if missing:
            lang = {"uk": "Ukrainian", "en": "English"}.get(self.settings.vosk_lang, "")
            raise WakeEngineError(
                f"not in the {lang} model's vocabulary: {', '.join(missing)}. "
                "Spell it the way the language writes it, pick a word the model "
                "knows, or switch to the Sherpa engine, which takes any phrase.")
        self._new_recognizer()

    def _new_recognizer(self) -> None:
        from vosk import KaldiRecognizer
        grammar = [" ".join(p) for p in self._phrases] + ["[unk]"]
        self._rec = KaldiRecognizer(self._model, SAMPLE_RATE,
                                    json.dumps(grammar, ensure_ascii=False))
        self._rec.SetWords(True)
        self._fed = 0

    def process(self, block) -> Hit | None:
        x = np.asarray(block, dtype=np.int16).reshape(-1)
        self._fed += len(x)
        if not self._rec.AcceptWaveform(x.tobytes()):
            return None
        words = json.loads(self._rec.Result()).get("result") or []
        last = match(words, self._phrases, self.min_conf)
        if last is None:
            return None
        # Word times count from the recogniser's creation, the same origin as
        # `_fed`, so the gap is how long ago the phrase ended.
        ago = max(0.0, self._fed / SAMPLE_RATE - float(last.get("end", 0.0)))
        score = float(last.get("conf", 1.0))
        self._new_recognizer()
        return Hit(self.label, score, ago_s=ago)

    def reset(self) -> None:
        # A fresh recogniser rather than Reset(): it also restarts the word
        # clock, which the `ago` arithmetic above depends on.
        if self._model is not None:
            self._new_recognizer()

    def close(self) -> None:
        self._rec = None
        self._model = None

    @property
    def label(self) -> str:
        alts = phrase_alternatives(self.settings.phrase)
        return alts[0].title() if alts else self.settings.phrase
