"""Whisper large-v3-turbo per-voice WER on the tts_bench.py samples (needs phrases.txt). Usage: python stt_bench.py SAMPLES_DIR"""
import time, glob, re, os, sys
import numpy as np, soundfile as sf, librosa
SAMPLES = sys.argv[1]
PHRASES = open("phrases.txt", encoding="utf-8").read().splitlines()
EQUIV = {"тридцять": "30", "відсотків": "%", "дванадцять": "12", "slack": "слак", "claude": "клод", "code": "код", "save": "сейв", "notepad": "нотпад"}
def norm(s):
    s = s.lower().replace("’", "'").replace("%", " % ")
    return [EQUIV.get(w, w) for w in re.sub(r"[^\w\s'%]", " ", s).split()]
def wer(ref, hyp):
    r, h = norm(ref), norm(hyp); d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j-1] + 1, prev + (r[i-1] != h[j-1])); prev, d[j] = d[j], cur
    return d[len(h)] / len(r)
files = sorted(glob.glob(f"{SAMPLES}/*.wav"))
audio = {f: (lambda a, sr: librosa.resample(a, orig_sr=sr, target_sr=16000))(*sf.read(f, dtype="float32")) for f in files}
src = lambda f: os.path.basename(f).rsplit("_", 1)[0]
ref_of = lambda f: PHRASES[int(re.search(r"_(\d+)\.wav$", f).group(1)) - 1]
def run(name, fn):
    fn(audio[files[0]]); per = {}
    for f in files: per.setdefault(src(f), []).append(wer(ref_of(f), fn(audio[f])))
    print(f"{name:40} " + "  ".join(f"{k.replace('piper-','p:').replace('tts_uk-','u:')[:16]} {np.mean(v)*100:4.0f}%" for k, v in per.items()), flush=True)
    return per
from faster_whisper import WhisperModel
m = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
tr = lambda a, **kw: " ".join(s.text for s in m.transcribe(a, language="uk", beam_size=1, condition_on_previous_text=False, **kw)[0])
run("turbo", tr)
HINT = "Slack, Claude Code, Codex, Notepad, Save, Telegram, Discord."
run("turbo + hotwords", lambda a: tr(a, hotwords=HINT))
run("turbo + beam 5 + hotwords", lambda a: " ".join(s.text for s in m.transcribe(a, language="uk", beam_size=5, hotwords=HINT, condition_on_previous_text=False)[0]))
f = [x for x in files if "tts_uk-mykyta_3" in x][0]
print("sample mykyta_3:", tr(audio[f], hotwords=HINT))
f = [x for x in files if "tts_uk-tetiana_6" in x][0]
print("sample tetiana_6:", tr(audio[f], hotwords=HINT))
