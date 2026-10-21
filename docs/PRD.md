# Product Requirements: JARVIS (MARK LIV/LV fork)

What this fork is meant to be and do, gathered from the agent sessions of 2026-10-14 → 2026-10-21. The reasons behind each design live in [ADR.md](ADR.md). The current state lives in `memory-bank/activeContext.md` and `memory-bank/progress.md`. Setup lives in `readme.md`.

_Last updated: 2026-10-21_

## 1. Overview

JARVIS is a desktop voice assistant. It hears, sees the screen, speaks through a holographic avatar, and drives the computer through tools: apps, windows, files, browser, messengers, git and the terminal. It can also hand coding work to Claude Code or Codex. The conversation runs on one Gemini Live session with native audio.

This repository is a personal fork of `FatihMakes/Mark-LV`. It is an experiment, not a work project: the owner is building an assistant to help with their own daily work. Features are ported from other forks, mixed and re-done (ADR-001).

## 2. User

- **One person** at their own computer, speaking Ukrainian and English, often in the same sentence.
- **Machine:** Windows 11, three monitors, Ukrainian and English keyboard layouts.
- **Accounts:** a Gemini key on the free tier, plus paid Claude Code and Codex subscriptions.
- **Daily tools:** Slack, Outlook, a browser, an IDE, Obsidian.

## 3. Goals

1. **Talk, don't click.** Common computer tasks get done by voice: opening and closing apps, settings, files, the browser, drafting messages, coding tasks.
2. **Trust what it says.** The assistant reports only what it checked, never acts on the user's accounts without a yes, and can undo what it did.
3. **Remember the user.** Facts, past conversations and the Obsidian notes are recalled by meaning in either language.
4. **Help with work.** It works with git and the terminal, hands larger coding work to Claude Code or Codex, and reports back by voice.
5. **Stay cheap and local.** No new subscription, no GPU required, and secrets and personal data stay on the machine.

## 4. Non-goals

