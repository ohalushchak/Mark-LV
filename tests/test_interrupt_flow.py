"""Interrupt bookkeeping in JarvisLive, without a UI, audio device or session."""

import asyncio
import threading
import time
from collections import deque
from types import SimpleNamespace

import numpy as np
import pytest

import main
from core.barge_in import BargeIn
from core.echo import EchoGuard
from core.viseme import VisemeStream


class _UI:
    muted = False

    def __init__(self):
        self.log = []

    def set_state(self, s):
        pass

    def write_log(self, s):
        self.log.append(s)


def _jarvis():
    j = main.JarvisLive.__new__(main.JarvisLive)
    j.ui = _UI()
    j._speaking_lock = threading.Lock()
    j._is_speaking = False
    j._interrupted = False
    j._gen_active = False
    j._tool_turn_open = False
    j._tool_responded = False
    j._play_gen = 0
    j._play_cursor = 0.0
    j._out_latency = 0.4
    j._tail_until = 0.0
    j._out_level = 0.0
    j._echo = EchoGuard()
    j._barge = BargeIn(j._echo, main.SEND_SAMPLE_RATE, main.CHUNK_SIZE)
    j._barge_enabled = True
    j._barge_mode = "voice"
    j._barge_hold = None
    j._barge_token = 0
    j._barge_since = 0.0
    j._barge_server_cut = False
    j._barge_gen_open = False
    j._barge_grace_until = 0.0
    j._continue_requested = False
    j.session = None
    j._pause_capture = False
    j._written_tail = bytearray()
    j._last_loud = 0.0
    j._wake_detector = None
    j._wake_ring = deque(maxlen=main._WAKE_RING_BLOCKS)
    j._mic_seq = 0
    j._wake_replay_from = None
    j._wake_label = "Hey Jarvis"
    j._loop = None
    j._tail_preroll = deque(maxlen=main._TAIL_PREROLL_BLOCKS)
    j._visemes = VisemeStream()
    j._turn_done_event = None
    j.audio_in_queue = asyncio.Queue()
    j.out_queue = asyncio.Queue(maxsize=200)
    return j


def test_interrupt_after_answer_finished_does_not_swallow_next_reply():
    """The server already sent turn_complete; only playback is left."""
    j = _jarvis()
    j._gen_active = False
    j.interrupt()
    assert j._interrupted is False


def test_interrupt_mid_generation_discards_the_rest():
    j = _jarvis()
    j._gen_active = True
    j.interrupt()
    assert j._interrupted is True


def test_interrupt_between_tool_call_and_its_continuation():
    j = _jarvis()
    j._gen_active = False          # a turn_complete came before the tool call
    j._tool_turn_open = True
    j.interrupt()
    assert j._interrupted is True


def test_interrupt_drains_queue_and_flags_device_flush():
    j = _jarvis()
    for _ in range(5):
        j.audio_in_queue.put_nowait(b"\0" * 2400)
    j._is_speaking = True
    j.interrupt()
    assert j.audio_in_queue.empty()
    assert j._play_gen == 1
    assert j._is_speaking is False


def test_button_interrupt_leaves_only_the_room_margin_as_tail():
    j = _jarvis()
    j._is_speaking = True
    j._play_cursor = time.time() + 0.4      # a full buffer still queued
    j.interrupt()
    left = j._tail_until - time.monotonic()
    assert 0.0 < left <= main._TAIL_MARGIN + 0.01


def test_voice_interrupt_holds_nothing_back():
    j = _jarvis()
    j._is_speaking = True
    j._tail_preroll.append(b"x")
    j.interrupt(by_voice=True)
    assert j._tail_until == 0.0
    assert not j._tail_preroll


def test_tail_follows_the_playback_cursor_not_the_whole_buffer():
    j = _jarvis()
    j._is_speaking = True
    j._play_cursor = time.time() + 0.1      # 100 ms actually left to play
    j.set_speaking(False)
    left = j._tail_until - time.monotonic()
    assert left == pytest.approx(0.1 + main._TAIL_MARGIN, abs=0.03)


def test_tail_never_exceeds_the_device_buffer():
    j = _jarvis()
    j._is_speaking = True
    j._play_cursor = time.time() + 5.0      # nonsense cursor
    j.set_speaking(False)
    left = j._tail_until - time.monotonic()
    assert left <= j._out_latency + main._TAIL_MARGIN + 0.01


def test_barge_state_resets_only_when_a_new_stretch_starts():
    j = _jarvis()
    j._barge._triggered = True
    j._barge._handed_off = True
    j.set_speaking(True)                     # False -> True: new stretch
    assert not j._barge.handed_off
    j._barge._triggered = True
    j.set_speaking(True)                     # every chunk calls this again
    assert j._barge.triggered


