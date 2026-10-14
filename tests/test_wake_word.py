"""Wake word: settings, phrase handling, the shared detector and each engine's
logic — with fake models, so nothing here needs an engine installed."""

import json
import threading
import time

import numpy as np
import pytest

from core.wake_engines import Hit, WakeEngine, WakeSettings, phrase_alternatives
from core.wake_engines.openwakeword_engine import CONSECUTIVE, FRAME, OpenWakeWordEngine
from core.wake_engines.sherpa_engine import SherpaEngine, keyword_params
from core.wake_engines.translit import to_latin
from core.wake_engines.vosk_engine import VoskEngine, match, min_confidence
from core.wake_word import WakeWordDetector
from memory import config_manager as cm

BLOCK = 1024


# ── Settings ─────────────────────────────────────────────────────────────────

@pytest.fixture
def config(tmp_path, monkeypatch):
    f = tmp_path / "api_keys.json"
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cm, "CONFIG_FILE", f)
    return f


def test_untouched_config_keeps_the_original_behaviour(config):
    s = cm.get_wake_settings()
    assert s["engine"] == "openwakeword" and s["oww_model"] == "hey_jarvis"
    assert s["sleep_timeout_seconds"] == 120 and not s["follow_up"]


def test_bad_values_fall_back_or_clamp(config):
    config.write_text(json.dumps({"wake_word": {
        "engine": "porcupine", "sensitivity": 7, "follow_up_seconds": 1,
        "sleep_timeout_seconds": "soon", "vosk_lang": "xx"}}), encoding="utf-8")
    s = cm.get_wake_settings()
    assert s["engine"] == "openwakeword"
    assert s["sensitivity"] == 1.0
    assert s["follow_up_seconds"] == 4
    assert s["sleep_timeout_seconds"] == 120
    assert s["vosk_lang"] == "uk"


def test_save_merges_and_keeps_the_rest_of_the_config(config):
    config.write_text(json.dumps({"gemini_api_key": "k" * 20}), encoding="utf-8")
    cm.save_wake_settings({"engine": "sherpa", "phrase": "hey marko", "junk": 1})
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["gemini_api_key"] == "k" * 20
    assert data["wake_word"] == {"engine": "sherpa", "phrase": "hey marko"}


def test_empty_phrase_follows_the_assistant_name(tmp_path):
    sherpa = WakeSettings.from_config({"engine": "sherpa"}, "Марко", tmp_path)
    vosk = WakeSettings.from_config({"engine": "vosk"}, "Марко", tmp_path)
    typed = WakeSettings.from_config({"engine": "sherpa", "phrase": "ok mark"}, "Марко", tmp_path)
    assert sherpa.phrase == "hey марко"
    assert vosk.phrase == "марко"
    assert typed.phrase == "ok mark"


# ── Phrase handling ──────────────────────────────────────────────────────────

def test_alternatives_are_split_and_squeezed():
    assert phrase_alternatives(" Hey  Marko, ok marko;HEY MARKO | ") == ["hey marko", "ok marko"]


@pytest.mark.parametrize("src,out", [
    ("Марко", "marko"), ("Джарвіс", "jarvis"), ("Хома", "homa"),
    ("Андрій", "andriy"), ("Юля", "yulya"), ("hey Jarvis", "hey jarvis"),
    ("О'Ліна!", "olina"),
])
def test_transliteration_spells_how_it_sounds(src, out):
    assert to_latin(src) == out


# ── The shared detector ──────────────────────────────────────────────────────

class _FakeEngine(WakeEngine):
    id = "fake"

    def __init__(self, fire_on=(), ago_s=0.0):
        super().__init__(WakeSettings())
        self.fire_on = set(fire_on)
        self.ago_s = ago_s
        self.seen = []
        self.resets = 0
        self.closed = False

    def load(self):
        pass

    def process(self, block):
        self.seen.append(int(block[0]))
        return Hit("hey fake", 0.9, self.ago_s) if int(block[0]) in self.fire_on else None

    def reset(self):
        self.resets += 1

    def close(self):
        self.closed = True


def _block(tag: int):
    b = np.zeros((BLOCK, 1), dtype=np.int16)    # microphone-shaped
    b[0, 0] = tag
    return b


def _run(det, tags, wait_for=None):
    got = []
    done = threading.Event()

    def on_detect(d):
        got.append(d)
        done.set()

    det._on_detect = on_detect
    assert det.start()
    for t in tags:
        det.feed(_block(t), seq=t)
        time.sleep(0.002)
    if wait_for:
        done.wait(2.0)
    time.sleep(0.05)
    det.stop()
    return got


