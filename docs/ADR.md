# Architecture Decision Records

The decisions behind this fork of MARK LIV/LV ("JARVIS"), gathered from the agent sessions of 2026-10-14 → 2026-10-21. Each record says what was decided, why, and what was weighed and left out. The product view lives in [PRD.md](PRD.md); how the code is built today lives in `memory-bank/systemPatterns.md`.

**Before changing a design covered here**, read its record. If the change reverses it, add a new record that supersedes the old one (mark the old one *Superseded by ADR-NNN*) instead of editing history.

Status values: **Accepted** (in the code), **Parked** (researched, not built), **Superseded**.

| # | Decision | Date | Status |
|---|---|---|---|
| [001](#adr-001-a-public-fork-that-mixes-features-from-other-forks) | A public fork that mixes features from other forks | 2026-10-14 | Accepted |
| [002](#adr-002-agent-context-in-agentsmd-and-memory-bank) | Agent context in `AGENTS.md` and `memory-bank/` | 2026-10-14 | Accepted |
| [003](#adr-003-feature-branches-live-testing-merge-commits) | Feature branches, live testing, merge commits | 2026-10-14 | Accepted |
| [004](#adr-004-secrets-and-personal-data-never-reach-the-public-fork) | Secrets and personal data never reach the public fork | 2026-10-14 | Accepted |
| [005](#adr-005-voice-barge-in-is-local-cautious-and-opt-in) | Voice barge-in is local, cautious and opt-in | 2026-10-14 | Accepted |
| [006](#adr-006-three-wake-word-engines-behind-one-interface) | Three wake-word engines behind one interface | 2026-10-14 | Accepted |
| [007](#adr-007-screen-capture-follows-the-cursor) | Screen capture follows the cursor | 2026-10-14 | Accepted |
| [008](#adr-008-close_app-closes-the-named-app-and-checks) | `close_app` closes the named app and checks | 2026-10-14 | Accepted |
| [009](#adr-009-compact-mode-for-windows-the-window-close-button-still-quits) | Compact mode for Windows; the window ✕ still quits | 2026-10-15 | Accepted |
| [010](#adr-010-coding-is-delegated-to-the-claude-code-and-codex-clis) | Coding is delegated to the Claude Code and Codex CLIs | 2026-10-15 | Accepted |
| [011](#adr-011-other-agent-sessions-are-announced-by-voice) | Other agent sessions are announced by voice | 2026-10-15 | Accepted |
| [012](#adr-012-self-repair-runs-in-a-git-worktree-behind-a-human-review) | Self-repair runs in a git worktree behind a human review | 2026-10-15 | Accepted |
| [013](#adr-013-every-state-changing-tool-reports-a-verified-verdict) | Every state-changing tool reports a verified verdict | 2026-10-16 | Accepted |
| [014](#adr-014-messages-are-drafted-sending-needs-the-users-yes) | Messages are drafted; sending needs the user's yes | 2026-10-16 | Accepted |
| [015](#adr-015-typing-goes-through-the-clipboard) | Typing goes through the clipboard | 2026-10-16 | Accepted |
| [016](#adr-016-irreversible-steps-are-dedicated-actions-behind-a-ui-gate) | Irreversible steps are dedicated actions behind a UI gate | 2026-10-16 | Accepted |
| [017](#adr-017-replacing-gemini-live-local-speech-and-claude-as-the-brain) | Replacing Gemini Live: local speech and Claude as the brain | 2026-10-16 | Parked |
| [018](#adr-018-memory-lives-in-one-sqlite-file-vectors-included) | Memory lives in one SQLite file, vectors included | 2026-10-21 | Accepted |
| [019](#adr-019-embeddinggemma-on-cpu-through-fastembed) | EmbeddingGemma on CPU through fastembed | 2026-10-21 | Accepted |
| [020](#adr-020-hybrid-search-full-text-plus-meaning-fused-by-rank) | Hybrid search: full text plus meaning, fused by rank | 2026-10-21 | Accepted |
| [021](#adr-021-facts-are-kept-in-the-users-words-with-history) | Facts are kept in the user's words, with history | 2026-10-21 | Accepted |
| [022](#adr-022-a-fact-the-user-did-not-say-waits-for-confirmation) | A fact the user did not say waits for confirmation | 2026-10-21 | Accepted |
| [023](#adr-023-conversations-are-kept-as-summaries-not-transcripts) | Conversations are kept as summaries, not transcripts | 2026-10-21 | Accepted |
| [024](#adr-024-the-obsidian-vault-is-indexed-read-and-edited-with-undo) | The Obsidian vault is indexed, read and edited with undo | 2026-10-21 | Accepted |
| [025](#adr-025-messengers-are-driven-by-what-each-app-shows-ui-automation) | Messengers are driven by what each app shows UI Automation | 2026-10-21 | Accepted |
| [026](#adr-026-calls-start-only-on-the-users-yes-and-are-seen-locally) | Calls start only on the user's yes and are seen locally | 2026-10-21 | Accepted |
| [027](#adr-027-messages-are-read-from-the-apps-and-from-windows-notifications) | Messages are read from the apps and from Windows notifications | 2026-10-21 | Accepted |

---

## ADR-001: A public fork that mixes features from other forks

- **Date:** 2026-10-14 · **Status:** Accepted

**Context.** Upstream `FatihMakes/Mark-LV` ships numbered "Marks". Of 725 forks, 92 had their own changes, many overlapping (several barge-in, HUD and memory variants). The owner wants an assistant for their own work, as a personal experiment.

**Decision.** Work in the fork `ohalushchak/Mark-LV` (`origin`; upstream stays `upstream`). For each feature, read the relevant forks' code, then build a mix: the best idea from each, re-done to fit this codebase. The proposal comes in numbered stages and the owner picks the stages.

**Consequences.**
- Every module docstring names the forks its ideas came from.
- A fork's code is read, never trusted from its commit message. Several overclaimed, and some had real bugs (a typo'd API, "any pixel change = success").
- Upstream design principles stay: no subscription required, no GPU, every OS supported (`memory-bank/projectbrief.md`).

**Rejected.** Merging a single fork wholesale. Pipecat and similar frameworks: they would replace the echo guard, wake word and barge-in path.

## ADR-002: Agent context in AGENTS.md and memory-bank

- **Date:** 2026-10-14 · **Status:** Accepted

**Context.** The work spans many agent sessions, and context ran out several times.

**Decision.**
- `AGENTS.md` holds the rules; `CLAUDE.md` imports it.
- `memory-bank/` holds the persistent context: brief, product, patterns, tech, active context, progress.
- Commit only when the owner's current message asks for it.
- Bring the memory bank up to date in the same commit.

**Consequences.** A new session starts from `activeContext.md` and `progress.md`. Each fact lives in one file. This ADR log and [PRD.md](PRD.md) record the *why* and the *what*, and point to the memory bank for the *now*.

## ADR-003: Feature branches, live testing, merge commits

- **Date:** 2026-10-14 · **Status:** Accepted

**Decision.**
1. Each feature gets a branch (`feature/<name>`, `fix/<name>`), with tests.
2. Rounds of live voice testing follow: the owner tests by voice, then the agent reads the console logs and fixes what was found.
3. Commit, push, then a `--no-ff` merge into `main`.
4. Merged branches are deleted locally and on GitHub. The last cleanup was 2026-10-21.

**Why.** Live testing found what unit tests could not:
- the 2-D mic block bug;
- layout-dependent typing;
- a silently broken volume API;
- Slack's jump box toggling;
- a lagging transcript in memory trust.

## ADR-004: Secrets and personal data never reach the public fork

- **Date:** 2026-10-14 · **Status:** Accepted

**Context.** The fork is public. The original `.gitignore` secret rules did not match at all, because git treats a trailing `# comment` as part of the pattern.

**Decision.**
- `.gitignore` comments go on their own lines.
- Key files, OAuth tokens, `memory/*.db`, `memory/long_term.json`, the embedding-model cache and voice recordings are ignored.
- Before every commit, check the staged files, and replace real names, work channels and vault paths in tests and docs with stand-ins.
- `config/api_keys.json` is never read aloud, printed or committed. The coding tools refuse to read or commit key files.

## ADR-005: Voice barge-in is local, cautious and opt-in

- **Date:** 2026-10-14 · **Status:** Accepted

**Context.** Interrupting the assistant by voice did not work. The cause was an upstream bug: 2-D mic blocks made the echo guard raise, and the error was swallowed. Once it worked, background noise interrupted too often.

**Decision.**
- Local detection in `core/barge_in.py`. It is mixed from:
  - bfrpaulondev's streak and cooldown;
  - Giljacob67's device flush with a generation counter;
  - syamsulmuiin's replay of the interrupting speech;
  - BlackStarG5's pre-roll;
  - Nathan123y's playback-cursor timing.
- It requires a settled room and about 0.64 s of voice evidence.
- When it fires, it pauses, confirms by transcribed words, then resumes automatically if no words came.
- An "interrupt by name" mode serves noisy rooms.
- It is off by default.

**Consequences.** A short "stop" under 0.64 s no longer interrupts by voice. A TV or people talking can still confirm a pause; interrupt-by-name is the answer there. Manual VAD needs Live's automatic VAD off (aonizai's lesson).

## ADR-006: Three wake-word engines behind one interface

- **Date:** 2026-10-14 · **Status:** Accepted

**Decision.** One interface with three engines, chosen and downloaded from a settings panel:
- openWakeWord ("Hey Jarvis" out of the box);
- sherpa-onnx (any English phrase; Ukrainian names are transliterated);
- Vosk (a Ukrainian or English grammar).

The command said together with the wake phrase is replayed to the model. There are a `sleep_assistant` tool, an optional follow-up window and a chime. Nothing is imported until enabled.

**Rejected.** Picovoice Porcupine: its free tier ended on 2026-06-30.

## ADR-007: Screen capture follows the cursor

- **Date:** 2026-10-14 · **Status:** Accepted

**Context.** Capture took `mss` monitor 1. On the owner's three-monitor setup, that was a 1280×720 AV-receiver output instead of the 3440×1440 primary.

**Decision.** Capture the monitor under the mouse cursor, falling back to the primary. Since ADR-013, `screen_process`, `screen_click` and `screen_find` take `monitor` (cursor / primary / left / right / number).

**Deferred.** `monitor='all'` (the owner: «Запиши це обмеження…»). When it is built, each monitor goes in as its own image, each compressed to 1280×720, never one stitched 7280-px frame.

## ADR-008: close_app closes the named app and checks

- **Date:** 2026-10-14 · **Status:** Accepted

**Context.** `close_app` pressed Alt+F4 in whatever window had focus.

**Decision.**
- Find the app by process (open_app aliases) or window title.
- Ask it to close like its ✕ would: WM_CLOSE, AppleScript quit, or `wmctrl -c`.
- Check afterwards. Report missing admin rights or a save prompt instead of "Done".
- Never close the assistant or the shell.
- Undo reopens the app.

## ADR-009: Compact mode for Windows; the window close button still quits

- **Date:** 2026-10-15 · **Status:** Accepted

**Decision.**
- An always-on-top compact card, a tray icon and a global quick prompt on F9 / Ctrl+Alt+J (through `RegisterHotKey`, no pynput).
- The window's ✕ keeps quitting the app. That is the owner's choice: «Хрестик вікна закриває програму, так як це є зараз».
- Built and tested for Windows only, the owner's platform. Elsewhere it falls back to plain Qt.
- The card's confirm gate was removed as unnecessary.
- Typed text wakes a sleeping assistant.
- Quitting is explicit and logged, because Qt quits silently when its last visible window closes.

## ADR-010: Coding is delegated to the Claude Code and Codex CLIs

- **Date:** 2026-10-15 · **Status:** Accepted

**Context.** The owner already pays for Claude Code and Codex. Gemini is weak at multi-step coding.

**Decision.**
- Coding tools:
  - `run_command`, with a three-tier policy and background jobs;
  - `git_project`, with push and PR behind CONFIRM and undo for commit, branch and switch;
  - `project_workspace`, with exact-text edits checked against a read hash and undo.
- `delegate_to_agent` runs Claude Code or Codex through the official CLIs behind an ask → answer → run gate, with session resume.
- An AGENT panel on the HUD shows two engine buttons, a model choice, an effort list that follows the engine, usage limits, and the job with STOP.
- A coloured ring with a timer on the hologram shows who is working: Claude orange, Codex violet, computer use pink.
- Usage limits are read from Claude Code's `rate_limit_event` and Codex's `account/rateLimits/read`.

**Rejected.** Bypassing the CLIs' permission systems (omygod9138's `bypassPermissions`). A model-written `confirmed=yes` parameter as a gate.

## ADR-011: Other agent sessions are announced by voice

- **Date:** 2026-10-15 · **Status:** Accepted

**Decision.**
- A hook installed into both CLIs (`UserPromptSubmit`, `Stop`, `PermissionRequest`) feeds `core/announce.py`.
- The announcer speaks when the room is quiet, also while asleep, instead of showing a tray notification. This is the owner's choice.
- It is opt-in.
- A project named in an announcement does not become the current project.

**Consequence.** While asleep, the mic stopping must be signalled with `audio_stream_end`, or Live drops the session with 1008.

## ADR-012: Self-repair runs in a git worktree behind a human review

- **Date:** 2026-10-15 · **Status:** Accepted

**Decision.** `self_improve` hands a fix to Claude Code or Codex inside a git worktree made from HEAD:
- Tests run before and after.
- A change check refuses the whole change if it touches one protected path.
- The diff is reviewed on the HUD with APPLY and DISCARD buttons only, so the model cannot apply it by voice.
- APPLY makes one fast-forward commit with undo.
- `core/restart_helper.py` rolls back a change that stops the next start.

The same work brought:
- honest results: arguments checked against the schema before anything runs;
- a redacted error journal and per-tool health;
- `self_check`.

**Mixed from.** BlackStarG5, klcenka9, syamsulmuiin, TeddyDARKVADOR, Robin.

## ADR-013: Every state-changing tool reports a verified verdict

- **Date:** 2026-10-16 · **Status:** Accepted

**Context.** The model reported success it never checked, and some forks counted any pixel change as success.

**Decision.**
- Tools declare `TOOL["snapshot"]` and `TOOL["verify"]`, run by the action loader.
- Results end with `[VERIFIED]`, `[UNVERIFIED]` or `[FAILED]`. No evidence means unverified.
- `computer_use` is a loop with re-locating before every click and a yes/no/unknown final check. Risky steps, judged by the real UIA label, pause for the user. Moving the mouse stops it.
- Finding goes by UIA name first, then vision with a second look at native resolution.

**Mixed from.** bfrpaulondev, syamsulmuiin, BlackStarG5, rilzisheree, JishnuDevTech.

**Rejected.**
- bfrpaulondev's provider router and OpenAI planner;
- a URL reader that steals focus and the clipboard;
- "any pixel change = success".

## ADR-014: Messages are drafted; sending needs the user's yes

- **Date:** 2026-10-16 · **Status:** Accepted

**Context.** A Slack message went to the wrong channel without confirmation.

**Decision.**
1. `send_message` opens the conversation, types the draft, and leaves it unsent.
2. Only "send this message" asks through `core/send_gate.py`. The user answers by voice or with the HUD CONFIRM button.
3. Enter in a chat box and Send buttons go through the same gate.
4. The confirmation question need not repeat the message text.

This is the owner's rule, word for word, in the build log.

## ADR-015: Typing goes through the clipboard

- **Date:** 2026-10-16 · **Status:** Accepted

**Context.** pyautogui presses US-layout keys, so under a Ukrainian layout a Latin name came out as Cyrillic gibberish. Unicode `SendInput` dropped and repeated characters in Windows 11 Notepad.

**Decision.** Text is typed through a clipboard paste, which is exact in any layout. Focus is taken without the lone Alt tap, which opens menu bars.

## ADR-016: Irreversible steps are dedicated actions behind a UI gate

- **Date:** 2026-10-16 · **Status:** Accepted

**Context.** Emptying the Recycle Bin through computer use ticked cleanup lists and never pressed Delete.

**Decision.**
- Irreversible operations such as `empty_trash`, shutdown and permanent delete are dedicated actions behind `core/confirm.py`. Its token comes only from the HUD button.
- Reversible actions run at once and push an undo (`core.undo.push_undo`).
- Notes edits, forgetting memory, file edits and app closing all follow this rule.

## ADR-017: Replacing Gemini Live: local speech and Claude as the brain

- **Date:** 2026-10-16 · **Status:** Parked (the owner: «я хочу відкласти це завдання…»)

**Context.** Gemini picks the wrong tools, repeats calls after an interruption and chains blind clicks. The free vision quota runs out, so `screen_click` and computer use take 10–60 s. The owner already pays for Claude Code and Codex.

**Decision when resumed.** Two steps; measurements and the plan are in `docs/research/gemini-alternatives/README.md`.
1. A Claude rung in `core/gemini.py` for one-shot calls (vision, computer use, file reading), run as a warm `claude -p` stream-json process.
2. A voice mode behind a switch, with Gemini Live staying the default and the fallback:
   - Silero VAD, Whisper turbo, a warm Claude session with an MCP bridge to `_execute_tool`;
   - `tts_uk` per sentence, with an English → Cyrillic dictionary;
   - an estimated ~1.7–4 s from end of speech to first sound, against ~1 s today.

**Rejected.**
- Rotating several Google keys: Google's terms forbid circumventing limits.
- Fully local Gemma 12B: 3–15 s per answer, and it once claimed an action without calling a tool.
- Claude with Gemini only for STT/TTS: three network hops, and no transcription-only mode.
- Energy-threshold end-of-speech: it never fired in a real room.

## ADR-018: Memory lives in one SQLite file, vectors included

- **Date:** 2026-10-21 · **Status:** Accepted

**Context.** Memory was one JSON file of facts. The owner asked whether a dedicated local vector store would be better.

**Decision.** Use `memory/memory.db`, a single SQLite file, for facts, history, suggestions, session summaries, the notes index, named state and vectors.
- Vectors are BLOBs next to their rows, scanned by numpy behind `memory/search.py` → `VectorIndex`.
- A fact, its vector and its FTS entry are written in one transaction.
- Delete triggers remove the vector with the row, so "forget X" never leaves a searchable copy.
- The schema is versioned with `PRAGMA user_version`. Migrations are append-only and run inside a transaction.
- One connection behind an RLock serves the asyncio, Qt and indexer threads.

**Why not Chroma, LanceDB or Qdrant.** At this scale (a few thousand vectors; an 83-note vault is about 1,800 chunks), a brute-force scan takes 1–3 ms. Each of them would be a second source of truth to keep in step, plus 100 MB or more of dependencies. Swapping in another engine later (for example sqlite-vec) touches one class.

**Also rejected.** Supabase or Postgres, maseko's Rust wheel, TeddyDARKVADOR's unencrypted OneDrive sync.

## ADR-019: EmbeddingGemma on CPU through fastembed

- **Date:** 2026-10-21 · **Status:** Accepted

**Context.** The owner asked for the best model, with disk space no concern. Queries mix Ukrainian and English.

**Decision.**
- `google/embeddinggemma-300m` (768 dimensions) through `fastembed` (ONNX Runtime, CPU, no torch). The 1.24 GB cache lives in `config/embed_models/`.
- Task prompts differ for search queries, documents and sentence similarity.
- It loads in the background. Until it is ready, or when switched off on the MEMORY panel, memory runs on full text only.

**Measured on 31 Ukrainian and English questions:**

| Model | Top-1 | Notes |
|---|---|---|
| EmbeddingGemma | 29/31 | ~20 ms per query |
| e5-large | 28/31 | Scores bunch at 0.77–0.83, so no usable threshold |
| Qwen3-0.6B-Q | 27/31 | 720 ms per query |
| Multilingual MiniLM | 28/31 | — |

**Consequence.** This is an exception to the "no heavy dependencies" constraint, and it is optional. The first index of an 83-note vault took 207 s of background CPU.

## ADR-020: Hybrid search: full text plus meaning, fused by rank

- **Date:** 2026-10-21 · **Status:** Accepted

**Decision.**
- **Full text:** FTS5 with `unicode61 remove_diacritics 2`, which folds Cyrillic case but keeps й/и and ї/і distinct.
  - Every query token is quoted, so FTS syntax is never executed.
  - Long tokens are cut into prefixes to catch Ukrainian inflection.
  - Stopwords are dropped and apostrophes unified.
- **Fusion:** FTS hits and cosine hits are fused by reciprocal rank (k = 60).
- **Floors:** per-kind floors and "strong" thresholds (fact 0.20/0.30, session 0.25/0.35, chunk 0.30/0.40). A few weak hits are kept when nothing is strong.
- **Fact text** is embedded as "category — key: value", because short facts score low in absolute terms.

**Consequence.** A Ukrainian question finds an English fact and the reverse. The floors were calibrated on a few dozen phrases and may need re-checking as the store grows.

## ADR-021: Facts are kept in the user's words, with history

- **Date:** 2026-10-21 · **Status:** Accepted

**Decision.**
- Values are stored in the words and language the user said them in, not translated or paraphrased. This was the owner's choice.
- A new value for a slot `(category, key)` supersedes the old row instead of overwriting it, so history survives and a wrong save can be reverted.
- Each row records `source` and `evidence`.
- `forget_memory(subject)` deletes the matching facts with their history, names them, and pushes an undo that restores them.
- The legacy `long_term.json` is migrated once and kept as `.bak`; the news monitors moved to DB state.

**Rejected.** omygod9138's ban on "language" keys; auto-saving tool results as facts.

## ADR-022: A fact the user did not say waits for confirmation

- **Date:** 2026-10-21 · **Status:** Accepted

**Context.** The model sometimes writes "facts" the user never said, including once from notes search results.

**Decision.** `memory/trust.py` checks each model-written fact against the user's recent words, gathered by `core/activity.recent_user_text()`.
- A fact is backed by words (prefix or transliteration matching, share ≥ 0.5) or by meaning (sentence similarity ≥ 0.69).
- Questions never back a fact.
- An unbacked fact is saved as *proposed*. It is rechecked after 3 s, because the transcript can arrive after the tool call. If still unbacked, it waits on the MEMORY panel with ✓ / ✕ and never reaches the prompt.
- The reply to the model is always "ok", so it does not argue.

**Rejected.** TeddyDARKVADOR's words-only provenance gate (it misses paraphrases and the other language); regex rule detectors.

## ADR-023: Conversations are kept as summaries, not transcripts

- **Date:** 2026-10-21 · **Status:** Accepted

**Decision.**
- At the end of a session, a 2–4 sentence summary of the last 60 turns is stored for good, with secrets redacted.
- `recall_conversation(query)` searches the summaries.
- The briefing mentions the last unbriefed summary once, if it is under 14 days old.
- Full transcripts are not stored. This was the owner's choice, for privacy and size.

## ADR-024: The Obsidian vault is indexed, read and edited with undo

- **Date:** 2026-10-21 · **Status:** Accepted

**Decision.**
- **Vault:** the one chosen on the MEMORY panel, or else the vault open in Obsidian (`obsidian.json`).
- **Indexing:**
  - incremental, by mtime and size, at most once per 30 s;
  - chunks split by headings, about 900 characters with 150 overlap;
  - embedded by a background indexer;
  - no file-count cap (BlackStarG5's 2,000 cap was dropped).
- **Search:** at most two hits per note. A folder note (`Project/Project.md`) wins over files buried deeper.
- **Notes as data:** note text reaches the model wrapped as data, never as instructions, and is never saved as a fact.
- **Edits:**
  - append under a heading's own text, create, remove a line with its sub-items, replace;
  - atomic writes (temp file plus `os.replace`), acting at once with undo;
  - undo refuses if the note changed since.
- **Exact names:** edits go only to an exactly named note (compared without spaces or case); a vague name is refused with candidates.

**Mixed from.** BlackStarG5 (document index), aonizai (documents and chunks), Nathan123y (edit with undo).

## ADR-025: Messengers are driven by what each app shows UI Automation

- **Date:** 2026-10-21 · **Status:** Accepted

**Context.** Every fork but one sends blindly (Win, app name, Ctrl+F, paste, Enter, "Message sent"). borgelevisberg17's `social_control` has the right rules but looks for a conversation header that neither Telegram nor Viber exposes. Each app was measured on the owner's PC.

**Decision.**
- **Rules (borgelevisberg17):** one window of the app's own process; a search result only when its name is exactly the person's, two of them a question; the opened conversation must prove it is theirs before anything is typed; sent means the message is in the chat and the box is empty.
- **Telegram:** its search box takes the focus from UIA; results are ListItems with Type/Name cells (a person wins over a channel or bot of the same name); the open chat is named only in the window title.
- **Viber:** names nothing to UIA, so a chat opens by the person's phone number (`viber://chat?number=`), and "it opened" means the bubbles on screen changed. When they did not (that chat was already open), the bubbles remembered from the last confirmed open, or one vision look at the header, decide.
- **Contacts:** `config/contacts.json` ties a spoken name to each app's name and the phone number (Nathan123y's refuse-rather-than-guess lookup, Kira's margin over the runner-up, masked numbers, emergency numbers refused).
- **Gate:** ADR-014 for every app. A confirmed=true call that nobody asked about is treated as the first call and asks (seen live: the model passed confirmed=true at once and twice told the user the screen showed no confirmation).

**Rejected.** Kira's WA-JS injection into WhatsApp Web (ban risk); JAlmuscaty's vision-picked click points; vision as the first way to find a chat.

## ADR-026: Calls start only on the user's yes and are seen locally

- **Date:** 2026-10-21 · **Status:** Accepted

**Context.** The owner's rule: the assistant opens the contact, and the call starts only after their confirmation; declining an incoming call needs none.

**Decision.**
- **Dial:** Telegram's "Call", then "Start Call" or "Start Video" in its call window; Viber's call button, then "Безплатний дзвінок Viber" in the menu it opens, never Viber Out (paid); Slack's Ctrl+Shift+H (pressed to hang up only while in a huddle, because it also starts one); Phone Link through `tel:`.
- **In a call** when any of these holds: Windows' microphone privacy record says the app holds the mic; the app has an active audio capture session on any microphone (Viber recorded from the webcam's mic and never touched the privacy record); a call window shows a hang-up control.
- **Viber's call windows** show UIA nothing: buttons are found by colour in a capture of that window only (a red and a green disc mean ringing; in a call, the white disc with a red handset, because the muted mic and camera are red pills).
- **Hold:** while a call is on, the assistant falls asleep, streams nothing and announces nothing (`main._run_call_watch`).
- **COM** for call detection runs on one thread that collects its own garbage, and interfaces come from `QueryInterface`, not pycaw's `cast`: both crashed the process with an access violation (a double release).

**Rejected.** JAlmuscaty's auto-decline loop with screenshots to Gemini; rilzisheree's escalation calls without a yes; Nathan123y's Twilio line (a business line with costs).

## ADR-027: Messages are read from the apps and from Windows notifications

- **Date:** 2026-10-21 · **Status:** Accepted

**Context.** Without a reading tool the model looked at the screen and called the owner's own message a received one.

**Decision.**
- **Reading on request (`read_messages`):** Viber's open chat, each bubble theirs (left) or the user's (right); Telegram's chat list cells (last message, sender, unread count, Delivery "Received" for incoming), so nothing is opened or marked read.
- **New messages (`watch_messages`, `core/message_watch.py`):** read-only from `wpndatabase.db`, the store of every Windows toast. Slack writes there, and Phone Link writes the phone's Viber, Telegram, WhatsApp and SMS; Telegram and Viber on the PC do not. Only chats the owner named are announced, one item per chat, when the room is quiet, never during a call.
- **Message text** reaches the model as quoted data, never as instructions; nothing is answered without the send gate.

**Rejected.** Nathan123y's `chat_takeover` (replies in the owner's style without a yes per message); reading the screen by vision.
