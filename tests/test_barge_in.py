"""BargeIn on synthetic audio: our own echo must never interrupt, a second
voice over it must, and only after sustained evidence."""

import numpy as np
import pytest

from core.barge_in import BargeIn
from core.echo import EchoGuard

MIC_SR, OUT_SR, BLOCK = 16000, 24000, 1024
DT = BLOCK / MIC_SR                     # 64 ms per microphone block


def _level(x) -> float:
    """Same mapping as main._pcm_level."""
    rms = float(np.sqrt(np.mean(np.asarray(x, dtype=np.float32) ** 2)))
    return 0.0 if rms <= 60 else min(1.0, (rms - 60) / (2600 - 60))


def _voice(sr, n, t0, f0, formants, rms):
    """A harmonic 'voice' whose energy sits around the given formants."""
    t = t0 + np.arange(n) / sr
    sig = np.zeros(n)
    for k in range(1, int(7000 / f0)):
        w = sum(np.exp(-((k * f0 - f) / 180.0) ** 2) for f in formants)
        sig += w * np.sin(2 * np.pi * k * f0 * t)
    sig *= rms / (np.sqrt(np.mean(sig ** 2)) + 1e-9)
    return sig


def _assistant(sr, n, t0):
    return _voice(sr, n, t0, 120.0, (500, 900), 3000)


def _user(sr, n, t0):
    return _voice(sr, n, t0, 210.0, (2300, 3400), 2500)


class _Room:
    """Plays the assistant into an EchoGuard and produces microphone blocks."""

    def __init__(self, echo_gain=0.5, seed=0):
        self.guard = EchoGuard()
        self.barge = BargeIn(self.guard, MIC_SR, BLOCK)
        self.gain = echo_gain
        self.rng = np.random.default_rng(seed)
        self.t = 100.0

    def step(self, user=False, user_gain=1.0):
        out = _assistant(OUT_SR, int(DT * OUT_SR), self.t)
        self.guard.note_output(out.astype(np.int16), OUT_SR, _level(out), when=self.t)
        mic = self.gain * _assistant(MIC_SR, BLOCK, self.t)
        mic += self.rng.normal(0, 40, BLOCK)
        if user:
            mic += user_gain * _user(MIC_SR, BLOCK, self.t)
        # Shaped (frames, channels), exactly as sounddevice.InputStream delivers.
        mic = np.clip(mic, -32768, 32767).astype(np.int16).reshape(-1, 1)
        fired = self.barge.feed(mic, _level(mic), now=self.t + 0.03)
        self.t += DT
        return fired


def test_own_echo_never_interrupts():
    room = _Room()
    assert not any(room.step() for _ in range(150))       # ~10 s of speech


def test_voice_over_echo_interrupts_after_sustained_evidence():
    room = _Room()
    for _ in range(40):                                    # calibrate on echo
        assert not room.step()
    need = room.guard.required_blocks
    fired_at = next((i for i in range(40) if room.step(user=True)), None)
    assert fired_at is not None, "a distinct voice over the echo was never heard"
    assert fired_at >= need - 1, "fired before the guard's required evidence"


def test_short_noise_burst_does_not_interrupt():
    room = _Room()
    for _ in range(40):
        room.step()
    for _ in range(3):
        for _ in range(2):                                 # a 128 ms cough
            assert not room.step(user=True)
        for _ in range(6):
            assert not room.step()


def test_fires_once_and_hands_back_the_speech():
    room = _Room()
    for _ in range(40):
        room.step()
    fired = [room.step(user=True) for _ in range(30)]
    assert fired.count(True) == 1
    assert room.barge.triggered and not room.barge.handed_off
    audio = room.barge.take_audio()
    # Everything since the run began, plus the pre-roll, at 2 bytes a sample.
    blocks = len(audio) // (BLOCK * 2)
    assert blocks >= room.guard.required_blocks + 1
    assert room.barge.handed_off
    assert room.barge.take_audio() == b""                  # only once
    assert not room.step(user=True)                        # done until reset


def test_cooldown_survives_reset():
    room = _Room()
    for _ in range(40):
        room.step()
    assert any(room.step(user=True) for _ in range(30))
    room.barge.take_audio()
    room.barge.reset()                                     # next answer starts
    # Still talking over it within the cooldown: no second interruption.
    assert not any(room.step(user=True) for _ in range(20))   # ~1.3 s
    room.barge.reset()
    for _ in range(15):                                    # ~1 s of echo
        room.step()
    assert any(room.step(user=True) for _ in range(30))


def test_band_energies_accepts_microphone_shaped_blocks():
    from core.echo import band_energies
    x = _user(MIC_SR, BLOCK, 0.0).astype(np.int16)
    np.testing.assert_allclose(band_energies(x.reshape(-1, 1), MIC_SR),
                               band_energies(x, MIC_SR))


def test_guard_error_means_no_interrupt():
    class Broken:
        required_blocks = 1

        def is_user_speech(self, *a, **k):
            raise RuntimeError("boom")

    b = BargeIn(Broken(), MIC_SR, BLOCK)
    block = np.zeros(BLOCK, dtype=np.int16)
    assert not any(b.feed(block, 0.5, now=float(i)) for i in range(20))


@pytest.mark.parametrize("gain", [0.0, 0.2, 0.9])
def test_works_across_echo_levels(gain):
    """Headphones (no echo), a quiet laptop, loud desktop speakers."""
    room = _Room(echo_gain=gain)
    assert not any(room.step() for _ in range(60))
    assert any(room.step(user=True) for _ in range(40))
