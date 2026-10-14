# Progress

What works, what is left, and known issues. Upstream releases up to Mark LV are described in `readme.md`; this file tracks the fork from there.

_Last updated: 2026-10-14_

## Done in this fork

- Screen capture takes the monitor under the mouse cursor, falling back to the primary (`_pick_monitor` in `actions/screen_processor.py`, tests in `tests/test_screen_capture.py`). It used to take `mss` monitor 1, which on a multi-monitor setup was a 1280×720 AV-receiver output instead of the 3440×1440 primary.
- Voice barge-in, opt-in (`core/barge_in.py`, tests in `tests/test_barge_in.py`, `tests/test_interrupt_flow.py`, `tests/test_barge_pause.py`): settled-room and residual requirements, ~0.64 s of evidence, pause-and-confirm with resume, interrupt-by-name mode.
- Wake word with a custom name: engines openWakeWord / sherpa-onnx / Vosk, settings panel, replay of the command said with the wake phrase, `sleep_assistant` voice tool, optional follow-up mode, chime, re-arm with model reset and 3 s quiet period, consecutive-frame check. Tests in `tests/test_wake_word.py`, `tests/test_wake_flow.py`.
- Interrupt bookkeeping fixes: no lost next reply, output buffer flushed on interrupt, `server_content.interrupted` handled, echo tail sized from the playback cursor with ~128 ms pre-roll.
- `core/echo.py` accepts `(frames, channels)` mic blocks (the 2-D shape used to raise inside the guard).
- `.gitignore` secret rules actually match.
- Removed unused legacy modules from an earlier Mark: `core/llm_client.py`, `core/stt.py`, `core/tts.py`, `core/installer.py`.

## Left to build

Upstream roadmap "LVI+" (interrupt by voice is done):

- [ ] Conversation history
- [ ] Telegram remote
- [ ] Full file access
- [ ] Security camera
- [ ] Obsidian integration

## Known issues

- `computer_settings` → `close_app` ignores its `value` (the app name) and presses Alt+F4 (Cmd+Q on macOS) in the focused window, then reports "Done" whatever happened. Seen live: asked to close Task Manager, it sent three Alt+F4s to the focused window; Task Manager runs elevated and ignores synthetic input from a normal process anyway.
- After a `screen_process` call the model often starts a short acknowledgement, and the image turn interrupts it (0.85–2.75 s of speech discarded per capture in one session).
- Voice barge-in: background speech (a television, people in the room) still gets transcribed as words and confirms a pause; interrupt-by-name is the answer for such rooms. A short "stop" (< 0.64 s) no longer interrupts by voice.
- Wake engines are verified only on Windows TTS speech, not a real microphone. Grammar-restricted Vosk can mistake similar words (an English test heard "market" as "marco"); two-word phrases are more reliable.
- Changing the wake phrase does not update the `{wake_phrase}` prompt line until the next reconnect.
