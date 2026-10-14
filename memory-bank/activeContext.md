# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-14_

## Current focus

- Voice barge-in is merged (`feature/voice-barge-in` → `main`, commit `1b0b443`). It is opt-in ("INTERRUPT BY VOICE", config key `voice_barge_in_enabled`) and has passed only synthetic-audio tests; it still needs trying on real speakers in real rooms.
- Agent tooling: `AGENTS.md` and this memory bank were added; `CLAUDE.md` only imports `AGENTS.md`, so every agent rule lives in `AGENTS.md`.

## Recent decisions

- Barge-in builds on `EchoGuard`'s per-block judgement and adds sustained evidence, a 2 s cooldown, and hands the speech heard so far to the model as audio so the user need not repeat it.
- An interrupt only discards an answer that is actually in flight (including one split by a tool call); interrupting aborts the output stream instead of letting the device buffer play out.
- `.gitignore` comments sit on their own lines: trailing text had silently broken every secret rule.
- Agents commit only on the user's explicit request in the current message (`AGENTS.md` → *Commits*).

## Next steps

- Pick the next feature from the upstream "LVI+" roadmap (see `progress.md`).
