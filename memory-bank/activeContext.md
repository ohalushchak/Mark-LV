# Active context

What is being worked on right now. Rewrite this file (do not append) whenever the focus moves; history belongs in git and in `progress.md`.

_Last updated: 2026-10-21_

## Current focus

- Messengers and calls, merged into `main` (stages 1–4 of the plan; Viber chats without a number added after on `feature/viber-by-name`; details in `progress.md` → *Done*, the design in ADR-025 – ADR-027). Tried live with a real contact on Windows:
  - **Worked:** drafts and sends in Viber after the yes (also when the model passed confirmed=true too early); Telegram drafts; Viber dial, decline, accept, hang up; Telegram dial and hang up; the assistant asleep during a call; `read_messages` in Viber and Telegram.
  - **Fixed after live findings:** Viber's call button opens a menu (the free call is picked, never Viber Out); Telegram asks "Start Call"; Viber's call windows have no UIA (colour); Viber records from the webcam mic (audio capture sessions); a COM double release killed the process; a contact's Telegram name differs from the spoken one.

## Recent decisions

- The user's rule (2026-10-21): messages and calls are prepared, and go out only after their confirmation; declining an incoming call needs none. Accepting one asks.
- Taken from forks: borgelevisberg17's fail-closed UI rules, Nathan123y's contact lookup and inbox-watch policies, Kira's ambiguity margin, JAlmuscaty's call hold and audio-session call detection, Blobby132's no-message-text logging. Not taken: Kira's WA-JS injection, JAlmuscaty's vision clicks and auto-decline loop, Nathan123y's chat takeover and Twilio line, rilzisheree's escalation calls (ADR-025 – ADR-027).
- Viber chats open by phone number only: Viber shows UI Automation no names.
- New messages come from Windows' notification database, not screenshots; only chats the user named are announced.

## Next steps

- Try live: a message announcement from a watched chat (through Phone Link), a Slack huddle, a Phone Link call, an incoming Telegram call.
- Stage 5 (the user chose it): the assistant speaks into a call — the user is installing VB-CABLE; see `progress.md` → *Left to build*.
- Parked by the user: replacing Gemini Live (Claude Code / Codex as the brain, local speech). Everything gathered is in `docs/research/gemini-alternatives/README.md`.
- Upstream moved to `FatihMakes/Mark-LVI`; compare with this fork when there is time.
- Carried over: a repeated tool call after "Server reports interruption"; Gemini vision quota (10–60 s); 1008 disconnects (GoAway not answered); `health.failed` in the actions' broad `except` blocks; self-repair restart/rollback not yet seen live.
