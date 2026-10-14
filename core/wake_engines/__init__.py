"""
Wake-word engines behind one small interface.

core/wake_word.py owns everything that does not depend on the engine: the
queue the microphone pushes into, the worker thread, the cooldown, re-arming
after sleep. An engine only turns 16 kHz int16 blocks into "heard it" or not.

Three engines, because no single one covers every wish:

  openwakeword  The pretrained "Hey Jarvis" (or alexa / hey_mycroft /
                hey_rhasspy), or any custom .onnx model the user trained.
                Lowest CPU and the fewest false wakes, but a new phrase needs
                a trained model.
  sherpa        Open-vocabulary keyword spotting (k2-fsa sherpa-onnx): the
                phrase is typed, not trained. The model is English, so a
                Ukrainian name is spelled the way it sounds in English letters
                ("Марко" -> "MARKO"); `spoken_as` overrides the automatic
                transliteration.
  vosk          A small Kaldi recogniser restricted to the phrase. The only
                engine that knows Ukrainian words natively; heavier and it
                only decides at the end of an utterance.

Every engine imports its package lazily, inside load()/download(), so an
engine that is not chosen costs nothing — not even an import.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SAMPLE_RATE = 16000

Logger = Callable[[str], None]


class WakeEngineError(RuntimeError):
    """A failure the user can act on; the message is shown to them as is."""


@dataclass
class Hit:
    """One detection.

    `ago_s` says how long before the end of the block just processed the
    phrase ended. Streaming engines decide right after the phrase (≈0); Vosk
    decides only once the whole utterance is over, and the command spoken
    after the name is already behind that point. The detector turns this into
    a microphone block number so the right audio can be handed to the model.
    """
    label: str
    score: float = 1.0
    ago_s: float = 0.0


@dataclass(frozen=True)
class WakeSettings:
    engine: str = "openwakeword"
    phrase: str = "hey jarvis"      # resolved: never empty
    spoken_as: str = ""
    sensitivity: float = 0.5        # 0 = strict, 1 = eager
    oww_model: str = "hey_jarvis"
    vosk_lang: str = "uk"
    models_dir: Path = field(default_factory=lambda: Path("config") / "wake_models")

    @classmethod
    def from_config(cls, cfg: dict, assistant_name: str, models_dir: Path) -> "WakeSettings":
        """Build from config_manager.get_wake_settings(). An empty phrase is
        derived from the assistant's name, so renaming the assistant renames
        what it answers to."""
        engine = cfg.get("engine", "openwakeword")
        name = (assistant_name or "JARVIS").strip()
        phrase = (cfg.get("phrase") or "").strip()
        if not phrase:
            # Vosk listens for a word of the chosen language, so the bare name
            # is the natural default there; the English spotter does better
            # with two words, and "hey" is the one everybody already says.
            phrase = name.lower() if engine == "vosk" else f"hey {name.lower()}"
        return cls(
            engine=engine,
            phrase=phrase,
            spoken_as=(cfg.get("spoken_as") or "").strip(),
            sensitivity=float(cfg.get("sensitivity", 0.5)),
            oww_model=(cfg.get("oww_model") or "hey_jarvis").strip(),
            vosk_lang=cfg.get("vosk_lang", "uk"),
            models_dir=Path(models_dir),
        )


def phrase_alternatives(text: str) -> list[str]:
    """'hey marko, ok marko' -> ['hey marko', 'ok marko']. Commas, semicolons
    and bars separate alternatives; each one is lower-cased and squeezed."""
    out = []
    for part in re.split(r"[,;|]", text or ""):
        p = " ".join(part.lower().split())
        if p and p not in out:
            out.append(p)
    return out


class WakeEngine:
    """Base class. Subclasses fill in the class attributes and the methods."""

    id = ""
    title = ""
    pip_packages: tuple[str, ...] = ()
    import_names: tuple[str, ...] = ()

    def __init__(self, settings: WakeSettings):
        self.settings = settings

    # ── Installation (class-level: no instance, no model load) ──────────────

    @classmethod
    def is_installed(cls) -> bool:
        try:
            return all(importlib.util.find_spec(m) is not None for m in cls.import_names)
        except Exception:
            return False

    @classmethod
    def models_present(cls, settings: WakeSettings) -> bool:
        """Cheap file-existence check. Never loads a model, never raises."""
        return True

    @classmethod
    def is_ready(cls, settings: WakeSettings) -> bool:
        return cls.is_installed() and cls.models_present(settings)

    @classmethod
    def download(cls, settings: WakeSettings, log: Logger) -> None:
        """Fetch whatever models_present() looks for. Raises WakeEngineError."""

    # ── Runtime (worker thread only) ─────────────────────────────────────────

    def load(self) -> None:
        """Load the model. Raises WakeEngineError with a user-facing message."""
        raise NotImplementedError

    def process(self, block) -> Hit | None:
        """Feed one 1-D int16 block; return a Hit when the phrase was heard."""
        raise NotImplementedError

    def reset(self) -> None:
        """Forget all audio heard so far, so nothing from before can fire."""

    def close(self) -> None:
        pass

    @property
    def label(self) -> str:
        """What the user has to say, for the log and the HUD."""
        return self.settings.phrase


def engine_class(engine_id: str) -> type[WakeEngine]:
    from core.wake_engines.openwakeword_engine import OpenWakeWordEngine
    from core.wake_engines.sherpa_engine import SherpaEngine
    from core.wake_engines.vosk_engine import VoskEngine
    return {
        OpenWakeWordEngine.id: OpenWakeWordEngine,
        SherpaEngine.id: SherpaEngine,
        VoskEngine.id: VoskEngine,
    }.get(engine_id, OpenWakeWordEngine)
