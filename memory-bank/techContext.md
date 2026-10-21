# Tech context

Technologies, environment and constraints. Exact package versions live in `requirements.txt`; install and run commands live in `readme.md` → *Quick Start* and `AGENTS.md` → *Setup and tests*.

## Stack

- Python 3.11–3.13 (`setup.py` enforces the floor and warns above the ceiling).
- `google-genai`: Gemini Live API for the conversation; REST and throwaway Live sessions for one-shot calls.
- PyQt6 for the HUD, avatar (QPainter, software-rendered) and video (`QGraphicsVideoItem` + `yt-dlp`).
- `sounddevice` + `numpy` for audio I/O and all signal processing (echo guard, barge-in, visemes).
- Playwright for browser automation.
- `sqlite3` (stdlib; FTS5 is compiled into CPython's Windows build) and `fastembed` (ONNX Runtime, no torch) with `google/embeddinggemma-300m` for memory search. Without fastembed, or with search by meaning off, memory runs on full text only.
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
- Desktop automation facts measured on this machine (Windows 11, three monitors 2560×1600 / 3440×1440 primary / 1280×720, Ukrainian and English layouts):
  - pycaw 2025+ returns a wrapped device with `.EndpointVolume`; the old `Activate(...)` call raises. The volume slider's 0–100 is `GetMasterVolumeLevelScalar`.
  - pyautogui presses US-layout keys (`typewrite` under a Ukrainian layout turns "John" into "Ощрт") and builds its letter map with `VkKeyScan` at import. Unicode `SendInput` avoids the layout, but Windows 11 Notepad dropped and repeated its characters; a clipboard paste is exact.
  - A lone Alt tap (the usual SetForegroundWindow unlock) opens the menu bar of the window in front.
  - Windows Settings windows are ApplicationFrameHost windows titled in the system language ("Налаштування"); Store apps the same ("Калькулятор").
  - Win11 Notepad opens a file as a tab in an existing window and restores unsaved tabs between runs.
  - Chrome's address bar is the first UIA Edit of its window (8–30 ms, no focus change). Electron apps (Slack desktop exposes its UI; ChatGPT, Claude expose only caption buttons) vary.
  - Slack desktop: Ctrl+K toggles the jump box (a second Ctrl+K closes it); results are UIA ListItems ("social", "Jane Doe, Direct Message", "Search for: …"); the message box is an Edit named "Message to <name>" whose value keeps emoji as `:codes:`; the window title follows a switch about a second late.
  - Gemini vision on a 3440-px screen scaled to 1600 px: one look missed a 45-px taskbar button by ~38 px; a native-resolution crop corrects it. With thinking off the models answered from priors (a left-aligned taskbar) instead of looking.

- Memory search measured on this machine (2026-10-21, CPU): EmbeddingGemma loads in ~2 s once cached (~20 s cold), a query embeds in ~20 ms, a vault passage in ~120 ms — the user's vault (83 notes, 1,712 chunks) took 207 s to embed the first time. Compared on 31 Ukrainian/English questions: EmbeddingGemma 29/31 top-1, e5-large 28/31 (scores bunch at 0.77–0.83, no usable threshold), Qwen3-0.6B-Q 27/31 at 720 ms a query, multilingual MiniLM 28/31. Short facts score low in absolute terms (right answer 0.23–0.44, unrelated ≤ 0.17), hence floors of 0.20 / 0.30 and the category prefix in the embedded text.
- The user's Obsidian vault is found through `%APPDATA%\obsidian\obsidian.json`. Folder notes (`Project/Project.md`) are its convention; one project's source package nests copies of old documents three folders down.

## Constraints

- No GPU, no OpenGL, no new heavy dependencies for core features. The memory model (1.24 GB download, CPU) is the user's chosen exception (2026-10-21: "беремо ту модель, яка буде кращою"); it is optional and can be switched off on the MEMORY panel.
- Free-tier Gemini quotas run out: the text-model pool exhausts first, which is why the one-shot ladder leads with Live.
- Live models refuse `response_modalities=["TEXT"]` (error 1007); one-shot Live calls read the reply from `output_transcription`.