- Several users, accounts or shared memory.
- Cloud sync of memory (TeddyDARKVADOR's OneDrive sync was rejected).
- Full conversation transcripts (ADR-023).
- macOS and Linux polish. Their code paths exist and must not break, but only Windows is tested live.
- Replacing Gemini Live right now (parked, ADR-017).

## 5. Experience principles

These come from `memory-bank/productContext.md`. They decide most trade-offs:

- **Latency is the product.** Slow tools announce themselves first.
- **Act, then offer undo.** Only irreversible actions ask, and through the HUD, which the model cannot forge.
- **It must not hear itself.**
- **Zero cost when off.**
- **Say it plainly** when something degrades.
- **Every token sent on connect is paid every session.**

## 6. Functional requirements

Status: ✅ built and tried live · 🟡 built, partly tried live · ⬜ planned.

### 6.1 Voice and presence

| ID | Requirement | Status |
|---|---|---|
| V-1 | Interrupt the assistant by voice while it speaks. A false trigger from noise pauses, then resumes on its own within about 1 s. An "interrupt by name" mode serves noisy rooms. Off by default (ADR-005). | ✅ |
| V-2 | Wake by a custom phrase (openWakeWord, sherpa-onnx or Vosk). The command said with the wake phrase is carried out. Sleep by voice. Optional follow-up window (ADR-006). | 🟡 "Hey Jarvis" live; the other engines tested on TTS speech only |
| V-3 | The HUD offers several avatar styles (reactor, head, wireframe orb, anime face), a soft theme, captions of the latest exchange, one text-size setting, and a desktop orb when the window is out of sight. | ✅ |
| V-4 | A compact always-on-top card, a tray icon and a global quick prompt (F9 / Ctrl+Alt+J). Typed text wakes the assistant. The window ✕ quits the app (ADR-009). | ✅ Windows |
| V-5 | Late results (background jobs, agent sessions) are spoken when the room is quiet, also while asleep (ADR-011). | ✅ |

### 6.2 Driving the computer

| ID | Requirement | Status |
|---|---|---|
| C-1 | Screen capture and screen finding work on a chosen monitor (under the cursor by default; primary, left, right or a number) (ADR-007). | ✅ |
| C-2 | `close_app` closes the named app, never the focused window, and reports admin rights or a save prompt instead of claiming success. Undo reopens it (ADR-008). | ✅ |
| C-3 | Every state-changing tool ends its result with `[VERIFIED]`, `[UNVERIFIED]` or `[FAILED]`, based on a before/after check (ADR-013). | ✅ |
| C-4 | Typing is exact under any keyboard layout (ADR-015). | ✅ |
| C-5 | Messages are drafted in the messenger and left unsent. Sending happens only after "send this message" and a yes by voice or HUD (ADR-014). The person is found through the user's contacts (C-9) and must be the one whose chat opened (ADR-025). | ✅ Slack, Telegram, Viber (by number, or shops and groups by name); Discord, Teams, WhatsApp untried |
| C-6 | `computer_use` runs a multi-step task with re-locating before every click. It pauses before risky buttons, and moving the mouse stops it. | 🟡 the risky-step pause not yet seen live |
| C-7 | Emptying the Recycle Bin is a dedicated action behind HUD CONFIRM (ADR-016). | 🟡 not yet seen live |
| C-8 | `screen_process` with `monitor='all'`: one image per monitor in the same turn. | ⬜ |
| C-9 | A contact list of the user's own (`config/contacts.json`, git-ignored): who a spoken name means, their name in each app and their phone number. Two people under one name is a question, never a guess; numbers reach the model as their last 4 digits; emergency numbers are never stored, written to or dialled. Changes by voice can be undone (ADR-025). | ✅ |
| C-10 | Calls in Telegram, Viber, Slack (huddle) and to phone numbers through Phone Link. Starting a call and accepting an incoming one happen only after the user's yes; declining and hanging up happen at once. While a call is on the assistant sleeps and announces nothing (ADR-026). | 🟡 Viber and Telegram live (dial, decline, accept, hang up); Slack, Phone Link and Telegram incoming untried |
| C-11 | Read what people wrote, from the messenger itself: Viber's open chat with each message marked theirs or the user's, Telegram's latest message per chat or its unread chats, without marking anything read (ADR-027). | ✅ Viber, Telegram |
| C-12 | New messages from the chats the user chose are announced by voice, read from Windows notifications (Slack; the phone's messengers through Phone Link) (ADR-027). | 🟡 built; not yet heard live |

### 6.3 Coding and other AIs

| ID | Requirement | Status |
|---|---|---|
| D-1 | Run terminal commands under a three-tier policy, with background jobs announced when done. | ✅ |
| D-2 | Git by voice. Commit, branch and switch can be undone; push and PR need CONFIRM; key files are never committed. | ✅ |
| D-3 | Project file edits by exact text, checked against what was read, with undo. | ✅ |
| D-4 | Delegate a task to Claude Code or Codex (ask → answer → run), with session resume. The HUD AGENT panel shows the engine, model, effort, usage limits and a job with STOP. A ring on the hologram shows who is working (ADR-010). | ✅ |
| D-5 | Self-check (status, error journal, tool health, probes, tests) and self-repair: an agent fixes JARVIS in a worktree, tests run before and after, the user reviews with APPLY / DISCARD, the change can be undone, and a restart watchdog rolls back a breaking change (ADR-012). | 🟡 live up to APPLY and restart; rollback not yet seen |

### 6.4 Memory

| ID | Requirement | Status |
|---|---|---|
| M-1 | Facts about the user are saved in their own words and language, with history. A wrong save can be reverted (ADR-021). | ✅ |
| M-2 | Recall works by meaning across languages: a Ukrainian question finds an English fact, and "children" finds both sons (ADR-019, ADR-020). | ✅ |
| M-3 | A fact the user did not actually say waits as a suggestion on the MEMORY panel (✓ / ✕) and stays out of the prompt (ADR-022). | ✅ |
| M-4 | "Forget X" deletes the facts about X, names them, and can be undone. | ✅ |
| M-5 | Past conversations are searchable as 2–4 sentence summaries with secrets redacted. The last one is mentioned once at the next start (ADR-023). | ✅ |
| M-6 | The Obsidian vault (auto-detected or chosen) is searched by meaning, read and opened. Note text is treated as data (ADR-024). | ✅ |
| M-7 | Notes can be edited by voice: add under a heading, create a note, remove an item, replace text. Every edit can be undone; vague note names are refused (ADR-024). | 🟡 `remove` built, its final form not yet seen live |
| M-8 | Search by meaning can be switched off on the MEMORY panel. Memory then works on full text only. | ✅ |

### 6.5 Upstream roadmap ("LVI+")

| ID | Requirement | Status |
|---|---|---|
| R-1 | Interrupt by voice | ✅ (V-1) |
| R-2 | Conversation history | ✅ as summaries (M-5) |
| R-3 | Obsidian integration | ✅ (M-6, M-7) |
| R-4 | Telegram remote | ⬜ |
| R-5 | Full file access | ⬜ |
| R-6 | Security camera | ⬜ |
| R-7 | Alternatives to Gemini Live: a Claude rung for one-shot calls, then local speech with Claude as the brain behind a switch (ADR-017) | ⬜ parked |

## 7. Non-functional requirements

| Area | Requirement |
|---|---|
| Latency | About 1 s from end of speech to first sound with Live. A memory query embeds in about 20 ms. Nothing slow runs on the asyncio or Qt loop. |
| Safety | Irreversible actions and sending messages need a yes the model cannot give itself (HUD token or a voice answer through the send gate). Reversible actions push an undo. |
| Privacy | Memory, the vault index, voice recordings and model caches stay local. Summaries are redacted. Note text is never treated as instructions. |
| Secrets | Key files are never read aloud, printed, committed, or opened by the coding tools. Before every commit, the staged files are checked for secrets and personal data (ADR-004). |
| Cost | No new subscription. The Gemini free tier plus the owner's existing Claude Code and Codex plans. No GPU required. |
| Footprint | Optional features import nothing until enabled. The 1.24 GB embedding model is the one accepted heavy dependency, and it can be switched off. |
| Platforms | Windows 11 is supported and tested live. macOS and Linux code paths branch on `platform.system()` and must keep working or explain themselves by voice. |
| Quality | `python -m pytest tests` passes headless (552 passed, 1 skipped on 2026-10-21). Every feature is tried live by voice before merge (ADR-003). |

## 8. Success measures

These are informal, because the product has one user:

- The owner uses it for real tasks without falling back to the mouse.
- No action on the owner's accounts happens without their yes (zero incidents since the send gate).
- Recall finds the right fact or note on the first try. The bench score was 29/31 top-1; live recall worked in both languages.
- A failure is reported as `[FAILED]` or `[UNVERIFIED]`, never as "Done".

## 9. Known problems and risks

The full list is in `memory-bank/progress.md` → *Known issues*. The ones that shape the roadmap:

- **Gemini quality:** wrong tool picks, a tool call repeated after an interruption (text typed twice in Slack), blind click chains.
- **Gemini quota:** vision calls fall to the Live rung and take 10–60 s.
- **Live disconnects (1008):** a GoAway is not answered, so a session drops after about 10 minutes and the answer in progress is lost.
- **Memory:** the first vault index takes minutes of CPU. Thresholds were calibrated on a few dozen phrases.
- **Windows only:** macOS and Linux are untried.

## 10. Open questions

- Should the Claude rung in `core/gemini.py` use the subscription first or only as a fallback? Its limits are shared with the owner's coding.
- Are the `tts_uk` voices good enough for the local voice mode, or should Gemini TTS be the fallback?
- How should a repeated tool call after an interruption be told apart from a real second request ("louder" said twice)?
- Upstream moved to `FatihMakes/Mark-LVI`. What is worth taking from it?
