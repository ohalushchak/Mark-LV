"""sherpa-onnx keyword spotting: a typed phrase, no training.

The decoder is open-vocabulary: any phrase that can be written in the model's
sub-word tokens can be spotted, so renaming the assistant needs nothing but
the new name. The model (k2-fsa, trained on GigaSpeech, 3.3 M parameters,
~18 MB download) is English — see translit.py for how a Ukrainian name gets
in.

sherpa_onnx ships its own text2token helper, but it imports pypinyin even for
English models; sentencepiece is called directly instead, which is all the
English model needs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from core.wake_engines import (
    Hit, Logger, SAMPLE_RATE, WakeEngine, WakeEngineError, WakeSettings,
    phrase_alternatives,
)
from core.wake_engines.translit import to_latin

MODEL_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             f"kws-models/{MODEL_NAME}.tar.bz2")


def keyword_params(sensitivity: float) -> tuple[float, float]:
    """(boost, threshold) for one keyword line.

    The boost helps the phrase survive the beam search, the threshold is the
    probability it must reach. At 0.5 the threshold is sherpa's own default
    (0.25); a strict setting matters most for short names, which ordinary
    speech contains far more often than a two-word phrase."""
    s = max(0.0, min(1.0, sensitivity))
    return round(1.0 + s, 2), round(0.40 - 0.30 * s, 2)


def _keep(name: str) -> bool:
    return name.endswith(".onnx") or name in ("tokens.txt", "bpe.model")


def _pick(d: Path, part: str, int8: bool) -> Path | None:
    files = sorted(d.glob(f"{part}*.onnx"))
    pref = [f for f in files if f.name.endswith(".int8.onnx") == int8]
    return (pref or files or [None])[0]


class SherpaEngine(WakeEngine):
    id = "sherpa"
    title = "Sherpa-ONNX — any phrase you type (English sounds)"
    pip_packages = ("sherpa-onnx", "sentencepiece")
    import_names = ("sherpa_onnx", "sentencepiece")

    def __init__(self, settings: WakeSettings):
        super().__init__(settings)
        self._spotter = None
        self._stream = None
        self.keywords: list[str] = []

    @staticmethod
    def model_dir(settings: WakeSettings) -> Path:
        return Path(settings.models_dir) / MODEL_NAME

    @classmethod
    def models_present(cls, settings: WakeSettings) -> bool:
        try:
            d = cls.model_dir(settings)
            return ((d / "tokens.txt").is_file() and (d / "bpe.model").is_file()
                    and all(_pick(d, p, False) for p in ("encoder", "decoder", "joiner")))
        except Exception:
            return False

    @classmethod
    def download(cls, settings: WakeSettings, log: Logger) -> None:
        from core.wake_engines._download import install_archive
        install_archive(MODEL_URL, Path(settings.models_dir), MODEL_NAME, log, keep=_keep)

    # ── Phrase -> keyword lines ──────────────────────────────────────────────

    def spoken_forms(self) -> list[str]:
        """The phrases as the English model should hear them, upper-case."""
        src = self.settings.spoken_as or self.settings.phrase
        forms = [to_latin(p).upper() for p in phrase_alternatives(src)]
        return [f for f in forms if f]

    def keyword_lines(self, encode, vocab: set[str]) -> list[str]:
        """One line per alternative: 'TOK TOK :boost #threshold @label'."""
        boost, thr = keyword_params(self.settings.sensitivity)
        lines = []
        for form in self.spoken_forms():
            toks = encode(form)
            missing = [t for t in toks if t not in vocab]
            if not toks or missing:
                raise WakeEngineError(
                    f"the wake model cannot spell '{form.lower()}' "
                    f"(unknown pieces: {' '.join(missing) or 'none'}). "
                    "Try writing how it sounds in English letters.")
            lines.append(f"{' '.join(toks)} :{boost} #{thr} @{form.replace(' ', '_')}")
        if not lines:
            raise WakeEngineError("the wake phrase is empty")
        return lines

    def load(self) -> None:
        d = self.model_dir(self.settings)
        try:
            import sentencepiece as spm
            import sherpa_onnx
        except Exception as e:
            raise WakeEngineError(f"sherpa-onnx is not installed — {e}") from e
        try:
            sp = spm.SentencePieceProcessor(model_file=str(d / "bpe.model"))
            vocab = {ln.split()[0] for ln in
                     (d / "tokens.txt").read_text(encoding="utf-8").splitlines() if ln.strip()}
        except Exception as e:
            raise WakeEngineError(f"the sherpa model is incomplete — {e}") from e
        self.keywords = self.keyword_lines(lambda s: sp.encode(s, out_type=str), vocab)

        # The spotter only takes keywords from a file. It lives next to the
        # model, is rewritten on every load and holds nothing private.
        kw_file = Path(self.settings.models_dir) / "sherpa_keywords.txt"
        kw_file.write_text("\n".join(self.keywords) + "\n", encoding="utf-8")
        try:
            self._spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(d / "tokens.txt"),
                encoder=str(_pick(d, "encoder", True)),
                decoder=str(_pick(d, "decoder", False)),
                joiner=str(_pick(d, "joiner", True)),
                keywords_file=str(kw_file),
                num_threads=1,
                sample_rate=SAMPLE_RATE,
                max_active_paths=4,
                num_trailing_blanks=1,
                provider="cpu",
            )
            self._stream = self._spotter.create_stream()
        except Exception as e:
            raise WakeEngineError(f"could not load the sherpa model — {e}") from e

    def process(self, block) -> Hit | None:
        x = np.asarray(block, dtype=np.int16).reshape(-1).astype(np.float32) / 32768.0
        self._stream.accept_waveform(SAMPLE_RATE, x)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            found = self._spotter.get_result(self._stream)
            if found:
                # Must be reset before more audio goes in, or the same keyword
                # is reported again on the next decode.
                self._spotter.reset_stream(self._stream)
                return Hit(str(found).replace("_", " ").title())
        return None

    def reset(self) -> None:
        if self._spotter is not None:
            self._stream = self._spotter.create_stream()

    def close(self) -> None:
        self._stream = None
        self._spotter = None

    @property
    def label(self) -> str:
        alts = phrase_alternatives(self.settings.phrase)
        return alts[0].title() if alts else self.settings.phrase
