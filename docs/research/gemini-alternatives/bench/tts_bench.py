"""Piper (uk_UA voices in ./piper_voices) and tts_uk on CUDA: time and RTF per phrase. Usage: python tts_bench.py OUT_DIR"""
import time, json, sys, wave, os
import numpy as np, soundfile as sf
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
PHRASES = [
    "Відкрила Блокнот.",
    "Гучність тепер тридцять відсотків.",
    "Написала Андрію в Slack: привіт, як справи? Повідомлення ще не відправлене, сказати, щоб відправила?",
    "Claude Code закінчив завдання: додав три тести, усі пройшли.",
    "У Києві зараз плюс дванадцять, хмарно, увечері можливий дощ.",
    "Не знайшла кнопку Save у вікні Notepad, спробуй сказати точніше.",
]
res = {}
# ---- Piper
from piper import PiperVoice
from piper.config import SynthesisConfig
for name, spk in [("piper-ukrainian_tts-medium", None), ("piper-lada-x_low", None)]:
    model = f"piper_voices/uk_UA-{name.split('piper-')[1]}.onnx"
    t = time.monotonic(); voice = PiperVoice.load(model); load = time.monotonic() - t
    times = []
    for i, text in enumerate(PHRASES):
        t = time.monotonic(); first = None; chunks = []
        for ch in voice.synthesize(text):
            if first is None: first = time.monotonic() - t
            chunks.append(ch.audio_int16_array)
        total = time.monotonic() - t
        audio = np.concatenate(chunks); sr = voice.config.sample_rate
        sf.write(f"{OUT}/{name}_{i+1}.wav", audio, sr)
        times.append((first, total, len(audio) / sr))
    res[name] = (load, times)
for name,(load,times) in res.items():
    w=times[1:]; print(f'{name:28} load {load:5.1f}s | first-chunk (warm) {np.mean([x[0] for x in w])*1000:6.0f} ms | RTF {np.mean([x[1]/x[2] for x in w]):.3f}', flush=True)
res={}
# ---- tts_uk (RAD-TTS++ + Vocos), GPU
os.chdir(os.path.dirname(os.path.abspath(__file__)))
t = time.monotonic()
from tts_uk import inference as tu
load = time.monotonic() - t
import torch
for v in ["tetiana", "mykyta", "lada"]:
    times = []
    for i, text in enumerate(PHRASES):
        torch.cuda.synchronize(); t = time.monotonic()
        mels, wav, st = tu.synthesis(text.lower(), v, 1, False, 1.0, 0, 0, 0, 0, 0.8, 0.666, 1.0, 1.0)
        torch.cuda.synchronize(); total = time.monotonic() - t
        a = wav[0].float().cpu().numpy()
        sf.write(f"{OUT}/tts_uk-{v}_{i+1}.wav", a, 44100)
        times.append((total, total, len(a) / 44100))
    res[f"tts_uk-{v}"] = (load, times)
for name, (load, times) in res.items():
    warm = times[1:]
    print(f"{name:28} load {load:5.1f}s | first-chunk (warm) {np.mean([x[0] for x in warm])*1000:6.0f} ms | "
          f"RTF {np.mean([x[1]/x[2] for x in warm]):.3f} | cold first phrase {times[0][1]*1000:.0f} ms")
