# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-21_

## Current focus

- Memory in a database with search by meaning, on `feature/semantic-memory` (stages 1–5 plus note editing; details in `progress.md` → *Done*, the design in `systemPatterns.md` → *Memory*). Tried live in five sessions on Windows:
  - **Worked:** migration of the real `long_term.json` (5 facts, 3 summaries); facts saved in Ukrainian and recalled in either language ("children" found both sons); forget with undo; conversation recall; notes search, read and open; adding to the to-do list under a heading, removing an item, creating a note and undoing it.
  - **Fixed after live findings:** a short "my brother's name is …" saved as a suggestion because the transcript arrived after the tool call (3 s recheck); opening a project by description opened a file three folders deep (folder-note rule); a question ("what's in my notes about <project>?") backed a fact the model made from search results (questions excluded); "To Do List" vs "ToDo list"; tasks under the note's title landed in the last subsection; deleting a line left a blank line.

## Recent decisions

- The user chose: the best embedding model regardless of disk (EmbeddingGemma, measured against e5-large, Qwen3-0.6B, MiniLM); conversation summaries only, no transcripts; the Obsidian stage now; facts stored in the words the user said them in.
- Vectors live in SQLite next to the rows, scanned with numpy behind `VectorIndex` — Chroma / LanceDB / Qdrant were weighed and left out at this scale (the user asked; one transaction per fact + vector + FTS is the reason).
- Not taken from forks: Supabase/Postgres, maseko's Rust wheel, TeddyDARKVADOR's unencrypted OneDrive sync and words-only provenance gate, regex rule detectors, auto-saving tool results, omygod9138's ban on "language" keys.
- Notes edits act at once with undo (the convention for state-changing actions); undo refuses if the note changed since.

## Next steps

- Parked by the user: replacing Gemini Live (Claude Code / Codex as the brain, local speech). Everything gathered is in `docs/research/gemini-alternatives/README.md`; resume from its *Plan when resuming*.
- Upstream moved to `FatihMakes/Mark-LVI` (a new release); compare with this fork when there is time.
- Try live: "empty the Recycle Bin" (CONFIRM), a computer-use run that reaches a risky button, a cleanup in Settings through computer use.
- A repeat of the same tool call after "Server reports interruption" typed text twice in Slack (`progress.md` → Known issues); decide how to tell a repeat from a new request.
- Gemini vision quota: on this key 3.6-flash / 3.5-flash run out and flash-latest times out, so screen_click and computer use fall to the Live rung and take 10–60 s.
- Carried over: 1008 disconnects (GoAway not answered; "operation aborted" ~150 s after `audio_stream_end` while asleep — seen again at every start on 2026-10-21); `health.failed` in the actions' broad `except` blocks; the self-repair restart/rollback steps not yet seen live; `yt-dlp` missing on this machine.
