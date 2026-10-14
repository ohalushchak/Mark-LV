# Tech context

Technologies, environment and constraints. Exact package versions live in `requirements.txt`; install and run commands live in `readme.md` → *Quick Start* and `AGENTS.md` → *Setup and tests*.

## Stack

- Python 3.11–3.13 (`setup.py` enforces the floor and warns above the ceiling).
- `google-genai`: Gemini Live API for the conversation; REST and throwaway Live sessions for one-shot calls.
- PyQt6 for the HUD, avatar (QPainter, software-rendered) and video (`QGraphicsVideoItem` + `yt-dlp`).
- `sounddevice` + `numpy` for audio I/O and all signal processing (echo guard, barge-in, visemes).
- Playwright for browser automation.
- Optional, installed from inside the app: one wake-word engine — `openwakeword` (onnxruntime), `sherpa-onnx` + `sentencepiece` (bundles its own onnxruntime), or `vosk`; models go to `config/wake_models/` (openWakeWord keeps its own inside the package). Loading openwakeword and sherpa-onnx in one process was tested on Windows and works. FastAPI/uvicorn for the phone dashboard.

## Environment facts

- Development machine: Windows 11. The app must still run on macOS and Linux; per-OS packages carry `sys_platform` markers.
- Mic input: 16 kHz, 1024-sample blocks (64 ms), delivered as `(frames, channels)` int16 arrays. Model output: 24 kHz PCM.
- Tests run with `pytest` and need no audio device, UI or API key. Expect a harmless `pynvml` FutureWarning from `ui.py` import.
- Global push-to-talk works only on Windows (`GetAsyncKeyState`); elsewhere it is window-scoped.
- The sherpa KWS model is English-only: Ukrainian names are transliterated (`core/wake_engines/translit.py`) or typed as "sounds like". The small Ukrainian Vosk model supports grammars, knows common names (марко, олена, богдан) but not "джарвіс".
- Picovoice Porcupine is not an option: its free tier ended on 2026-06-30.

## Constraints

- No GPU, no OpenGL, no new heavy dependencies for core features.
- Free-tier Gemini quotas run out: the text-model pool exhausts first, which is why the one-shot ladder leads with Live.
- Live models refuse `response_modalities=["TEXT"]` (error 1007); one-shot Live calls read the reply from `output_transcription`.
