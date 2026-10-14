"""The pause-and-confirm barge-in against the real receive and playback loops,
with a fake session and a fake output device."""

import asyncio
import time
from types import SimpleNamespace

import numpy as np
import pytest

import main
from test_interrupt_flow import _jarvis


# ── receive loop ─────────────────────────────────────────────────────────────

def _msg(data=None, interrupted=False, heard=None, turn_complete=False):
    sc = None
    if interrupted or heard or turn_complete:
        sc = SimpleNamespace(
            interrupted=interrupted,
            output_transcription=None,
            input_transcription=SimpleNamespace(text=heard) if heard else None,
            turn_complete=turn_complete,
        )
    return SimpleNamespace(session_resumption_update=None, data=data,
                           server_content=sc, tool_call=None)


class _Session:
    def __init__(self, messages=()):
        self._messages = messages
        self._served = False
        self.sent = []

    async def send_client_content(self, turns=None, turn_complete=False):
        self.sent.append(turns["parts"][0]["text"])

    def receive(self):
        if self._served:
            raise ConnectionError("end of script")
        self._served = True

        async def gen():
            for m in self._messages:
                yield m
        return gen()


def _receive(j, messages):
    j.session = _Session(messages)
    j._loop = None
    j._session_log = []
    j._dashboard = None
    j._last_out_logged = ""
    j._asst_name = "JARVIS"
    j._vision_close_pending = False
    j._resume_handle = None
    with pytest.raises(ConnectionError):
        asyncio.run(j._receive_audio())


def _pause(j):
    j._is_speaking = True
    j._gen_active = True
    j.audio_in_queue.put_nowait(b"old-1")
    j._barge._triggered = True
    j._on_voice_barge_in()


def test_server_vad_alone_does_not_settle_the_pause():
    j = _jarvis()
    _pause(j)
    _receive(j, [_msg(interrupted=True)])
    assert j._barge_hold == [b"old-1"] and j._barge_server_cut


def test_a_new_reply_after_the_server_cut_confirms():
    j = _jarvis()
    _pause(j)
    _receive(j, [_msg(interrupted=True), _msg(data=b"\1\0" * 1200)])
    assert j._barge_hold is None
    q = []
    while not j.audio_in_queue.empty():
        q.append(j.audio_in_queue.get_nowait())
    assert b"old-1" not in q and q                 # only the new reply is left


def test_transcribed_words_confirm():
    j = _jarvis()
    _pause(j)
    _receive(j, [_msg(heard="зачекай")])
    assert j._barge_hold is None and j.audio_in_queue.empty()


def test_a_noise_tag_does_not_confirm():
    j = _jarvis()
    _pause(j)
    _receive(j, [_msg(heard="<noise>")])
    assert j._barge_hold is not None


def test_without_a_pause_server_interruption_still_interrupts():
    j = _jarvis()
    j._is_speaking = True
    j.audio_in_queue.put_nowait(b"x")
    _receive(j, [_msg(interrupted=True)])
    assert j.audio_in_queue.empty() and not j._is_speaking


# ── playback loop ────────────────────────────────────────────────────────────

class _Stream:
    latency = 0.2

    def __init__(self, *a, **k):
        self.written = []
        self.aborts = 0

    def start(self):
        pass

    def write(self, data):
        self.written.append(len(data))
        time.sleep(0.005)

    def abort(self):
        self.aborts += 1

    def stop(self):
        pass

    def close(self):
        pass


def test_pause_puts_back_what_the_device_had_not_played(monkeypatch):
    streams = []

    def make(*a, **k):
        streams.append(_Stream())
        return streams[-1]

    monkeypatch.setattr(main.sd, "RawOutputStream", make)
    monkeypatch.setattr(main, "get_output_device", lambda: "")
    j = _jarvis()
    j.ui.push_visemes = lambda *a, **k: None
    j.ui.set_audio_level = lambda *a, **k: None
    j._turn_done_event = asyncio.Event()

    chunk = (np.ones(1200, dtype=np.int16) * 1000).tobytes()   # 50 ms each

    async def scenario():
        for _ in range(4):                                     # 200 ms → one batch
            j.audio_in_queue.put_nowait(chunk)
        for _ in range(10):
            j.audio_in_queue.put_nowait(chunk)
        task = asyncio.create_task(j._play_audio())
        while not streams or not streams[0].written:
            await asyncio.sleep(0.001)
        j._barge._triggered = True
        j._on_voice_barge_in()                                  # pause right after a write
        for _ in range(50):
            await asyncio.sleep(0.005)
            if not j._pause_capture and streams[0].aborts:
                break
        held = list(j._barge_hold)
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        return held

    held = asyncio.run(scenario())
    assert not j._pause_capture and streams[0].aborts >= 1
    # The first held piece is the unplayed tail of what was written: between
    # nothing and the whole 200 ms batch, never more.
    tail = held[0]
    assert 0 < len(tail) <= 9600 and len(tail) % 2 == 0
    assert all(c == chunk for c in held[1:]) and len(held) >= 10


# ── finishing an answer the server cut off ──────────────────────────────────

def _resume_in_loop(j, server_cut):
    async def go():
        j._loop = asyncio.get_running_loop()
        j.session = _Session()
        _pause(j)
        j._barge_server_cut = server_cut
        j._barge_check(j._barge_token)
        await asyncio.sleep(0.01)                  # let the request go out
        return j.session.sent
    return asyncio.run(go())


def test_resume_after_a_server_cut_asks_the_model_to_finish():
    j = _jarvis()
    sent = _resume_in_loop(j, server_cut=True)
    assert sent == [main._CONTINUE_TEXT]
    out = []
    while not j.audio_in_queue.empty():
        out.append(j.audio_in_queue.get_nowait())
    assert out == [b"old-1"]                       # held audio still plays first


def test_resume_without_a_cut_asks_nothing():
    j = _jarvis()
    assert _resume_in_loop(j, server_cut=False) == []


def test_a_late_server_interruption_keeps_the_resumed_answer():
    j = _jarvis()
    _pause(j)
    j._barge_check(j._barge_token)                 # resumed; no session → no request
    j._continue_requested = False
    asked = []
    j._request_continuation = lambda: asked.append(1)
    _receive(j, [_msg(interrupted=True)])
    assert not j.audio_in_queue.empty()            # nothing discarded
    assert asked == [1]


def test_after_the_grace_window_server_interruption_interrupts_again():
    j = _jarvis()
    _pause(j)
    j._barge_check(j._barge_token)
    j._barge_grace_until = time.monotonic() - 1
    j._is_speaking = True
    _receive(j, [_msg(interrupted=True)])
    assert j.audio_in_queue.empty()
