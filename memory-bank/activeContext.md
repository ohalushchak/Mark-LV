# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-15_

## Current focus

- Self-check and self-repair, stages 1–5, committed on branch `feature/self-repair` (not merged into `main`, not yet tried live):
  1. **Honest results:** `core/tool_args.py` checks every call against its schema in `_execute_tool`; "no result" is reported as unverified.
  2. **Health:** `core/health.py` — the error journal and per-tool status.
  3. **Self-check:** `core/capabilities.py` and the `self_check` tool.
  4. **Self-repair:** `core/self_repair.py` and the `self_improve` tool, on top of `agents.start_owned`.
  5. **Review, apply, restart:** `ReviewOverlay` and `RepairPanel` in `ui.py`, `JarvisLive.restart_app`, `core/restart_helper.py`.
- First live test, 2026-10-15: `self_check` status, errors and the yt-dlp finding worked. Self-repair never started:
  - the model asked "shall we start?" and called `run` in the same turn;
  - the gate refused, the model told the user it had started anyway, then a 1008 dropped the conversation.

  Fixed since: the question tells the model to end its turn; a premature run returns `[NOT_STARTED] … Do NOT say it has started` (also for `delegate_to_agent`); the SELF-REPAIR panel shows the waiting question with START / ✕, so a click can answer it; the missing-package hint is `python -m pip install -U …` (the model had run `python setup.py` in the wrong project).
- Second live test, 2026-10-15:
  - START on the panel answered the question;
  - the model waited for the answer before running;
  - both runs reached Codex in the worktree, but no command could start: Codex's sandbox setup was failing machine-wide because the Codex app's computer-use runtime held `node_repl.exe` (see `techContext.md`);
  - the model then used `git_project` and `delegate_to_agent`, which act on the current project (`jarvis-sandbox`), so it looked as if self-repair used the wrong project.

  Fixed since: a sandbox failure is reported as such, with the advice to switch to Claude Code or close the Codex app; it does not count towards the two failed tries. The failure announcement tells the model that this was JARVIS's own code and not to use the project tools for it. The question names the task.
- In `main` before this: coding by voice with Claude Code / Codex delegation and agent-session announcements; compact mode, tray and quick prompt; the HUD redesign; voice barge-in; monitor-under-cursor screenshots; `close_app` by name.

## Recent decisions

- Self-repair mixes the forks and avoids their weak spots:
  - **The fixer:** our coding agent (Claude Code / Codex), not a one-shot Gemini diff like every fork used.
  - **Ideas taken:** tests before and after and the review window (klcenka9); the whole change refused on one bad path, tests never editable by the fixer, memory of failed and rejected tasks (TeddyDARKVADOR); validation in a separate copy (syamsulmuiin, here a git worktree); stale-HEAD check and case-insensitive protected paths (Robin); schema checks of tool calls (BlackStarG5).
  - **Rejected:** auto-applying "safe" changes (klcenka9), approval by phrase match or by tool parameter (syamsulmuiin), approval without showing the diff (Robin), the 10-minute auto-update with `os._exit` and the approval-free self-coding plugin (dragonballls). Upstream self-update was not ported: the fork has diverged too far for automatic merges.
- Only the HUD's APPLY button applies a change; `self_improve` can ask, run, show, discard, cancel and restart. Starting a run is behind the same "the user answered after the question" gate as `delegate_to_agent`.
- Protected from the fixer: the gates and the loop that checks it — confirm, undo, shell policy, loaders, tool_args, health, capabilities, self_repair, restart_helper, agents, activity — plus the prompt, `config_manager`, AGENTS.md, CLAUDE.md, `.gitignore`, requirements, setup, `config/`, `.github/` and existing tests. New `tests/test_*.py` files are allowed and flagged. `main.py`, `ui.py` and `core/*` changes are allowed but marked "core code" in the review.
- An applied change is one commit on the user's current branch (fast-forward from the worktree branch), made by the APPLY click — that click is the user's explicit request to commit.
- The error journal and self-repair data live under `~/.jarvis/`, outside the public repository.

## Next steps

- Try it live on Windows:
  - a malformed call being corrected;
  - `self_check` status, errors and probe;
  - a real fix with Claude Code and then with Codex in the worktree;
  - review, APPLY, the restart and undo;
  - a deliberately broken change, to see the watchdog roll it back.
- `yt-dlp` is not installed on this machine (found by the first self-check): YouTube on the HUD falls back to the browser until `pip install -U yt-dlp`.
- Add `health.failed(why)` to the actions' broad `except` blocks so their failures show in tool health.
- Handle the Live server's GoAway, the remaining 1008 cause: it arrives before the session-duration limit, and today the server closes with "client failed to close the connection after receiving a GoAway signal". Reconnect with the resumption handle when `go_away` arrives.
- 1008 "The operation was aborted" is back. On 2026-10-15 there were six in one session, each after an `audio_stream_end` (asleep, after an announcement while asleep, and twice in a row straight after connecting asleep), plus one 1011 keepalive timeout. The Recv error line now prints the seconds since connecting and since the last `audio_stream_end`. Collect those figures before changing the stream-end logic.
- Muting the mic while awake also stops the stream without `audio_stream_end` — watch for 1008s after answers while muted.
- Monitor choice for `screen_process` — deferred; the plan is in `progress.md` → Fork backlog.
