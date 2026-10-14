# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-14_

## Current focus

- Voice barge-in robust to background sounds is merged into `main` and was tried live: false alarms pause and resume, and a server-cut answer is finished by the model without an audible seam.
- Next: screen capture picks the wrong monitor on multi-display setups (see `progress.md` → Known issues).

## Recent decisions

- The misfires in the first real session log came from local barge-in, not the server: each on exactly the then-minimum 5 blocks (≈0.32 s), after ~8 s and ~12 s of the assistant talking — so the evidence length and the cost of a false trigger were the problem, not calibration time.
- Barge-in needs a settled room (64 echo blocks ≈ 4 s), a residual ≥ 0.20 per block, and ~0.64 s of voice-like blocks within a window that tolerates 3 gaps.
- A voice barge-in pauses and keeps the answer; words in the model's transcription confirm it, silence resumes it. The forwarded audio makes the server stop generating, so on resume the model is asked to finish (`_CONTINUE_TEXT`); live, this continued from the cut-off point.
- "Interrupt by name" uses the wake detector while speaking and ignores its own name via EchoGuard verdicts. The UI button cycles OFF → BY VOICE → BY NAME (config `voice_barge_in_enabled`, `voice_barge_in_mode`).
- Wake word: openWakeWord default; sherpa-onnx for typed phrases; Vosk for native Ukrainian; after sleeping the detector is reset and deaf for 3 s.
- Agents commit only on the user's explicit request in the current message (`AGENTS.md` → *Commits*).

## Next steps

- Fix the multi-monitor screen capture.
- Try interrupt-by-name live, and each wake engine on a real microphone (a Ukrainian name on Vosk and on sherpa).
- Pick the next feature from the upstream "LVI+" roadmap (see `progress.md`).
