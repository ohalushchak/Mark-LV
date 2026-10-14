# Product context

Why the product behaves the way it does: the user experience the code is protecting. Feature-by-feature descriptions live in `readme.md` → *Capabilities* and *What's New*.

## Who uses it

One person at their own computer, talking to it. Speech is the primary interface; the HUD (`ui.py`) shows state, the activity log, settings drawers, camera and video. A phone dashboard (`dashboard/`) mirrors it over the local network.

## Experience principles

These decide most design arguments in the codebase:

- **Latency is the product.** Silence reads as broken. Slow tools announce themselves before they finish (video: "it answers before it opens"); one-shot model calls carry timeouts and a fallback ladder.
- **Act, then offer undo.** Asking "are you sure?" makes an assistant nobody uses. Reversible actions run immediately and push an undo; only irreversible ones get a UI confirmation the model cannot forge.
- **It must not hear itself.** Speaker echo, video soundtracks and the reply tail are all filtered so the assistant never answers its own voice.
- **Zero cost when off.** Optional features (wake word, voice barge-in, plugins) import nothing and spend nothing until enabled.
- **Say it plainly.** When something degrades (reconnect, mic muted for a video, plugin failed) the activity log says so in a sentence instead of failing silently.
- **Every token sent to the model on connect is paid every session.** Tool declarations and `core/prompt.txt` stay short; bundled skills are limited to ones that drive the computer.
