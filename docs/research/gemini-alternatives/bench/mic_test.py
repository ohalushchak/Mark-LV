"""Real-voice STT check: read each command after Enter; Whisper turbo, Whisper + hotwords and
Parakeet transcribe it. Recordings go to %TEMP%/voicebench_mic (your voice: keep them out of the repo).
Known flaw (run 2): the energy end-of-speech below never fired in a real room, so every take ran to
15 s; segment with Silero VAD instead (see the README)."""
import os, sys, time, json, re, queue, tempfile
os.environ["PATH"] = os.pathsep.join([os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", d, "bin") for d in ("cublas", "cudnn")] + [os.environ["PATH"]])
import numpy as np, sounddevice as sd, soundfile as sf
PHRASES = ["Відкрий блокнот.",
           "Зроби гучність тридцять відсотків.",
           "Напиши Андрію Коваленку в слаку: привіт, як справи?",
           "Запусти Claude Code у проєкті Марк.",
           "Натисни кнопку Save у вікні Notepad.",
           "Яка погода в Києві завтра?",
           "Відправ це повідомлення.",
           "Знайди в телеграмі вчорашній файл від Тараса і відкрий його в Excel."]
HINT = "Slack, Claude Code, Codex, Notepad, Save, Telegram, Excel, Discord."
OUT = os.path.join(tempfile.gettempdir(), "voicebench_mic"); os.makedirs(OUT, exist_ok=True)
print("Loading models (≈30 s)…", flush=True)
from faster_whisper import WhisperModel
import onnx_asr
wh = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
pk = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")
wh.transcribe(np.zeros(16000, np.float32), language="uk"); pk.recognize(np.zeros(16000, np.float32), sample_rate=16000)
def calibrate():
    with sd.InputStream(samplerate=16000, channels=1, dtype="float32") as st:
        a, _ = st.read(16000)          # 1 s of the room, taken while nobody speaks
    return float(np.sqrt(np.mean(a ** 2)))
def record(thr):
    # Every block from the moment Enter is pressed is kept, so a phrase begun
    # at once is not clipped (run 1 lost its first 0.5 s to calibration).
    q = queue.Queue(); blocks = []; voiced = False; silent = 0.0; start = time.monotonic()
    with sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=1600, callback=lambda d, *_: q.put(d.copy())):
        while True:
            b = q.get(); blocks.append(b); lvl = np.sqrt(np.mean(b ** 2))
            if lvl > thr: voiced, silent = True, 0.0
            elif voiced: silent += 0.1
            if (voiced and silent >= 1.2) or time.monotonic() - start > 15: break
    return np.concatenate(blocks)[:, 0], silent
input("Помовчи 1 секунду після Enter — міряю тишу в кімнаті.")
noise = calibrate(); THR = max(noise * 3.0, 0.006)
print(f"  тиша {noise:.4f}, поріг {THR:.4f}")
results = []
for i, ref in enumerate(PHRASES, 1):
    input(f"\n[{i}/{len(PHRASES)}] Enter, потім скажи:  «{ref}»")
    print("  ● слухаю…", flush=True)
    a, _ = record(THR); sf.write(f"{OUT}/{i}.wav", a, 16000)
    row = {"ref": ref}
    for name, fn in [("turbo", lambda x: " ".join(s.text for s in wh.transcribe(x, language="uk", beam_size=1, condition_on_previous_text=False)[0])),
                     ("turbo+hint", lambda x: " ".join(s.text for s in wh.transcribe(x, language="uk", beam_size=1, hotwords=HINT, condition_on_previous_text=False)[0])),
                     ("parakeet", lambda x: pk.recognize(x, sample_rate=16000))]:
        t = time.monotonic(); txt = fn(a).strip(); row[name] = (txt, round(time.monotonic() - t, 3))
        print(f"  {name:10} {row[name][1]*1000:5.0f} ms  {txt}")
    results.append(row)
json.dump(results, open(os.path.join(OUT, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\nГотово, дякую! Результати збережено.")
