# Progress

What works, what is left, and known issues. Upstream releases up to Mark LV are described in `readme.md`; this file tracks the fork from there.

_Last updated: 2026-10-21_

## Done in this fork

- Messengers and calls, mixed from forks (tests in `tests/test_contacts.py`, `test_messengers.py`, `test_calls.py`, `test_message_watch.py`, `test_send_gate.py`; ADR-025 – ADR-027). Tried live on Windows with a real contact.
  - **Contacts:** `core/contacts.py` + the `contacts` tool (find, list, add, rename, remove, undo); `config/contacts.json` is git-ignored. Nathan123y's refuse-rather-than-guess lookup, Kira's margin rule, masked numbers, emergency numbers refused.
  - **Messages:** `send_message` drafts and sends in Telegram (search box, Type/Name cells, the title names the chat) and Viber (opened by phone number, bubbles must change) besides Slack; a confirmed=true that nobody asked about now asks first. Live: drafts, sends after the yes, emoji-only drafts.
  - **Calls:** the `call` tool (start, accept, decline, end, status) and `core/calls.py` (mic privacy record, audio capture sessions, call windows; Viber's buttons found by colour). Live in Viber: dial ("Безплатний дзвінок Viber"), decline, accept after the yes, hang up; in Telegram: dial ("Start Call"), hang up. While a call is on, `main._run_call_watch` keeps the assistant asleep and silent — seen live.
  - **Reading:** `read_messages` (Viber's open chat marked theirs / the user's; Telegram's last message per chat or its unread chats, nothing marked read) — live in both.
  - **New messages:** `watch_messages` + `core/message_watch.py` read Windows' notification database (Slack; the phone's messengers through Phone Link) and announce chats the user named (`main._run_message_watch`).
  - **Fixed on the way:** call / Send-like buttons clicked by `screen_click` or computer use now go through the send gate; the console log no longer prints message text; a COM double release (pycaw's `cast`) that killed the process.
- `docs/PRD.md` and `docs/ADR.md`: the product requirements (with built / partly tried / planned status) and the architecture decisions with their reasons and rejected alternatives, linked from `CLAUDE.md`.
- Memory in SQLite with search by meaning, mixed from forks (tests in `tests/test_memory_store.py`, `test_memory_search.py`, `test_memory_trust.py`, `test_notes_index.py`, `test_notes_edit.py`, `test_memory_panel.py`). Tried live on Windows in five sessions.
  - **Store:** `memory/memory.db` with facts, history, suggestions, session summaries, the notes index and vectors (TeddyDARKVADOR's schema ideas with INTEGER rowids; maseko-lucky-9's migration recipe; bfrpaulondev's provenance). `long_term.json` migrated and kept as `.bak`; monitors moved to DB state.
  - **Search:** FTS5 + EmbeddingGemma vectors fused by rank (Giljacob67's fastembed index, re-done for Cyrillic and off the event loop; reyesservice34-hue's quoted FTS query). Facts are stored in the user's own words and language; a Ukrainian question finds an English fact and back.
  - **Trust:** a model-written fact is checked against the user's recent words (not questions), with a 3 s recheck for a lagging transcript; unbacked facts wait on the MEMORY panel (✓ / ✕). `forget_memory` deletes facts about a subject and names them; undo restores.
  - **Conversations:** summaries kept for good (2–4 sentences, secrets redacted), searched by `recall_conversation`; the briefing still mentions the last one once. Full transcripts are not stored (the user's choice).
  - **Notes:** the Obsidian vault (auto-detected or chosen on the panel) indexed incrementally; `notes` searches, reads, opens and edits — append (under a heading's own text), create, remove a line, replace — every edit undoable (BlackStarG5's document index without its 2,000-file cap; Nathan123y's edit-with-undo flow).
- Computer use with verified results, mixed from forks (tests in `tests/test_verify.py`, `test_verified_actions.py`, `test_send_gate.py`, `test_computer_use.py`). Tried live on Windows in eight sessions; `empty_trash` and the computer-use pause before a risky step are not yet seen live.
  - **Verdicts:** `TOOL["snapshot"]` / `TOOL["verify"]` run by the action loader; results end with [VERIFIED] / [UNVERIFIED] / [FAILED] (bfrpaulondev's postconditions, syamsulmuiin's "no evidence = unverified"); a FAILED verdict counts in tool health.
  - **Checked actions:** `open_app` (new window / process, an app already running is brought forward), `computer_control` (pointer, focus, a noise-masked screen sample, the focused field read back), `computer_settings` (volume, mute, brightness, theme, window state on a named window), `file_controller` (the disk read back), `browser_control` (page state before/after, BlackStarG5; the real browser's address bar through UIA).
  - **Finding:** `screen_click` / `screen_find` by UIA name, then vision with a native-resolution second look, mapped through the monitor's rectangle, refusing ambiguous and unlike matches (rilzisheree's coordinate proof); `screen_process` and both take `monitor` (cursor / primary / left / right / number).
  - **Messages:** `send_message` opens the conversation (Slack / Discord jump box, the matching result picked from UIA), types a draft and leaves it; `send` asks through `core/send_gate.py` (voice answer or HUD CONFIRM). Enter in a chat box and Send buttons go through the same gate.
  - **Computer use:** `computer_use` → `core/computer_use.py` (bfrpaulondev's loop without its provider router, JishnuDevTech's re-locating, rilzisheree's yes/no/unknown final check); risky steps by the real UIA label pause for the user's yes; moving the mouse stops it.
  - **Fixed on the way:** pycaw 2025's API (volume read/set had silently failed), explicit mute/unmute, `ShowWindow` minimise/maximise, layout-free typing, focus without the Alt tap, `ms-settings:` links kept by `open_app`, `empty_trash` instead of clicking cleanup lists.
- Self-check and self-repair, mixed from forks (tests in `tests/test_tool_health.py`, `test_self_check.py`, `test_self_repair.py`, `test_repair_panel.py`). Tried live on Windows with Claude Code up to APPLY and the restart; the agent ring, undo after restart and the watchdog rollback are not yet seen live.
  - **Honest results:** tool arguments checked against the schema before anything runs, with a "nothing ran, correct X" refusal (BlackStarG5); no result is reported as unverified instead of "Done.".
  - **Health:** a redacted error journal and per-tool green / yellow / red (klcenka9's thresholds, syamsulmuiin's redaction); uncaught thread and asyncio errors journaled.
  - **Self-check:** `self_check` — status, the journal, one error in detail, probes of tools, packages, git, disk, key and audio, and an optional test run (TeddyDARKVADOR's probes; `TOOL["probe"]` on run_command, git_project, delegate_to_agent, project_workspace).
  - **Self-repair:** `self_improve` → `core/self_repair.py`: the agent changes JARVIS's code in a worktree; tests before and after (klcenka9), a change check that refuses the whole change on one protected path (TeddyDARKVADOR, Robin), memory of rejected and twice-failed tasks; review with the diff on the HUD, APPLY/DISCARD buttons only, apply = one fast-forward commit with undo; restart through `core/restart_helper.py`, which rolls back a change that stops the next start.
  - Dry run on the real repository with a stand-in agent: worktree, 271 tests before and after, check, review and discard in 39 s, nothing left behind.
- Coding by voice and bridges to other AIs, mixed from forks (tests in `tests/test_coding_tools.py`, `test_git_project.py`, `test_project_workspace.py`, `test_agents.py`, `test_agent_panel.py`, `test_agent_relay.py`, `test_ask_chat_app.py`). Tried live on Windows.
  - **Coding tools:**
    - a current project found by name;
    - `run_command` with a lexing three-tier policy, background jobs announced when done, and a visible terminal;
    - `git_project` (BlackStarG5's operations; undo for commit, branch and switch; CONFIRM for push and PR; key files never committed);
    - `project_workspace` (exact-text edits checked against a read hash, undoable; key files never read).
  - **Agents:** `delegate_to_agent` runs Claude Code or Codex behind rayhao-han's ask → answer → run gate, with session resume. The HUD has an AGENT panel (engine, model, effort, usage limits, job with STOP), plus a card line and a tray submenu.
  - **Other bridges:** `ask_chat_app` (Nathan123y's clipboard hand-off) and opt-in agent-session announcements through a hook installed into both CLIs (omygod9138's idea, without herdr).
  - **Shared plumbing:** `core/announce.py` speaks late results when the room is quiet, also while asleep; `core/activity.py` orders speech and user input.
- Live 1008 "The operation was aborted" disconnects fixed: the mic stopping is signalled with `audio_stream_end` (asleep, after an answer while asleep, on connecting asleep).
- Compact mode, mixed from forks (tests in `tests/test_compact.py`): a draggable always-on-top card with the orb, state, last reply, hover HUD/MUTE/STOP, a right-click menu (omygod9138's pattern, maltahhan10-a11y's handling); a confirm gate brings the HUD back; a tray icon with state, latest reply and the everyday switches (Nathan123y's menu-bar design on `QSystemTrayIcon`); a quick prompt on a global F9 / Ctrl+Alt+J via `RegisterHotKey` (omygod9138's prompt without pynput). Typed text now reaches the captions too, and wakes a sleeping assistant instead of being refused.
- HUD redesign, mixed from forks (tests in `tests/test_hud.py`): a wireframe orb style (`core/orb_hud.py`, from Kennycen) and an anime face style (`core/anime_avatar.py`, madshadowtest) beside the head and the reactor; a livelier head (state-driven smile, heavy lids when asleep, eye glow and gradient iris, lower teeth, breathing — madshadowtest); a soft slate theme preset (AcaneloL palette); a per-OS monospace font and one TEXT SIZE setting for all text (100–150 %); a bigger, theme-tinted mute button; captions of the latest exchange under the HUD (Nathan123y); a desktop orb when the window is out of sight, only while speaking or always (rilzisheree's idea). `JarvisUI.glance` now reaches the canvas — it referenced a missing attribute and silently did nothing.
- `close_app` closes the named app (`actions/_app_closer.py`, tests in `tests/test_close_app.py`): found by process (open_app aliases) or window title, asked to close like its ✕ (WM_CLOSE / AppleScript quit / `wmctrl -c`), checked afterwards; reports missing admin rights or a save prompt instead of "Done"; never closes the assistant or the shell; undo reopens the app. It used to press Alt+F4 in whatever window had focus.
- Screen capture takes the monitor under the mouse cursor, falling back to the primary (`_pick_monitor` in `actions/screen_processor.py`, tests in `tests/test_screen_capture.py`). It used to take `mss` monitor 1, which on a multi-monitor setup was a 1280×720 AV-receiver output instead of the 3440×1440 primary.
- Voice barge-in, opt-in (`core/barge_in.py`, tests in `tests/test_barge_in.py`, `tests/test_interrupt_flow.py`, `tests/test_barge_pause.py`): settled-room and residual requirements, ~0.64 s of evidence, pause-and-confirm with resume, interrupt-by-name mode.
- Wake word with a custom name: engines openWakeWord / sherpa-onnx / Vosk, settings panel, replay of the command said with the wake phrase, `sleep_assistant` voice tool, optional follow-up mode, chime, re-arm with model reset and 3 s quiet period, consecutive-frame check. Tests in `tests/test_wake_word.py`, `tests/test_wake_flow.py`.
- Interrupt bookkeeping fixes: no lost next reply, output buffer flushed on interrupt, `server_content.interrupted` handled, echo tail sized from the playback cursor with ~128 ms pre-roll.
- `core/echo.py` accepts `(frames, channels)` mic blocks (the 2-D shape used to raise inside the guard).
- `.gitignore` secret rules actually match.
- Removed unused legacy modules from an earlier Mark: `core/llm_client.py`, `core/stt.py`, `core/tts.py`, `core/installer.py`.

## Left to build

Upstream roadmap "LVI+" (interrupt by voice is done):

- [x] Conversation history — as searchable summaries (`recall_conversation`); full transcripts deliberately not kept
- [ ] Telegram remote
- [ ] Full file access
- [ ] Security camera
- [x] Obsidian integration — search, read, open, edit (`notes`)

Fork backlog (deferred by the user, 2026-10-14):

- [ ] Alternatives to Gemini Live (parked 2026-10-16): a Claude rung in `core/gemini.py`, then local STT/TTS with Claude Code as the brain behind a mode switch. Research, measurements and plan: `docs/research/gemini-alternatives/README.md`.
- [ ] Messengers stage 5 (chosen by the user): the assistant speaks into a call — rilzisheree's `messenger_audio_bridge` (VB-CABLE in, loopback out), its own Live session with an AI disclosure; echo and Phone Link audio still to solve.
- [ ] Viber chats without a phone number (shops, groups): open through Viber's search and confirm the header by vision.
- [ ] `screen_process` with `monitor='all'`: each monitor as its own image in the same turn (each compressed to 1280×720), never one stitched 7280-px-wide frame. Choosing one monitor is done.

## Known issues

- Memory: the first index of a vault takes minutes of CPU in the background (207 s for 1,712 chunks); a spoken recall during it waits up to ~1 s for the batch in progress. Floors and the trust thresholds were calibrated on a few dozen phrases; a long-running store may need them re-checked. The model may still save something it read as a "fact" (it did once after a notes search); questions no longer back facts, so such saves now land as suggestions.
- Notes editing writes only to an exactly named note; a vague name is refused with candidates. Obsidian vault detection is untested on macOS/Linux.
- `ui.py` reuses names easily: a new `JarvisUI` / `MainWindow` member with an existing name silently replaces the old one (it happened with `show_review` / `_review_sig`). Check with a grep before adding one.
- Tool health sees a handler's caught error only when it calls `health.failed(why)` or its sentence starts like a failure ("Error…", "Failed…"); most actions still return their errors as plain sentences, so their status stays green.
- Self-repair restart: the new instance runs on the old one's console, so the shell prompt comes back while JARVIS keeps printing. Restart and rollback are untested on macOS and Linux.
- After a `screen_process` call the model often starts a short acknowledgement, and the image turn interrupts it (seen live: 0.2–5 s of speech discarded per capture).
- Voice barge-in: background speech (a television, people in the room) still gets transcribed as words and confirms a pause; interrupt-by-name is the answer for such rooms. A short "stop" (< 0.64 s) no longer interrupts by voice.
- Wake engines are verified only on Windows TTS speech, not a real microphone. Grammar-restricted Vosk can mistake similar words (an English test heard "market" as "marco"); two-word phrases are more reliable.
- `close_app` on macOS and Linux is untested (no such machine here); on Linux it needs `wmctrl`. Closing an elevated window (Task Manager) is reported, not done, unless the assistant itself runs as administrator.
- HUD redesign was tried live on Windows only (styles, themes, captions, desktop orb in both modes); macOS and Linux are untested, notably the translucent, click-through desktop orb window.
- TEXT SIZE applies after a restart. At 150 % a few fixed-height spots are tight (the drop zone's second line touches its border, the command-input placeholder is cut).
- A few neon-era tints are still fixed hex (`#002010`/`#001a08` active-button greens, camera backgrounds) and look slightly off on the soft theme.
- The desktop orb also appears when the HUD is visible on another monitor but not the focused window; its mode can be set to off in CONTROLS.
- Compact mode, tray and quick prompt are built and tested for Windows only; on macOS/Linux they fall back to plain Qt (window-scoped quick-prompt key) and are untried.
- F9 is also an IDE debugger key (VS Code breakpoint, IntelliJ resume); while JARVIS runs with the F9 setting, those IDEs do not get it. Ctrl+Alt+J or off is in CONTROLS.
- The Live server closes the session with 1008 when it receives no GoAway reply: `main.py` ignores the server's GoAway (sent before the session-duration limit), so the server aborts the connection ("client failed to close the connection after receiving a GoAway signal"). The assistant reconnects after 3 s, but an answer in progress is lost. (The other 1008, "The operation was aborted", came from the server waiting for audio while the sleeping mic streamed nothing; fixed with `audio_stream_end`, see *Done*.)
- Quitting through `shutdown_jarvis` prints Qt warnings `QObject::killTimer: Timers cannot be stopped from another thread` (the exit runs on the asyncio thread); harmless.
- Changing the wake phrase does not update the `{wake_phrase}` prompt line until the next reconnect.
- Coding tools: routing between `project_workspace`, `run_command`, `code_helper`, `dev_agent`, `delegate_to_agent` and `ask_chat_app` rests on tool descriptions plus the `[WRONG_TOOL]` guard; the first live test misrouted delegation until both were tightened. macOS/Linux paths (bash, `open -a`, terminal emulators) are untested.
- Claude Code's usage limits are known only as of its last run; the ↻ refresh spends a one-word haiku request.
- Codex's sandbox writes files as a sandbox user: in folders with owner-only ACLs (Python's `mkdtemp`) the user cannot read them. Codex may ask to trust the relay hook in its next interactive session.
- The agent-session relay cannot see whether the user is looking at the session; it relies on the 30 s minimum turn. A permission prompt counts as answered when its transcript file changes, a heuristic.
- `run_command` output from cmd-era tools is decoded with the OEM code page only when it is not valid UTF-8.
- After "Server reports interruption" the model sometimes repeats the same tool call: in Slack a `type` ran twice. Not guarded — a repeat cannot yet be told from a new request ("louder" said twice).
- Vision calls (screen_click by sight, computer use) take 10–60 s on this key: 3.6-flash and 3.5-flash run out of quota and flash-latest times out, so the ladder falls to the Live rung; each computer-use run spends several calls.
- Verification, finding by UIA name, the send gate's message-box detection, conversation jumps, calls and computer use work on Windows only; elsewhere the verdict is UNVERIFIED and `send_message` refuses to open a conversation. Slack (Ctrl+K), Telegram and Viber are tried live; Discord shares Slack's shortcut, Teams / WhatsApp fall back to Ctrl+F + Enter and are untried.
- Viber: a chat opens only by phone number, so shops and groups cannot be written to or read; the first open after a start may take a vision look at the header (up to a minute on this key) when that chat was already on screen. `read_messages` sees only the bubbles on screen; Telegram shows only each chat's latest message.
- Viber's call buttons are found by colour (its call windows show UIA nothing), so a Viber redesign can break decline / accept / hang up there; the `[Calls]` console lines describe what a call window offers.
- Calls not yet tried live: Slack huddles, Phone Link (`tel:` is not set as the default handler on this PC), an incoming Telegram call. Video works in Telegram and Viber only.
- With the wake word off, the assistant cannot be woken by voice during a call (the mic stays closed); typed text wakes it, and then it hears the call until it ends.
- New-message announcements come only from Windows notifications: Telegram and Viber on the PC write none, so their messages are seen through the phone (Phone Link). Not yet heard live.
- `actions/computer_settings.py` still uses pycaw's `cast(...)` for the volume interface — the pattern that crashed call detection with a double release.
- `screen_click`'s label check cannot compare an English description with a Ukrainian label, so it trusts such matches.
- `empty_trash` waits for the HUD button only (no voice yes), like the other irreversible actions behind `core/confirm.py`.
- A click is VERIFIED when the window in front, the focused control or the screen near it changed — a change caused by something else in that half-second (Snap Assist appearing) is credited to the click.
