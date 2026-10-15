# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-14_

## Current focus

- Compact mode merged into `main` and tried live on Windows: the card (drag, restore, hover buttons, menu), the tray icon, and the F9 quick prompt, which now wakes a sleeping assistant. The confirm gate is not on the card (the user did not want it); a gate brings the HUD back. Quitting is explicit after a run that ended silently in compact mode.
- In `main`: the HUD redesign (tried live), voice barge-in, monitor-under-cursor screenshots, `close_app` by name.

## Recent decisions

- Compact mode: the window's ✕ keeps quitting (the user's choice); the card hides the HUD instead of minimising it, so the card and the tray icon are the ways back. Windows is the target platform — no macOS-specific code from the forks.
- Qt's quit-on-last-window-closed is off; the ✕, Quit (card/tray) and `shutdown_jarvis` are the only exits. The exact window whose closing ended that run was not identified — the `[UI] Quitting — …` line will show if anything else stops the loop.
- Typed text (HUD box or quick prompt) wakes a sleeping assistant; before, it was refused with a log note.
- The quick prompt uses `RegisterHotKey`, not polling: the key is swallowed from the app in front, and the press grants the foreground the popup needs.
- HUD redesign took pieces, not a whole fork: no fork had a redesign worth taking entire (spyduck007's particle core costs ~2,300 translucent draws a frame; senthuran-ukr, Wildanel321 and Diegodevcyber were heavy, broken or rebrands). The head stays the default style; the anime face and soft theme are opt-in. Layout changes that hid the activity log were not taken.
- The state while talking is "SPEAKING" (`MainWindow._apply_state`); renderers get the `speaking` flag rather than parsing the state.
- The misfires in the first real session log came from local barge-in, not the server: each on exactly the then-minimum 5 blocks (≈0.32 s), after ~8 s and ~12 s of the assistant talking — so the evidence length and the cost of a false trigger were the problem, not calibration time.
- Barge-in needs a settled room (64 echo blocks ≈ 4 s), a residual ≥ 0.20 per block, and ~0.64 s of voice-like blocks within a window that tolerates 3 gaps.
- A voice barge-in pauses and keeps the answer; words in the model's transcription confirm it, silence resumes it. The forwarded audio makes the server stop generating, so on resume the model is asked to finish (`_CONTINUE_TEXT`); live, this continued from the cut-off point.
- "Interrupt by name" uses the wake detector while speaking and ignores its own name via EchoGuard verdicts. The UI button cycles OFF → BY VOICE → BY NAME (config `voice_barge_in_enabled`, `voice_barge_in_mode`).
- Wake word: openWakeWord default; sherpa-onnx for typed phrases; Vosk for native Ukrainian; after sleeping the detector is reset and deaf for 3 s.
- Agents commit only on the user's explicit request in the current message (`AGENTS.md` → *Commits*).

## Next steps

- Investigate the frequent Live disconnects (`1008 The operation was aborted`, four in one session on 2026-10-15, mostly right after connecting while asleep): add timestamps, see what precedes them.
- Monitor choice for `screen_process` — deferred; the plan is in `progress.md` → Fork backlog.
- Try interrupt-by-name live, and each wake engine on a real microphone (a Ukrainian name on Vosk and on sherpa).
- Pick the next feature from the upstream "LVI+" roadmap (see `progress.md`).
