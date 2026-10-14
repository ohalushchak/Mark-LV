"""openWakeWord: a trained model per phrase, the cheapest and the most precise.

The pretrained "hey_jarvis" is the default. Any other phrase needs its own
model, trained with openWakeWord's synthetic-data notebook (Colab, about an
hour) — the result is an .onnx file the settings can point at.

Note on licences: the openWakeWord code is Apache-2.0, but its pretrained
models (hey_jarvis included) are CC BY-NC-SA 4.0 — non-commercial, which
matches this project's own licence.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import numpy as np

from core.wake_engines import Hit, Logger, SAMPLE_RATE, WakeEngine, WakeEngineError, WakeSettings

PRETRAINED = {
    "hey_jarvis":  "Hey Jarvis",
    "alexa":       "Alexa",
    "hey_mycroft": "Hey Mycroft",
    "hey_rhasspy": "Hey Rhasspy",
}

# openWakeWord scores audio in 80 ms frames. The microphone delivers 64 ms
# blocks, and feeding those directly made the model re-emit its previous score
# for some calls, so "N frames in a row" silently counted fewer real frames.
FRAME = 1280

# A single frame above the threshold fired on ordinary speech; the score has to
# hold for this many consecutive frames (240 ms). The real phrase easily does.
CONSECUTIVE = 3


def _resources_dir() -> Path | None:
    """openwakeword/resources/models without importing the package — the
    import drags onnxruntime in behind it and costs two seconds."""
    try:
        spec = importlib.util.find_spec("openwakeword")
        if spec is None or not spec.origin:
            return None
        return Path(spec.origin).resolve().parent / "resources" / "models"
    except Exception:
        return None


def threshold_for(sensitivity: float) -> float:
    """0 (strict) -> 0.75, 0.5 -> 0.5 (openWakeWord's own default), 1 -> 0.25."""
    return 0.75 - 0.5 * max(0.0, min(1.0, sensitivity))


class OpenWakeWordEngine(WakeEngine):
    id = "openwakeword"
    title = "openWakeWord — Hey Jarvis or a trained model"
    pip_packages = ("openwakeword",)
    import_names = ("openwakeword",)

    def __init__(self, settings: WakeSettings):
        super().__init__(settings)
        self._model = None
        self._key = ""
        self._buf = np.zeros(0, dtype=np.int16)
        self._run = 0
        self._best = 0.0
        self.threshold = threshold_for(settings.sensitivity)

    @staticmethod
    def _custom_path(settings: WakeSettings) -> Path | None:
        spec = settings.oww_model
        return None if spec in PRETRAINED else Path(spec)

    @classmethod
    def models_present(cls, settings: WakeSettings) -> bool:
        try:
            custom = cls._custom_path(settings)
            if custom is not None:
                return custom.is_file()
            d = _resources_dir()
            if d is None or not d.is_dir():
                return False
            return all(any(d.glob(f"{stem}*.onnx"))
                       for stem in (settings.oww_model, "melspectrogram", "embedding_model"))
        except Exception:
            return False

    @classmethod
    def download(cls, settings: WakeSettings, log: Logger) -> None:
        custom = cls._custom_path(settings)
        if custom is not None and not custom.is_file():
            raise WakeEngineError(f"model file not found: {custom}")
        try:
            import openwakeword.utils as u
            log("Wake word: downloading openWakeWord models…")
            # The feature models (melspectrogram, embedding) are fetched either
            # way; a custom model still needs them. An empty list would mean
            # "every pretrained model", so a custom model asks for a name that
            # matches none of them.
            u.download_models(["__features_only__"] if custom is not None
                              else [settings.oww_model])
        except WakeEngineError:
            raise
        except Exception as e:
            raise WakeEngineError(f"model download failed: {e}") from e

    def load(self) -> None:
        custom = self._custom_path(self.settings)
        if custom is not None and custom.suffix.lower() != ".onnx":
            raise WakeEngineError("custom openWakeWord models must be .onnx files")
        try:
            from openwakeword.model import Model
            spec = str(custom) if custom is not None else self.settings.oww_model
            self._model = Model(wakeword_models=[spec], inference_framework="onnx")
        except Exception as e:
            raise WakeEngineError(f"could not load the openWakeWord model — {e}") from e
        # Read the score of the model actually loaded. Matching any key that
        # contains "jarvis" broke the moment the model was anything else.
        keys = list(getattr(self._model, "models", {}) or {})
        self._key = keys[0] if keys else ""

    def process(self, block) -> Hit | None:
        self._buf = np.concatenate([self._buf, np.asarray(block, dtype=np.int16).reshape(-1)])
        while len(self._buf) >= FRAME:
            frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
            scores = self._model.predict(frame) or {}
            score = float(scores.get(self._key, 0.0)) if self._key else \
                max((float(v) for v in scores.values()), default=0.0)
            if score >= self.threshold:
                self._run += 1
                self._best = max(self._best, score)
                if self._run >= CONSECUTIVE:
                    hit = Hit(self.label, self._best, ago_s=len(self._buf) / SAMPLE_RATE)
                    self._run, self._best = 0, 0.0
                    return hit
            else:
                self._run, self._best = 0, 0.0
        return None

    def reset(self) -> None:
        # The model keeps ~1.3 s of audio features and recent scores. Without a
        # reset, the "hey jarvis" that just woke it is still in there the next
        # time it is put to sleep, and the first predictions wake it again.
        if self._model is not None:
            try:
                self._model.reset()
            except Exception:
                pass
        self._buf = np.zeros(0, dtype=np.int16)
        self._run, self._best = 0, 0.0

    def close(self) -> None:
        self._model = None

    @property
    def label(self) -> str:
        custom = self._custom_path(self.settings)
        if custom is None:
            return PRETRAINED[self.settings.oww_model]
        # "hey_marko_v0.2.onnx" -> "Hey Marko"
        stem = re.sub(r"_v\d+(\.\d+)*$", "", custom.stem)
        return stem.replace("_", " ").strip().title() or custom.stem
