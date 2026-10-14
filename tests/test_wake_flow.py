"""Wake-word state in JarvisLive: what reaches the model on waking, when it
falls asleep again, and the sleep tool — without a UI, audio device or session."""

import asyncio
import threading
import time
from collections import deque
from types import SimpleNamespace

import numpy as np

import main
from core.wake_word import Detection


class _UI:
    muted = False

    def __init__(self):
        self.log = []
        self.states = []
        self.chimes = 0

    def set_state(self, s):
        self.states.append(s)

    def write_log(self, s):
        self.log.append(s)

    def chime(self):
        self.chimes += 1


class _Det:
    ready = True
    error = ""

    def __init__(self):
        self.fed = []
        self.armed = []

    def feed(self, block, seq=None):
        self.fed.append(seq)

    def arm(self, quiet_s=0.0):
        self.armed.append(quiet_s)


def _jarvis(**cfg):
    j = main.JarvisLive.__new__(main.JarvisLive)
    j.ui = _UI()
    j._loop = None
    j._speaking_lock = threading.Lock()
    j._is_speaking = False
    j._gen_active = False
    j._tool_turn_open = False
    j._tool_responded = False
    j._vision_busy = False
    j._ptt_enabled = False
    j._ptt_held = False
    j._out_level = 0.0
    j._play_cursor = 0.0
    j._out_latency = 0.2
    j._tail_until = 0.0
    j._barge_enabled = False
    j._wake_enabled = True
    j._awake = False
    j._wake_detector = _Det()
    j._wake_cfg = {"follow_up": False, "follow_up_seconds": 10,
                   "sleep_timeout_seconds": 120, "chime": True, **cfg}
    j._wake_label = "Hey Marko"
    j._wake_ring = deque(maxlen=main._WAKE_RING_BLOCKS)
    j._mic_seq = 0
    j._wake_replay_from = None
    now = time.monotonic()
    j._woke_at = j._last_user_speech = now
    j._answer_ended_at = 0.0
    j._sleep_requested = 0.0
    j.audio_in_queue = asyncio.Queue()
    return j


def _blocks(j, n):
    for i in range(n):
        j._mic_asleep(np.full((main.CHUNK_SIZE, 1), i, dtype=np.int16))


def test_asleep_blocks_are_numbered_kept_and_heard():
    j = _jarvis()
    _blocks(j, 5)
    assert [s for s, _ in j._wake_ring] == [1, 2, 3, 4, 5]
    assert j._wake_detector.fed == [1, 2, 3, 4, 5]


def test_voice_wake_hands_over_what_followed_the_phrase():
    j = _jarvis()
    _blocks(j, 30)
    j._wake_by_voice(Detection("Hey Marko", 0.9, "sherpa", end_seq=20))
    assert j._awake and j.ui.chimes == 1
    back = round(main._WAKE_REPLAY_BACK_S * main.SEND_SAMPLE_RATE / main.CHUNK_SIZE)
    replay = j._take_wake_replay()
    first = np.frombuffer(replay[0], dtype=np.int16)[0]
    assert len(replay) == 30 - (20 - back)          # blocks 21-back … 30
    assert first == 20 - back                       # block seq s holds value s-1
    assert not j._wake_ring and j._wake_replay_from is None
    assert j._take_wake_replay() == []              # only once


def test_button_wake_replays_nothing_and_empties_the_ring():
    j = _jarvis()
    _blocks(j, 10)
    j.wake(reason="you tapped wake")
    assert j._take_wake_replay() == []
    assert not j._wake_ring


def test_chime_can_be_turned_off():
    j = _jarvis(chime=False)
    j._wake_by_voice(Detection("Hey Marko", 0.9, "sherpa", end_seq=None))
    assert j._awake and j.ui.chimes == 0


def test_a_late_detection_after_waking_another_way_is_ignored():
    j = _jarvis()
    j.wake(reason="push-to-talk")
    j._wake_by_voice(Detection("Hey Marko", 0.9, "sherpa", end_seq=3))
    assert j._wake_replay_from is None and j.ui.chimes == 0


def test_sleep_rearms_the_detector_with_a_quiet_period_and_names_the_phrase():
    j = _jarvis()
    j._awake = True
    j.sleep(reason="timeout")
    assert j._wake_detector.armed == [main._WAKE_QUIET_S]
    assert "Hey Marko" in j.ui.log[-1]
    assert j.ui.states[-1] == "SLEEPING"


def test_idle_timeout_counts_from_the_last_thing_that_happened():
    j = _jarvis(sleep_timeout_seconds=120)
    j._awake = True
    t0 = j._woke_at
    assert j._sleep_due(t0 + 100) is None
    j._answer_ended_at = t0 + 100                    # an answer just finished
    assert j._sleep_due(t0 + 200) is None
    assert j._sleep_due(t0 + 221) == "no speech for 2 min"


def test_follow_up_mode_sleeps_a_few_seconds_after_the_answer():
    j = _jarvis(follow_up=True, follow_up_seconds=10)
    j._awake = True
    t0 = j._woke_at
    j._answer_ended_at = t0 + 5
    assert j._sleep_due(t0 + 14) is None
    assert j._sleep_due(t0 + 16) == "no follow-up for 10 s"


def test_never_sleeps_mid_answer_or_mid_tool():
    j = _jarvis(follow_up=True, follow_up_seconds=4)
    j._awake = True
    late = j._woke_at + 60
    j._is_speaking = True
    assert j._sleep_due(late) is None
    j._is_speaking = False
    j._tool_turn_open = True
    assert j._sleep_due(late) is None
    j._tool_turn_open = False
    j.audio_in_queue.put_nowait(b"\0" * 10)          # audio still to play
    assert j._sleep_due(late) is None


def test_sleep_request_waits_for_the_goodbye_to_finish():
    j = _jarvis()
    j._awake = True
    j._sleep_requested = time.monotonic()
    j._tool_turn_open = True                         # tool turn not closed yet
    j._on_answer_finished()
    assert j._awake
    j._tool_turn_open = False                        # goodbye played out
    j._on_answer_finished()
    assert not j._awake


def test_sleep_request_has_a_fallback_if_no_turn_end_arrives():
    j = _jarvis()
    j._awake = True
    j._sleep_requested = time.monotonic()
    assert j._sleep_due(j._sleep_requested + 5) is None
    assert j._sleep_due(j._sleep_requested + main._SLEEP_REQUEST_MAX_S + 1) == "you asked me to"


def _call_tool(j, name):
    j._action_registry = SimpleNamespace(has=lambda n: False, scheduling=lambda n: None)
    j._plugin_registry = SimpleNamespace(has=lambda n: False, scheduling=lambda n: None)
    fc = SimpleNamespace(name=name, args={}, id="1")
    return asyncio.run(j._execute_tool(fc))


def test_sleep_tool_schedules_sleep_after_the_reply():
    j = _jarvis()
    j._awake = True
    r = _call_tool(j, "sleep_assistant")
    assert j._awake and j._sleep_requested
    assert "Hey Marko" in r.response["result"]


def test_sleep_tool_without_wake_word_explains_instead():
    j = _jarvis()
    j._wake_enabled = False
    j._awake = True
    r = _call_tool(j, "sleep_assistant")
    assert not j._sleep_requested
    assert "turned off" in r.response["result"]


def test_sleep_tool_is_declared_once():
    names = [t["name"] for t in main.TOOL_DECLARATIONS]
    assert names.count("sleep_assistant") == 1