def test_detection_carries_the_block_where_the_phrase_ended():
    eng = _FakeEngine(fire_on={7}, ago_s=0.0)
    got = _run(WakeWordDetector(None, engine=eng), range(1, 10), wait_for=True)
    assert len(got) == 1 and got[0].end_seq == 7 and got[0].label == "hey fake"


def test_late_decision_points_back_to_the_phrase():
    # Vosk-style: decided 0.7 s after the phrase ended ≈ 11 blocks back.
    eng = _FakeEngine(fire_on={30}, ago_s=0.7)
    got = _run(WakeWordDetector(None, engine=eng), range(1, 32), wait_for=True)
    assert got[0].end_seq == 30 - round(0.7 * 16000 / BLOCK)


def test_cooldown_swallows_a_second_report_of_the_same_utterance():
    eng = _FakeEngine(fire_on={3, 5})
    got = _run(WakeWordDetector(None, engine=eng, cooldown_s=2.0), range(1, 8), wait_for=True)
    assert len(got) == 1


def test_engine_is_reset_after_a_hit_and_closed_on_stop():
    eng = _FakeEngine(fire_on={2})
    _run(WakeWordDetector(None, engine=eng), range(1, 5), wait_for=True)
    assert eng.resets >= 1
    assert eng.closed


def test_arm_ignores_the_microphone_for_the_quiet_period_then_resets():
    eng = _FakeEngine()
    det = WakeWordDetector(lambda d: None, engine=eng)
    assert det.start()
    det.arm(quiet_s=0.2)
    det.feed(_block(1), seq=1)              # inside the quiet period: dropped
    time.sleep(0.25)
    det.feed(_block(2), seq=2)
    time.sleep(0.05)
    det.stop()
    assert eng.seen == [2]
    assert eng.resets == 1                  # reset ran on the worker, once


def test_a_full_queue_drops_the_oldest_audio():
    det = WakeWordDetector(lambda d: None, engine=_FakeEngine())
    det._running = True                     # no worker: let the queue fill
    for t in range(1, 60):
        det.feed(_block(t), seq=t)
    seqs = []
    while not det._queue.empty():
        seqs.append(det._queue.get_nowait()[0])
    assert seqs[-1] == 59 and seqs[0] == 59 - len(seqs) + 1


def test_a_failing_callback_does_not_kill_the_detector():
    eng = _FakeEngine(fire_on={2, 40})
    det = WakeWordDetector(None, engine=eng, cooldown_s=0.0)
    calls = []

    def boom(d):
        calls.append(d.end_seq)
        raise RuntimeError("ui gone")

    det._on_detect = boom
    det.start()
    for t in range(1, 45):
        det.feed(_block(t), seq=t)
        time.sleep(0.002)
    time.sleep(0.1)
    det.stop()
    assert calls == [2, 40]


def test_a_model_that_will_not_load_reports_and_stays_down():
    class Broken(_FakeEngine):
        def load(self):
            from core.wake_engines import WakeEngineError
            raise WakeEngineError("model missing")

    notes = []
    det = WakeWordDetector(lambda d: None, engine=Broken(), notify=notes.append,
                           logger=lambda m: None)
    assert not det.start()
    assert not det.ready and "model missing" in det.error and notes


# ── openWakeWord ─────────────────────────────────────────────────────────────

