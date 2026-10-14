# Progress

What works, what is left, and known issues. Upstream releases up to Mark LV are described in `readme.md`; this file tracks the fork from there.

_Last updated: 2026-10-14_

## Done in this fork

- Voice barge-in, opt-in (`core/barge_in.py`, tests in `tests/test_barge_in.py`, `tests/test_interrupt_flow.py`).
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

## Known issues

- Voice barge-in is unverified on real hardware; thresholds may need tuning per room.
