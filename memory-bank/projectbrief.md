# Project brief

The foundation document: what this repository is and what it is for. Changes rarely; every other memory-bank file builds on it.

## What it is

MARK LIV/LV ("JARVIS") is a cross-platform (Windows, macOS, Linux) desktop voice assistant: it hears, sees the screen and webcam, speaks through a holographic avatar, and controls the computer through tools. The conversation runs on the Gemini Live API with native audio.

## Ownership

- Upstream: `FatihMakes/Mark-LV` (remote `upstream`). The author ships numbered "Marks"; the roadmap table in `readme.md` lists them.
- This repo: the fork `ohalushchak/Mark-LV` (remote `origin`). Work lands on `main` through feature branches (`feature/<name>`), merged with a merge commit.
- `readme.md` is the upstream author's release document. Fork-specific work is recorded here in the memory bank.

## Goals of the fork

- Add features from the upstream roadmap ("LVI+") and from other forks.
- Keep the upstream design principles intact: no subscriptions, no GPU, every OS supported, secrets never committed.
