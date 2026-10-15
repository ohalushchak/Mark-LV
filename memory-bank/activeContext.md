# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-15_

## Current focus

- Self-check and self-repair merged into `main` (from `feature/self-repair`):
  1. **Honest results:** `core/tool_args.py` checks every call against its schema in `_execute_tool`; "no result" is reported as unverified.
  2. **Health:** `core/health.py` — the error journal and per-tool status; `core/console_log.py` copies the console to `~/.jarvis/logs/`.
  3. **Self-check:** `core/capabilities.py` and the `self_check` tool.
  4. **Self-repair:** `core/self_repair.py` and the `self_improve` tool, on top of `agents.start_owned`.
  5. **Review, apply, restart:** `ReviewOverlay` and `RepairPanel` in `ui.py`, `JarvisLive.restart_app`, `core/restart_helper.py`.
  - Plus an agent ring on the hologram while Claude Code / Codex works.
- Tried live on Windows in four sessions (2026-10-15):
  - **Worked:** `self_check` (status, journal, probe — it found `yt-dlp` missing); with Claude Code, the whole loop from the question to APPLY, three times. The voice restart closed the old instance and the new one connected.
  - **The applied changes stayed in the history:** docstrings in `actions/weather_report.py` (`1b28369`), `actions/desktop.py` (`5893918`) and `actions/reminder.py` (`b5a636d`).
  - **Claude Code refused the protected** `core/confirm.py` by itself.
  - **Fixed along the way:**
    - the model running before the user answered, then claiming it had started;
    - "nothing is waiting" after START or ✕;
    - a Codex sandbox failure reported as success;
    - `delegate_to_agent` naming the wrong project for a self-repair job;
    - review names that collided with the document review's;
    - the restarted instance being invisible.
  - **Not yet seen live:** the agent ring; the restart in its own console window; "undo after restart"; LATER, DISCARD, ✕ and STOP; the watchdog's rollback.
- In `main` before this: coding by voice with Claude Code / Codex delegation and agent-session announcements; compact mode, tray and quick prompt; the HUD redesign; voice barge-in; monitor-under-cursor screenshots; `close_app` by name.

## Recent decisions

- Self-repair mixes the forks and avoids their weak spots:
  - **The fixer:** our coding agent (Claude Code / Codex), not a one-shot Gemini diff like every fork used.
  - **Ideas taken:** tests before and after and the review window (klcenka9); the whole change refused on one bad path, tests never editable by the fixer, memory of failed and rejected tasks (TeddyDARKVADOR); validation in a separate copy (syamsulmuiin, here a git worktree); stale-HEAD check and case-insensitive protected paths (Robin); schema checks of tool calls (BlackStarG5).
  - **Rejected:** auto-applying "safe" changes (klcenka9), approval by phrase match or by tool parameter (syamsulmuiin), approval without showing the diff (Robin), the 10-minute auto-update with `os._exit` and the approval-free self-coding plugin (dragonballls). Upstream self-update was not ported: the fork has diverged too far for automatic merges.
- Only the HUD's APPLY button applies a change. `self_improve` can ask, run, show, discard, cancel, restart and undo. A run starts after the user answered the question by voice (the same gate as `delegate_to_agent`) or pressed START.
- Protected from the fixer: the gates and the loop that checks it — confirm, undo, shell policy, loaders, tool_args, health, capabilities, self_repair, restart_helper, agents, activity — plus the prompt, `config_manager`, AGENTS.md, CLAUDE.md, `.gitignore`, requirements, setup, `config/`, `.github/` and existing tests. New `tests/test_*.py` files are allowed and flagged. `main.py`, `ui.py` and `core/*` changes are allowed but marked "core code" in the review.
- An applied change is one commit on the user's current branch (fast-forward from the worktree branch), made by the APPLY click — that click is the user's explicit request to commit.
- The error journal, console log and self-repair data live under `~/.jarvis/`, outside the public repository.
- The model sometimes says a run has started after being told `[NOT_STARTED]`; the tool texts say so as plainly as they can, and the panel shows the true state.

## Next steps

- Try live: the agent ring (Claude Code, and Codex's red flash), the restart in a new console window with "undo after restart", LATER / DISCARD / ✕ / STOP, and a deliberately broken change to see the watchdog roll it back.
- Codex's sandbox fails on this machine while the Codex app's computer-use runtime runs (`techContext.md`); use Claude Code meanwhile.
- `yt-dlp` is not installed on this machine: YouTube on the HUD falls back to the browser until `python -m pip install -U yt-dlp`.
- Add `health.failed(why)` to the actions' broad `except` blocks so their failures show in tool health.
- 1008 disconnects, two causes now told apart by the timings on the Recv error line:
  - the session-duration GoAway (594 s into a session): reconnect with the resumption handle when `go_away` arrives;
  - "The operation was aborted" ~150 s after the last `audio_stream_end` with nothing sent since (asleep; six in one session on 2026-10-15). Collect more timings before changing the stream-end logic.
- Muting the mic while awake also stops the stream without `audio_stream_end` — watch for 1008s after answers while muted.
- Monitor choice for `screen_process` — deferred; the plan is in `progress.md` → Fork backlog.
