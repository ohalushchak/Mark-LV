"""
actions/ask_chat_app.py — hand a question to the user's ChatGPT or Claude app.

From the Nathan123y/Jarvis-for-Nathan fork's ask_chatgpt / ask_claude: the
question goes on the clipboard and the app (or its website) opens; the user
pastes and sends it. A subscription exposes no API another desktop program
could use, so this deliberately stops there and says so — it never claims to
have sent the question or read the answer.

Changed for Windows, where this fork runs: both apps here are Microsoft Store
packages, found by name in `Get-StartApps` and launched through
shell:AppsFolder; the clipboard is written through the Win32 API (any thread,
no Qt, no extra package). macOS uses `open -a` and pbcopy; elsewhere, the
website and pyperclip.
"""

from __future__ import annotations

import platform
import re
import subprocess
import webbrowser

_OS = platform.system()

APPS = {
    "chatgpt": {"label": "ChatGPT", "url": "https://chatgpt.com/",
                "names": ("ChatGPT", "ChatGPT Classic"), "mac": "ChatGPT"},
    "claude": {"label": "Claude", "url": "https://claude.ai/new",
               "names": ("Claude",), "mac": "Claude"},
}
_MAX_CHARS = 20_000
# Words that make a request a coding job for a project rather than a chat
# question. In the first live test, "hand to Claude: add a subtract function to
# calc.py" and "pass it to Codex: run the tests" both came here instead of to
# delegate_to_agent, and the user was told the answer could not be read back.
_CODING = re.compile(r"(\b[\w-]+\.(py|js|ts|tsx|jsx|json|md|txt|toml|yaml|yml|cs|java|go|rs|cpp|c|h|html|css)\b"
                     r"|\b(функці\w*|тест\w*|код\w*|коміт\w*|проєкт\w*|проект\w*|репозитор\w*|"
                     r"рефактор\w*|баг\w*|function\w*|tests?\b|code\b|commit\w*|repo\w*|"
                     r"refactor\w*|bug\w*|project\w*|codex|claude code))", re.IGNORECASE)
_start_apps: dict[str, str] | None = None


def _copy(text: str) -> bool:
    if _OS == "Windows":
        return _copy_windows(text)
    if _OS == "Darwin":
        try:
            subprocess.run(["pbcopy"], input=text, text=True, check=True,
                           capture_output=True, timeout=5)
            return True
        except Exception:
            return False
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def _copy_windows(text: str) -> bool:
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    data = text.encode("utf-16-le") + b"\0\0"
    for _ in range(10):                      # another app may hold it briefly
        if user32.OpenClipboard(None):
            break
        import time
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0002, len(data))      # GMEM_MOVEABLE
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(13, handle))      # CF_UNICODETEXT
    finally:
        user32.CloseClipboard()


def _windows_app_id(names: tuple[str, ...]) -> str:
    """The Start-menu AppID of the first installed app with one of `names`."""
    global _start_apps
    if _start_apps is None:
        _start_apps = {}
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-StartApps | ForEach-Object { $_.Name + \"`t\" + $_.AppID }"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in r.stdout.splitlines():
                name, _, app_id = line.partition("\t")
                if name.strip() and app_id.strip():
                    _start_apps.setdefault(name.strip().lower(), app_id.strip())
        except Exception:
            pass
    for n in names:
        if n.lower() in _start_apps:
            return _start_apps[n.lower()]
    return ""


def _open(app: dict) -> str:
    """'app', 'web' or '' — how it was opened."""
    if _OS == "Windows":
        app_id = _windows_app_id(app["names"])
        if app_id:
            try:
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
                return "app"
            except OSError:
                pass
    elif _OS == "Darwin":
        try:
            subprocess.run(["open", "-a", app["mac"]], check=True,
                           capture_output=True, timeout=8)
            return "app"
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        return "web" if webbrowser.open(app["url"]) else ""
    except Exception:
        return ""


def ask_chat_app(parameters: dict, player=None) -> str:
    p = parameters or {}
    key = str(p.get("app") or "chatgpt").strip().lower().replace(" ", "")
    app = APPS.get({"gpt": "chatgpt", "openai": "chatgpt"}.get(key, key))
    if app is None:
        return "I can hand questions to ChatGPT or Claude."
    question = str(p.get("question") or "").strip()
    if not question:
        return f"What should I ask {app['label']}?"
    if _CODING.search(question) and not p.get("confirm_chat_app"):
        return ("[WRONG_TOOL] Nothing was opened. This reads like a coding job for the "
                "project, which the coding agent does: call delegate_to_agent with "
                "action='ask' and this task (engine claude for 'Claude'/'Claude Code', "
                "codex for 'Codex'/'ChatGPT'). Only if the user clearly wants the chat "
                "app itself, call ask_chat_app again with confirm_chat_app=true.")
    if len(question) > _MAX_CHARS:
        return f"That question is too long to hand over; shorten it."
    if not _copy(question):
        return f"I could not put the question on the clipboard, so I did not open {app['label']}."
    how = _open(app)
    if not how:
        return (f"I copied the question, but could not open {app['label']}. The user can "
                f"open it and paste.")
    if player is not None:
        try:
            player.write_log(f"SYS: Opened {app['label']} with the question on the clipboard")
        except Exception:
            pass
    where = "app" if how == "app" else "website"
    return (f"I opened the {app['label']} {where} and copied the question. Tell the user "
            f"to paste it (Ctrl+V) and send it there; you cannot read {app['label']}'s "
            f"answer back.")


TOOL = {
    "name": "ask_chat_app",
    "description": (
        "Opens the ChatGPT or Claude CHAT app with a general question on the "
        "clipboard, for the user to paste and send themselves — only when they "
        "explicitly want to ask the chat app ('ask ChatGPT how to cook borscht', "
        "'open Claude and ask …'). It cannot send the question or read the "
        "answer. NOT for coding: any request to have Claude, Claude Code or "
        "Codex do work on code, files, tests or a project ('pass to Claude: add "
        "a function', 'let Codex run the tests') goes to delegate_to_agent. "
        "Ordinary questions are answered directly."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app": {"type": "STRING", "description": "chatgpt (default) or claude"},
            "question": {"type": "STRING", "description": "The user's exact question"},
            "confirm_chat_app": {"type": "BOOLEAN",
                                 "description": "Only after a [WRONG_TOOL] reply, when the "
                                                "user really wants the chat app"},
        },
        "required": ["question"],
    },
    "handler": ask_chat_app,
}