def _paused(j, queued=(b"a", b"b")):
    j._is_speaking = True
    j._gen_active = True
    for c in queued:
        j.audio_in_queue.put_nowait(c)
    j._barge._triggered = True
    speech = np.arange(main.CHUNK_SIZE * 3, dtype=np.int16).tobytes()
    j._barge._buf = deque([speech])
    j._on_voice_barge_in()
    return speech


def test_voice_barge_in_pauses_keeps_the_answer_and_forwards_the_speech():
    j = _jarvis()
    speech = _paused(j)
    assert j._is_speaking is False
    assert j._interrupted is False                   # nothing discarded yet
    assert j._barge_hold == [b"a", b"b"] and j.audio_in_queue.empty()
    assert j._pause_capture and j._play_gen == 1      # device buffer flushed
    sent = b""
    while not j.out_queue.empty():
        sent += j.out_queue.get_nowait()["data"]
    assert sent == speech
    assert j._barge.handed_off


def test_words_confirm_the_interruption():
    j = _jarvis()
    _paused(j)
    j._commit_barge_in("heard words")
    assert j._barge_hold is None and j.audio_in_queue.empty()
    assert j._interrupted is True                    # rest of the answer dropped


def test_no_words_resumes_where_it_stopped():
    j = _jarvis()
    _paused(j)
    j._barge_hold.insert(0, b"unplayed")             # what the device still held
    j.audio_in_queue.put_nowait(b"c")                # arrived during the pause
    j._barge_check(j._barge_token)
    assert j._barge_hold is None
    out = []
    while not j.audio_in_queue.empty():
        out.append(j.audio_in_queue.get_nowait())
    assert out == [b"unplayed", b"a", b"b", b"c"]
    assert j._barge._not_before > time.monotonic() + main._BARGE_FALSE_HOLD_S - 1


def test_resume_waits_while_the_mic_is_still_loud():
    j = _jarvis()
    calls = []
    j._loop = SimpleNamespace(call_later=lambda d, f, *a: calls.append(d))
    _paused(j)
    calls.clear()
    j._last_loud = time.monotonic()                  # still talking
    j._barge_check(j._barge_token)
    assert j._barge_hold is not None and calls == [0.25]
    j._barge_since -= main._BARGE_CONFIRM_MAX_S      # but not forever
    j._barge_check(j._barge_token)
    assert j._barge_hold is None


def test_a_stale_timer_does_nothing():
    j = _jarvis()
    _paused(j)
    old = j._barge_token
    j._commit_barge_in("words")
    _paused(j)
    j._barge_check(old)
    assert j._barge_hold is not None


def test_button_interrupt_during_a_pause_drops_the_held_answer():
    j = _jarvis()
    _paused(j)
    j.interrupt()
    assert j._barge_hold is None and not j._pause_capture


@pytest.mark.parametrize("text,words", [
    ("стоп", True), ("wait", True), ("<noise>", False), ("[музика]", False),
    ("...", False), ("a", False),
])
def test_what_counts_as_words(text, words):
    assert main._has_words(text) is words


class _Det:
    def __init__(self):
        self.fed, self.armed = [], 0

    def feed(self, block, seq=None):
        self.fed.append(seq)

    def arm(self, quiet_s=0.0):
        self.armed += 1


def _name_mode(j, mine):
    j._barge_mode = "name"
    j._wake_detector = _Det()
    j._is_speaking = True
    j._echo.is_user_speech = lambda *a, **k: mine
    for _ in range(20):
        j._mic_name_watch(np.zeros((main.CHUNK_SIZE, 1), dtype=np.int16))


def test_name_interrupts_when_the_user_said_it():
    from core.wake_word import Detection
    j = _jarvis()
    _name_mode(j, mine=True)
    j._name_barge_in(Detection("Hey Jarvis", 0.9, "openwakeword", end_seq=15))
    assert j._is_speaking is False
    assert j._wake_replay_from is not None and j._wake_replay_from <= 16


def test_name_in_our_own_speech_is_ignored():
    from core.wake_word import Detection
    j = _jarvis()
    _name_mode(j, mine=False)                        # every block was our echo
    j._name_barge_in(Detection("Hey Jarvis", 0.9, "openwakeword", end_seq=15))
    assert j._is_speaking is True


def test_name_mode_rearms_the_detector_for_each_answer():
    j = _jarvis()
    j._barge_mode = "name"
    j._wake_detector = _Det()
    j.set_speaking(True)
    assert j._wake_detector.armed == 1


def test_voice_barge_in_too_late_does_nothing():
    j = _jarvis()
    j._is_speaking = False                   # the answer ended on its own
    j._barge._triggered = True
    j._barge._buf = deque([b"\1\0" * 100])
    j._on_voice_barge_in()
    assert j.out_queue.empty()
    assert j._play_gen == 0
