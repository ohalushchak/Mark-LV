# AGENTS.md

MARK LIV/LV ("JARVIS"): a cross-platform desktop voice assistant. `main.py` runs the Gemini Live session, audio I/O and tool dispatch; `ui.py` is the PyQt6 HUD. The file-by-file map lives in `readme.md` → *Project Structure*; setup and run commands live in `readme.md` → *Quick Start*.

## Memory bank

`memory-bank/` is the project's persistent context across agent sessions. It is unrelated to `memory/`, which is the assistant's own runtime memory.

| File | Holds |
|---|---|
| `projectbrief.md` | What the repo is, fork ownership, goals |
| `productContext.md` | Experience principles behind design decisions |
| `systemPatterns.md` | Runtime architecture: threads, audio path, dispatch, persistence |
| `techContext.md` | Stack, environment facts, constraints |
| `activeContext.md` | Current focus, recent decisions, next steps |
| `progress.md` | Done, left to build, known issues |

**At the start of a task**, read `activeContext.md` and `progress.md`; read the other files when the task touches what they hold.

**Before every commit**, actualize the memory bank:

1. Review the staged diff against each memory-bank file.
2. Update every file whose facts the commit changes: `activeContext.md` and `progress.md` nearly always; the others when the commit changes architecture, stack, goals or principles.
3. Rewrite `activeContext.md` to the present state and refresh its `_Last updated_` date; move finished items in `progress.md` to *Done*, and add new known issues.
4. Delete lines that went stale, and keep each fact in one file: point to `readme.md`, `AGENTS.md` or the code instead of copying them.
5. Stage the memory-bank changes in the same commit.

The commit is ready when every memory-bank statement matches the code as committed.

## Setup and tests

- Python 3.11–3.13. Install with `python setup.py`: it picks only this OS's packages via the `sys_platform` markers in `requirements.txt`. A new OS-specific dependency gets a marker there too.
- Tests: `python -m pytest tests` from the repo root. Tests import `main` and `core.*` directly and run headless, so feed synthetic audio and stub the UI the way `tests/test_interrupt_flow.py` does (`_UI` with `write_log`/`set_state`).
- The real app needs a microphone, speakers and a Gemini key in `config/api_keys.json`; a fresh clone without that file boots into the setup screen.

## Adding a skill (tool the model can call)

Every tool self-describes; `main.py` never gets a hardcoded declaration or dispatch branch.

- **Bundled action** → `actions/<name>.py` with a module-level `TOOL` dict (`name`, `description`, `parameters` with `"type": "OBJECT"`, `handler`). Live example: `actions/open_app.py`. Contract: `core/action_loader.py` docstring. The handler declares only the context kwargs it needs (`player`, `speak`, `response`, `session_memory`).
- **Drop-in plugin** → copy `plugins/_template.py` to a name without a leading `_`; fill `PLUGIN` and `run()`. Plugins can be toggled at runtime and may declare `PLUGIN_SETTINGS` for a config form.
- A bundled action must **drive the computer** (apps, files, browser, desktop, screen). Hobby- or trade-specific skills go in `plugins/`.
- Files starting with `_` are helpers and are never discovered as tools.
- Tool names are unique across inline tools in `main.py` (`TOOL_DECLARATIONS`), actions and plugins; a collision is rejected at startup with only a log line, so check the console after adding one.
- The `description` is the routing signal the model reads. Name the trigger phrases, and name the neighbouring tool it could be confused with.
- A slow tool sets `behavior: "NON_BLOCKING"` plus a `scheduling` of `WHEN_IDLE` / `SILENT` / `INTERRUPT` (meanings in `core/action_loader.py`).
- Handlers return a short spoken sentence and catch their own errors, returning them as a spoken string. Log to the HUD with `player.write_log(f"JARVIS: ...")`, guarded because `player` may be `None`.

## Conventions

- **Gemini calls**: every one-shot call goes through `core/gemini.py` (`call` / `text` / `as_json` with a `FAST`, `SMART` or `SEARCH` tier). It owns the model ladder, timeouts and cooldowns. Model names live only there; the sole direct `genai.Client` is the Live session in `main.py`. Where an answer depends only on stable input, cache it.
- **State-changing actions**: act immediately and register the reversal with `core.undo.push_undo(label, callable)` (see `actions/file_controller.py`). Reserve `core/confirm.py` for genuinely irreversible actions (shutdown, permanent delete); its token comes from the UI button, so a model-written `confirmed=yes` parameter is never a gate.
- **Prompt**: all wording lives in `core/prompt.txt`. Keep its `{tokens}`, which are substituted with plain `replace` at startup. Keep it short: routing rules belong in each tool's description, and the prompt carries only rules written nowhere else.
- **Config and memory**: read and write settings through `memory/config_manager.py` and the long-term store through `memory/memory_manager.py`, never by opening the JSON files directly.
- **Cross-platform**: branch on `platform.system()` (`"Windows"` / `"Darwin"` / `"Linux"`) inside the action, with a working path or a spoken explanation for each OS.
- **Threads**: the Live session runs on asyncio, the HUD on the Qt thread. Cross into the UI through its signals or `write_log`, and run blocking work off both loops.
- **Comments**: the codebase explains *why* in docstrings and block comments (measurements, past failures, trade-offs). Write new ones in the same voice, beside the code they justify.

## Secrets and personal data

`.gitignore` lists what must never be committed: `config/api_keys.json`, `config/certs/`, `config/whatsapp_web/`, OAuth tokens and client secrets, `memory/long_term.json`, `memory/*.db`. Before committing, confirm `git status` shows none of them. New credential or personal-data files get a `.gitignore` entry, with its comment on its own line above the pattern (git treats trailing text as part of the pattern).
