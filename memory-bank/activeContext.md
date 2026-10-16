# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-16_

## Current focus

- Computer use with verified results, merged into `main` from `feature/verified-computer-use` (stages 1–5):
  1. **Verdicts:** an action may declare `TOOL["snapshot"]` / `TOOL["verify"]`; `core/action_loader.py` runs them around the handler and appends `[VERIFIED]` / `[UNVERIFIED]` / `[FAILED]` (contract in `core/verify.py`, observations in `core/desktop_state.py`). The prompt tells the model to speak from the verdict.
  2. **Desktop checks:** `open_app`, `computer_control`, `computer_settings`, `file_controller`.
  3. **Browser checks:** before/after page state in the Playwright window; the real browser's address bar read through UI Automation.
  4. **Finding and clicking:** `screen_click` by UI Automation name first, then vision with a second close look, on the chosen monitor, refusing ambiguous or unlike matches; `screen_process` got `monitor`.
  5. **Computer use:** `computer_use` → `core/computer_use.py`, the look → plan → act → final-check loop in the background, with a pink ring on the hologram.
  - Plus: messages are drafted and sent only after the user's yes (`send_message`, `core/send_gate.py`), and `file_controller empty_trash` behind CONFIRM.
- Tried live on Windows across eight sessions (2026-10-16), with fixes after each:
  - **Worked:** opening apps, volume/mute read-back, window actions on a named window, Cyrillic and long text in Notepad and Slack under a Ukrainian layout, Slack open / draft / send with confirmation, channels by name, the computer-use loop in Notepad and Windows Settings.
  - **Found by the checks themselves:** the newer pycaw had silently broken `volume_get` / `volume_set` (it pressed mute twice and reported the volume set); "mute" toggled; Win+Down only restored a maximised window; `pyautogui.typewrite` dropped Cyrillic and typed US-layout keys; the Alt tap used to bring a window forward opened menus.
  - **Messages that went wrong before the send gate:** two Slack messages went to channels the user did not mean from blind clicks and Enters — the reason for draft-then-send.
  - **Not yet seen live:** `empty_trash` with CONFIRM, the computer-use pause before a risky step, Discord/Telegram/Teams conversation jumps.

## Recent decisions

- The mix, and what was left out:
  - **Base:** bfrpaulondev/Mark-BP's before/after postconditions and its computer-use loop (window-scoped vision, batching only what needs no new look, stale-screen check, bounded recovery, UIA first).
  - **Mixed in:** syamsulmuiin's "anything without evidence is unverified"; BlackStarG5's observed page state after browser actions; rilzisheree's coordinate proof and yes/no/unknown verdict where unknown fails; JishnuDevTech's re-locate-each-time loop and confidence gate (via ambiguity refusal).
  - **Rejected:** bfrpaulondev's provider router, OpenAI planner, cost telemetry and source-string tests; its Ctrl+L / Ctrl+C URL reading (steals focus and clipboard); its safety that judged only the model's own description of an action; JishnuDevTech's "any pixel change is success"; Robin's browser bridge with an open CORS port.
- Verification lives in each action module (self-describing, like `probe`), not in a central table. "Nothing changed" after a click is UNVERIFIED, not FAILED.
- Sending from the user's accounts is always asked: the first call registers exactly what goes where; the follow-up call (`confirmed=true`) goes through only if the user spoke after the question (`core/activity.py`) — or the user presses CONFIRM on the HUD. A dictated message is a draft left in the box until the user says to send it.
- Computer use: risky = what the action really touches (the UIA name under the point, a message box Enter would send, Delete keys), not the model's words; moving the mouse stops it; it goes through the same verified actions as the direct tools.
- Typing on Windows goes through the clipboard (restored afterwards); pyautogui's letter keys are pinned to layout-free key codes.
- Windows Settings windows belong to ApplicationFrameHost and carry localized titles, so a named window that is not found falls back to the window in front.

## Next steps

- Try live: "empty the Recycle Bin" (CONFIRM), a computer-use run that reaches a risky button (the pause and the voice yes), a cleanup in Settings through computer use.
- A repeat of the same tool call after "Server reports interruption" typed text twice in Slack (`progress.md` → Known issues); decide how to tell a repeat from a new request.
- Gemini vision quota: on this key 3.6-flash / 3.5-flash run out and flash-latest times out, so screen_click and computer use fall to the Live rung and take 10–60 s.
- Carried over: 1008 disconnects (GoAway not answered; "operation aborted" ~150 s after `audio_stream_end` while asleep); `health.failed` in the actions' broad `except` blocks; the self-repair restart/rollback steps not yet seen live; `yt-dlp` missing on this machine.
