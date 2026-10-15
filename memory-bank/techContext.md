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
- Tests run with `pytest` and need no audio device, window or API key; HUD tests draw widgets on Qt's `offscreen` platform (`tests/test_hud.py`). Offscreen Qt on Windows has no fonts (text renders as boxes), so text layout is checked by grabbing a never-shown `MainWindow` on the normal platform instead. Expect a harmless `pynvml` FutureWarning from `ui.py` import.
- Global push-to-talk works only on Windows (`GetAsyncKeyState`); elsewhere it is window-scoped.
- The sherpa KWS model is English-only: Ukrainian names are transliterated (`core/wake_engines/translit.py`) or typed as "sounds like". The small Ukrainian Vosk model supports grammars, knows common names (марко, олена, богдан) but not "джарвіс".
- Picovoice Porcupine is not an option: its free tier ended on 2026-06-30.
- Coding agents on this machine: Claude Code 2.1.300 (`~/.local/bin/claude`, Max plan) and Codex CLI 0.156 (npm, ChatGPT Plus sign-in). Claude Code takes `--permission-mode auto --permission-prompts none` and `--effort low…max`; its headless stream carries a `rate_limit_event` with the 5-hour and weekly utilization — there is no free way to read them otherwise. Codex's app-server answers `model/list` and `account/rateLimits/read` in ~0.3 s at no cost. `codex exec resume` accepts neither `--sandbox` nor `-C` (the sandbox goes in as `-c sandbox_mode=…`).
- Codex's own `config.toml` names `gpt-6.1-sol`, which the ChatGPT sign-in refuses ("not supported when using Codex with a ChatGPT account"); the agent passes the app-server's default model explicitly instead.
- Self-repair needs a git checkout and git on PATH; it is off in a frozen build (no repository, and `sys.executable -m pytest` would relaunch the app). Its worktree is made from HEAD, so the user's uncommitted edits are not in it. The full suite takes ~20 s, so one run (tests before, agent, tests after) costs ~40 s plus the agent's time.
- Windows' `time.monotonic()` ticks every ~16 ms: tests that order events through `core/activity.py` sleep ≥0.05 s between them.
- Codex's Windows sandbox refreshes its ACL setup before every command, and that refresh opens Codex's computer-use runtime (`%LOCALAPPDATA%\OpenAI\Codex\runtimes\cua_node\…\node_repl.exe`). While that runtime runs (the Codex app's computer use), the file is locked and every `codex exec` command fails with "helper_unknown_error: setup refresh had errors", in any folder; Codex still ends the turn normally. The log is `~/.codex/.sandbox/sandbox.<date>.log`. `core/agents.py` names this (`SANDBOX_HINT`) instead of reporting success.
- Codex's Windows sandbox writes files as a sandbox user. In a folder whose ACL grants only OWNER RIGHTS (Python's `tempfile.mkdtemp`), the user cannot read those files; normal project folders inherit usable ACLs.
- Both CLIs support the hook events `UserPromptSubmit`, `Stop` (with `last_assistant_message`) and `PermissionRequest`, in the same JSON format.
- PowerShell 7 is installed (`pwsh`); `run_command` uses it, falling back to Windows PowerShell.
- `time.monotonic()` on Windows ticks every ~16 ms: tests that order two events need a ≥50 ms gap.

## Constraints

- No GPU, no OpenGL, no new heavy dependencies for core features.
- Free-tier Gemini quotas run out: the text-model pool exhausts first, which is why the one-shot ladder leads with Live.
- Live models refuse `response_modalities=["TEXT"]` (error 1007); one-shot Live calls read the reply from `output_transcription`.
