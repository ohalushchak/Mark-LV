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


def test_voice_barge_in_interrupts_and_forwards_the_speech():
    j = _jarvis()
    j._is_speaking = True
    j._gen_active = True
    j._barge._triggered = True
    speech = np.arange(main.CHUNK_SIZE * 3, dtype=np.int16).tobytes()
    j._barge._buf = deque([speech])
    j._on_voice_barge_in()
    assert j._is_speaking is False and j._interrupted is True
    sent = b""
    while not j.out_queue.empty():
        sent += j.out_queue.get_nowait()["data"]
    assert sent == speech
    assert j._barge.handed_off


def test_voice_barge_in_too_late_does_nothing():
    j = _jarvis()
    j._is_speaking = False                   # the answer ended on its own
    j._barge._triggered = True
    j._barge._buf = deque([b"\1\0" * 100])
    j._on_voice_barge_in()
    assert j.out_queue.empty()
    assert j._play_gen == 0
