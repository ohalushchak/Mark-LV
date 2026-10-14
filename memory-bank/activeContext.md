# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-14_

## Current focus

- HUD redesign merged into `main` and tried live: orb and anime styles beside the head and reactor, a livelier head, a soft theme, per-OS font and TEXT SIZE, captions, and a desktop orb with modes off / while speaking / always.
- Earlier work, also in `main`: voice barge-in robust to background sounds (tried live), screenshots of the monitor under the cursor, `close_app` by name with verification.

## Recent decisions

- HUD redesign took pieces, not a whole fork: no fork had a redesign worth taking entire (spyduck007's particle core costs ~2,300 translucent draws a frame; senthuran-ukr, Wildanel321 and Diegodevcyber were heavy, broken or rebrands). The head stays the default style; the anime face and soft theme are opt-in. Layout changes that hid the activity log were not taken.
- The state while talking is "SPEAKING" (`MainWindow._apply_state`); renderers get the `speaking` flag rather than parsing the state.
- The misfires in the first real session log came from local barge-in, not the server: each on exactly the then-minimum 5 blocks (≈0.32 s), after ~8 s and ~12 s of the assistant talking — so the evidence length and the cost of a false trigger were the problem, not calibration time.
- Barge-in needs a settled room (64 echo blocks ≈ 4 s), a residual ≥ 0.20 per block, and ~0.64 s of voice-like blocks within a window that tolerates 3 gaps.
- A voice barge-in pauses and keeps the answer; words in the model's transcription confirm it, silence resumes it. The forwarded audio makes the server stop generating, so on resume the model is asked to finish (`_CONTINUE_TEXT`); live, this continued from the cut-off point.
- "Interrupt by name" uses the wake detector while speaking and ignores its own name via EchoGuard verdicts. The UI button cycles OFF → BY VOICE → BY NAME (config `voice_barge_in_enabled`, `voice_barge_in_mode`).
- Wake word: openWakeWord default; sherpa-onnx for typed phrases; Vosk for native Ukrainian; after sleeping the detector is reset and deaf for 3 s.
- Agents commit only on the user's explicit request in the current message (`AGENTS.md` → *Commits*).

## Next steps

- Monitor choice for `screen_process` — deferred; the plan is in `progress.md` → Fork backlog.
- Try interrupt-by-name live, and each wake engine on a real microphone (a Ukrainian name on Vosk and on sherpa).
- Pick the next feature from the upstream "LVI+" roadmap (see `progress.md`).