class _FakeOwwModel:
    def __init__(self, scores):
        self.models = {"hey_jarvis_v0.1": object()}
        self.scores = list(scores)
        self.frames = []
        self.resets = 0

    def predict(self, frame):
        self.frames.append(len(frame))
        return {"hey_jarvis_v0.1": self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


def _oww(scores, sensitivity=0.5):
    e = OpenWakeWordEngine(WakeSettings(sensitivity=sensitivity))
    e._model = _FakeOwwModel(scores)
    e._key = "hey_jarvis_v0.1"
    return e


def test_oww_scores_whole_80ms_frames_from_64ms_blocks():
    e = _oww([0.0] * 10)
    for _ in range(5):                      # 5 × 1024 = 4 × 1280
        e.process(np.zeros(BLOCK, dtype=np.int16))
    assert e._model.frames == [FRAME] * 4


def test_oww_needs_consecutive_frames_above_threshold():
    e = _oww([0.9, 0.9, 0.1, 0.9, 0.9, 0.9])
    hits = [e.process(np.zeros(FRAME, dtype=np.int16)) for _ in range(6)]
    fired = [i for i, h in enumerate(hits) if h]
    assert fired == [CONSECUTIVE + 2]       # the broken run did not count
    assert hits[fired[0]].label == "Hey Jarvis"


def test_oww_threshold_follows_sensitivity():
    assert _oww([], 0.0).threshold == pytest.approx(0.75)
    assert _oww([], 0.5).threshold == pytest.approx(0.5)
    assert _oww([], 1.0).threshold == pytest.approx(0.25)


def test_oww_reset_clears_the_model_and_the_run():
    e = _oww([0.9, 0.9])
    e.process(np.zeros(FRAME * 2, dtype=np.int16))
    e.process(np.zeros(300, dtype=np.int16))
    e.reset()
    assert e._model.resets == 1 and e._run == 0 and len(e._buf) == 0


def test_oww_custom_model_label_and_presence(tmp_path):
    f = tmp_path / "hey_marko_v0.2.onnx"
    s = WakeSettings(oww_model=str(f))
    assert OpenWakeWordEngine(s).label == "Hey Marko"
    assert not OpenWakeWordEngine.models_present(s)
    f.write_bytes(b"x")
    assert OpenWakeWordEngine.models_present(s)


# ── sherpa-onnx ──────────────────────────────────────────────────────────────

def _fake_bpe(text):
    """Stand-in for sentencepiece: one piece per word, '▁' in front."""
    return ["▁" + w for w in text.split()]


def test_sherpa_keyword_lines_from_a_cyrillic_phrase():
    e = SherpaEngine(WakeSettings(engine="sherpa", phrase="Hey Марко, марко"))
    lines = e.keyword_lines(_fake_bpe, {"▁HEY", "▁MARKO"})
    boost, thr = keyword_params(0.5)
    assert lines == [f"▁HEY ▁MARKO :{boost} #{thr} @HEY_MARKO",
                     f"▁MARKO :{boost} #{thr} @MARKO"]


def test_sherpa_spoken_as_overrides_the_transliteration():
    e = SherpaEngine(WakeSettings(engine="sherpa", phrase="Хома", spoken_as="homa"))
    assert e.spoken_forms() == ["HOMA"]


def test_sherpa_refuses_a_phrase_it_cannot_spell():
    from core.wake_engines import WakeEngineError
    e = SherpaEngine(WakeSettings(engine="sherpa", phrase="hey zork"))
    with pytest.raises(WakeEngineError, match="cannot spell"):
        e.keyword_lines(_fake_bpe, {"▁HEY"})


def test_sherpa_stricter_setting_raises_the_threshold():
    assert keyword_params(0.0)[1] > keyword_params(0.5)[1] > keyword_params(1.0)[1]


# ── Vosk ─────────────────────────────────────────────────────────────────────

def _w(word, end, conf=1.0):
    return {"word": word, "end": end, "start": end - 0.4, "conf": conf}


def test_vosk_matches_the_phrase_in_order_with_confidence():
    words = [_w("[unk]", 0.5), _w("привіт", 1.0), _w("марко", 1.4), _w("[unk]", 2.0)]
    assert match(words, [["привіт", "марко"]], 0.8)["word"] == "марко"
    assert match(words, [["марко", "привіт"]], 0.8) is None
    weak = [_w("марко", 1.0, conf=0.6)]
    assert match(weak, [["марко"]], 0.8) is None


def test_vosk_hit_reports_how_long_ago_the_phrase_ended():
    class Rec:
        def __init__(self):
            self.n = 0

        def AcceptWaveform(self, data):
            self.n += 1
            return self.n == 40              # utterance ends at block 40

        def Result(self):
            return json.dumps({"result": [_w("марко", 1.2), _w("[unk]", 2.4)]})

    e = VoskEngine(WakeSettings(engine="vosk", phrase="марко"))
    e._phrases = [["марко"]]
    e._rec = Rec()
    e._new_recognizer = lambda: None
    hits = [e.process(np.zeros(BLOCK, dtype=np.int16)) for _ in range(40)]
    hit = hits[-1]
    assert hit is not None
    assert hit.ago_s == pytest.approx(40 * BLOCK / 16000 - 1.2)


def test_vosk_confidence_follows_sensitivity():
    assert min_confidence(0.0) > min_confidence(0.5) > min_confidence(1.0)
