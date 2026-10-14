# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-14_

## Current focus

- The wake word is rebuilt around pluggable engines (`core/wake_engines/`) with a settings panel (⚙ SETUP → WAKE WORD SETUP, config block `wake_word`); merged into `main`. First real use worked (openWakeWord "Hey Jarvis", `sleep_assistant`); other engines are tested only on Windows TTS speech.
- Voice barge-in fires on background sounds. A real session log showed both false interruptions early in the session, while the threshold still sat at its 0.15 floor, each on exactly the minimum 5-block (≈0.32 s) run; one discarded ≈14.5 s of a long answer. Later in the session the learned threshold rose to 0.27 and it stopped. Fix planned in a separate branch: longer calibration, more evidence, a pause-and-confirm interrupt that resumes on no words, and an "interrupt only by name" mode.

## Recent decisions

- Wake word, taken from the fork survey: openWakeWord stays the default ("Hey Jarvis"); sherpa-onnx gives a typed phrase without training (default "hey <assistant name>"); Vosk gives native Ukrainian words. Picovoice (no free tier), STT-text matching (CPU, false wakes) and prompt-only wake words were rejected.
- What follows the wake phrase is replayed to the model from a ring of numbered mic blocks; the prompt (`{wake_phrase}`) says a turn may begin with the phrase.
- After falling asleep the detector is reset and deaf for 3 s (own-voice false wakes); detections have a 2 s cooldown; openWakeWord needs 3 consecutive 80 ms frames.
- Detector models load on worker threads; wake-word mode stays asleep (mic closed) if loading fails.
- Barge-in builds on `EchoGuard`'s per-block judgement plus sustained evidence and a 2 s cooldown; interrupting aborts the output stream.
- Agents commit only on the user's explicit request in the current message (`AGENTS.md` → *Commits*).

## Next steps

- Make voice barge-in robust to background noise (see Current focus).
- Try each wake engine on a real microphone, including a Ukrainian name on Vosk and on sherpa; tune default sensitivities if needed.
- Pick the next feature from the upstream "LVI+" roadmap (see `progress.md`).
