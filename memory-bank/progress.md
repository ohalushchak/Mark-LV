# Progress

What works, what is left, and known issues. Upstream releases up to Mark LV are described in `readme.md`; this file tracks the fork from there.

_Last updated: 2026-10-15_

## Done in this fork

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

- [ ] Conversation history
- [ ] Telegram remote
- [ ] Full file access
- [ ] Security camera
- [ ] Obsidian integration

Fork backlog (deferred by the user, 2026-10-14):

- [ ] Monitor choice for `screen_process` (see Known issues). Plan: a `monitor` parameter — default the screen under the cursor (today's behaviour), or `primary`, a number, `left`/`right`, or `all`. `all` sends each monitor as its own image in the same turn (each compressed to 1280×720), never one stitched 7280-px-wide frame whose text cannot be read; `_capture_screen` then returns a list and `_flush_pending_vision` sends several `inline_data` parts. The tool description must tell the model to use it when asked about "the other monitor"/"all screens".

## Known issues

- `ui.py` reuses names easily: a new `JarvisUI` / `MainWindow` member with an existing name silently replaces the old one (it happened with `show_review` / `_review_sig`). Check with a grep before adding one.
- Tool health sees a handler's caught error only when it calls `health.failed(why)` or its sentence starts like a failure ("Error…", "Failed…"); most actions still return their errors as plain sentences, so their status stays green.
- Self-repair restart: the new instance runs on the old one's console, so the shell prompt comes back while JARVIS keeps printing. Restart and rollback are untested on macOS and Linux.
- `screen_process` cannot be pointed at another monitor: it always takes the one under the cursor. Seen live: Task Manager opened on another screen, the model asked to "check the other monitor" four times and got the same screen each time. Fix planned under *Left to build* → Fork backlog.
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
