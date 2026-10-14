# System patterns

How the running system fits together. The rules for writing code live in `AGENTS.md` → *Conventions*; the file map lives in `readme.md` → *Project Structure*. This file records the runtime flow neither of those shows.

## Process and threads

- `main.main()` runs the `JarvisUI` (PyQt6) event loop on the main thread and `asyncio.run(JarvisLive.run())` in a daemon thread, started after the UI has an API key.
- `run()` loops forever: build `LiveConnectConfig` → `client.aio.live.connect(...)` inside an `asyncio.TaskGroup` → on a `_ReconnectSignal` or a dropped connection, rebuild. Voice change, audio-device change and server errors all reconnect through `request_reconnect(keep_context=...)`.
- Session resumption: the server's resumption handle is kept in RAM only (`_resume_handle`), never on disk, so a fresh launch starts a new conversation and the end-of-session summary still runs.
- Per-session transient state (vision, interrupt flags, tool-turn flags) is reset at the top of each connection in `run()`; new per-turn state belongs in that reset block.

## Audio path

- `_listen_audio`: the microphone callback (sounddevice thread) computes a level and pushes blocks to `out_queue` only when the mic is open (awake, not muted, push-to-talk held if enabled, not speaking).
- While asleep (wake-word mode) nothing is streamed: `_mic_asleep` numbers each block, keeps the last ~6 s in `_wake_ring` and feeds `core.wake_word.WakeWordDetector`, which runs the chosen engine (`core/wake_engines/`: openWakeWord, sherpa-onnx or Vosk) on its own thread. A detection reports the block where the phrase ended; `_wake_by_voice` (on the loop via `call_soon_threadsafe`) wakes up, and the first awake block sends the ring from just before that point, so a command said in the same breath reaches the model whole.
- Falling asleep (`sleep()`) re-arms the detector: its model is reset and the mic is ignored for 3 s, because the assistant's own last words woke it. `_sleep_due` decides auto-sleep (idle timeout, or the short follow-up window) and never fires mid-answer or mid-tool; the `sleep_assistant` tool sleeps once the goodbye has played (`_on_answer_finished`).
- While the assistant speaks, mic blocks go to `core.barge_in.BargeIn` instead (when the opt-in "INTERRUPT BY VOICE" setting is on). `BargeIn` uses `core.echo.EchoGuard` per block and fires `_on_voice_barge_in` on the loop via `call_soon_threadsafe` after sustained evidence.
- `_receive_audio` reads server messages: audio → `audio_in_queue`, transcripts → log and `core.viseme`, tool calls → `_execute_tool`, `interrupted` → interrupt bookkeeping.
- `_play_audio` writes PCM to the output device, reports each block to `EchoGuard.note_output`, and drives the avatar's mouth from `_pcm_level` / `_pcm_visemes`.
- `interrupt()` is the single interrupt path (UI button, "stop", voice barge-in): it drops queued audio, aborts the output stream, and discards the rest of the answer in flight.

## Tool dispatch

`_execute_tool(fc)` routes by name: inline tools in `main.py` (`TOOL_DECLARATIONS`) → `ActionRegistry` (`actions/`) → `PluginRegistry` (`plugins/`). Action and plugin handlers run in the default executor, off the event loop. Results return as `FunctionResponse`; a tool's `scheduling` decides when a non-blocking result re-enters the conversation.

## One-shot model calls

`core/gemini.py` runs a ladder: a throwaway Live session first (separate quota pool), then pinned REST models, then rolling `-latest` aliases. Failures cool a model down (429 → 5 min, 503/504 → 30 min, 404 → 6 h). The conversation's own Live model has exactly two rungs and steps only on quota or loss of access.

## Persistence

- `config/api_keys.json` (via `memory/config_manager.py`): key, identity, voice, colour, feature toggles, plugin settings.
- `memory/long_term.json` (via `memory/memory_manager.py`): long-term memory, session summaries, monitored topics. Only a ~900-character core block rides in the system prompt; the rest is fetched on demand by the `recall_memory` tool.
