# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-15_

## Current focus

- Coding by voice and bridges to other AIs merged into `main` and tried live on Windows in five sessions:
  - **Coding tools:** `run_command` (`core/shell_policy.py`, `core/jobs.py`), `git_project` and `project_workspace` on a shared current project (`core/projects.py`).
  - **Agents:** `delegate_to_agent` (`core/agents.py`) runs Claude Code or Codex. The AGENT panel shows the engine, model, effort, 5-hour and weekly limits and the running job with STOP; there is also a card line and a tray submenu.
  - **Chat apps:** `ask_chat_app`.
  - **Agent-session announcements:** `core/agent_hook.py`, `core/agent_relay.py`, switched on in CONTROLS.
- Live results:
  - **Coding:** project, files, commands, git, delegation (Claude with session resume, Codex) and relay announcements all work.
  - **Fixed along the way:**
    - "pass to Claude/Codex" was routed to `ask_chat_app`;
    - a project named by the relay became the current one;
    - announcements while asleep were answered as requests ("I am asleep…");
    - every announcement while asleep was followed by a 1008 disconnect.
- In `main` before this: compact mode, tray and quick prompt; the HUD redesign; voice barge-in; monitor-under-cursor screenshots; `close_app` by name.

## Recent decisions

- Coding tools: mixed from forks.
  - BlackStarG5's code is the base: the job engine, exact-text edits with a read hash, and fixed git operations.
  - PrinceRaval1706 gave the three tiers, but its substring classifier was not taken: it called `npm update` and `python tools.py` safe. Its cancel never worked (a missing `global`).
  - deepakchouguledatascience gave the visible-terminal mode.
- Tests and builds count as SAFE commands; installs, deletes, scripts and anything unrecognised need CONFIRM; push and PR creation always need CONFIRM; commit, branch, switch, edits and new files go on the undo stack instead.
- Agents: rayhao-han's two-call gate (ask → the user answers → run), extended to Codex.
  - Claude runs in `auto` mode without prompts; Codex runs in its workspace-write sandbox.
  - Only one agent job runs at a time, and agent changes are not undoable (git diff shows them).
  - "Default" model for Codex means the account's default from the app-server, because Codex's own config names a refused model.
- Routing: anything addressed to Claude, Claude Code or Codex about code goes to `delegate_to_agent`. `ask_chat_app` is only for general questions to the chat apps, and refuses coding jobs with `[WRONG_TOOL]`. A project named in a delegation is used for that job but does not become the current project.
- Usage limits: Codex's come free from the app-server, polled every 5 min. Claude's arrive with each run; the ↻ refresh sends a one-word haiku request. A 5-hour or weekly window at ≥90 % adds a warning to the question; a reached limit refuses and offers the other agent.
- Background results (commands, push, agents, relay) are spoken by one announcer when the room is quiet. While asleep too, at the user's request: they preferred a voice to the tray notification.
  - Each is framed as `[NOTIFICATION]` news to pass on, not a request, and the assistant stays asleep afterwards.
  - There is a 3 s settle time after each connect.
  - The tray only gets an item still unsaid after a minute.
- The mic stopping is signalled to the server with `audio_stream_end` (on falling asleep, after an answer while asleep, on connecting asleep). Without it the server waited for audio and closed with 1008 "The operation was aborted". The fifth live session, with announcements asleep and awake and a long conversation, had no 1008.
- Relay: finished turns shorter than 30 s are not announced; permission prompts after 15 s, and only if the transcript has not moved.
- Agents commit only on the user's explicit request in the current message (`AGENTS.md` → *Commits*).

## Next steps

- Handle the Live server's GoAway, the remaining 1008 cause: it arrives before the session-duration limit, and today the server closes with "client failed to close the connection after receiving a GoAway signal". Reconnect with the resumption handle when `go_away` arrives.
- Muting the mic while awake also stops the stream without `audio_stream_end` — watch for 1008s after answers while muted.
- Monitor choice for `screen_process` — deferred; the plan is in `progress.md` → Fork backlog.
- Try interrupt-by-name live, and each wake engine on a real microphone.
- Pick the next feature from the upstream "LVI+" roadmap (see `progress.md`).
