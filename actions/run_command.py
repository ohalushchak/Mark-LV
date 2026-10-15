"""
actions/run_command.py — run a terminal command in the current project.

WHAT HAPPENS TO A COMMAND
    core/shell_policy.py sorts it into three tiers:
      SAFE     (git status, dir, pytest, npm test, --version …) runs at once
      CONFIRM  (installs, deletes, scripts, anything unknown) waits for the
               HUD's CONFIRM button — the model cannot press it
      BLOCKED  (format, recursive delete of a drive, git reset --hard, force
               push, encoded or downloaded-and-piped scripts) is refused

    It runs through PowerShell on Windows (pwsh 7 when installed), bash or sh
    elsewhere, in the current project folder (core/projects.py), with no
    console window and stdin closed. The tool waits up to ten seconds; a
    command still running then becomes a background job (core/jobs.py) whose
    result is announced when it ends (core/announce.py), so the user can keep
    talking. A confirmed command is always announced that way.

    action="terminal" opens a real terminal window instead, so the user can
    watch an install or answer an interactive prompt — the idea of the
    deepakchouguledatascience/Mark-LIII fork's terminal tool. The same tiers
    apply: being visible does not make a command safer.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import threading
from pathlib import Path

from core import announce, jobs, projects, shell_policy

_OS = platform.system()

INLINE_WAIT_S = 10.0
DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 1800
_OUTPUT_CHARS = 3000
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def shell_argv(command: str) -> list[str]:
    """argv that runs `command` in this OS's shell."""
    if _OS == "Windows":
        exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        # UTF-8 out, and no progress bars: they are drawn with control
        # characters that would end up in the output read back to the model.
        prefix = ("$ProgressPreference='SilentlyContinue'; "
                  "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
        return [exe, "-NoProfile", "-NonInteractive", "-Command", prefix + command]
    sh = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
    return [sh, "-c", command]


def _log(player, text: str) -> None:
    if player is not None:
        try:
            player.write_log(text)
        except Exception:
            pass


def _where(cwd: Path) -> str:
    home = Path.home()
    return "your home folder" if cwd == home else cwd.name


def report(job: jobs.Job) -> str:
    """The finished job as text for the model."""
    if job.error:
        head = f"`{job.label}` could not run: {job.error}"
    elif job.cancelled:
        head = f"`{job.label}` was stopped after {job.duration} s"
    elif job.timed_out:
        head = f"`{job.label}` was stopped: it ran past its {int(job.timeout)} s limit"
    else:
        verb = "finished" if job.exit_code == 0 else "failed"
        head = f"`{job.label}` {verb} with exit code {job.exit_code} after {job.duration} s"
    out = _ANSI.sub("", job.text()).strip()
    if len(out) > _OUTPUT_CHARS:
        out = "…" + out[-_OUTPUT_CHARS:]
        head += " (output trimmed to its end)"
    elif job.truncated:
        head += " (only the end of the output was kept)"
    body = f"\nOutput:\n{out}" if out else "\nNo output."
    return f"{head} in {job.cwd}.{body}"


def _finished_line(job: jobs.Job) -> str:
    mark = "✓" if job.ok else "✗"
    code = "stopped" if job.cancelled else ("timed out" if job.timed_out
                                            else f"exit {job.exit_code}")
    return f"SYS: {mark} {job.label[:70]} — {code}, {job.duration} s"


def _start(command: str, cwd: Path, timeout: int, player, background: bool):
    """Start the job. Returns (job, state) where state tells the done-callback
    whether the result still has to be announced."""
    state = {"mode": "background" if background else "inline"}
    lock = threading.Lock()

    def done(job: jobs.Job):
        _log(player, _finished_line(job))
        with lock:
            if state["mode"] != "background":
                state["mode"] = "finished"     # the tool reports it itself
                return
        if job.cancelled:
            return      # the user stopped it; action="stop" already said so
        announce.say(
            f"[COMMAND_FINISHED] {report(job)}\n\nTell the user in one or two short "
            f"sentences how it went (the outcome first, no code or long paths).",
            note=f"{job.label[:60]} — {'done' if job.ok else 'failed'}")

    job = jobs.start(shell_argv(command), cwd=str(cwd), timeout=timeout,
                     label=command, kind="command", on_done=done)
    return job, state, lock


def _run_now(command: str, cwd: Path, timeout: int, player) -> str:
    try:
        job, state, lock = _start(command, cwd, timeout, player, background=False)
    except RuntimeError as e:
        return f"I did not run it: {e}"
    jobs.wait(job, INLINE_WAIT_S)
    with lock:
        if job.finished:
            state["mode"] = "finished"
            return report(job)
        state["mode"] = "background"     # from now on the callback announces it
    return (f"[JOB_RUNNING] `{command}` is still running in {_where(cwd)} (job "
            f"{job.id}). Its result will be announced when it finishes. Tell the "
            f"user in a few words that it is running; do not wait for it.")


def _open_terminal(command: str, cwd: Path) -> str:
    """A visible terminal in `cwd`, running `command` if one is given."""
    try:
        if _OS == "Windows":
            exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            argv = [exe, "-NoExit"] + (["-Command", command] if command else [])
            # A new console of its own. On Windows 11 the default terminal
            # application (Windows Terminal) hosts it, so there is no need to
            # build wt.exe's own command line, which treats ';' as a separator.
            subprocess.Popen(argv, cwd=str(cwd),
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif _OS == "Darwin":
            script = f"cd {_sh_quote(str(cwd))}" + (f"; {command}" if command else "")
            apple = script.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e",
                              f'tell application "Terminal" to do script "{apple}"',
                              "-e", 'tell application "Terminal" to activate'])
        else:
            term = next((t for t in ("x-terminal-emulator", "gnome-terminal", "konsole",
                                     "xfce4-terminal", "xterm") if shutil.which(t)), None)
            if term is None:
                return ("No terminal program was found on this computer. I can run "
                        "the command in the background and read you the result instead.")
            inner = (command + "; " if command else "") + "exec bash"
            flag = "--" if term == "gnome-terminal" else "-e"
            subprocess.Popen([term, flag, "bash", "-c", inner], cwd=str(cwd),
                             start_new_session=True)
    except Exception as e:
        return f"I could not open a terminal: {e}"
    if command:
        return (f"Opened a terminal in {_where(cwd)} running `{command}`. The user "
                f"can watch it there; its output does not come back to me.")
    return f"Opened a terminal in {_where(cwd)}."


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _status(job_id: str) -> str:
    job = jobs.get(job_id) if job_id else jobs.latest("command")
    if job is None:
        return "No command has been run in this session." if not job_id \
            else f"There is no job {job_id} in this session."
    if job.finished:
        return report(job)
    tail = _ANSI.sub("", job.text()).strip()[-600:]
    return (f"`{job.label}` (job {job.id}) is still running, {job.duration} s so far."
            + (f" Latest output:\n{tail}" if tail else " No output yet."))


def _stop(job_id: str) -> str:
    if job_id:
        job = jobs.get(job_id)
    else:
        live = jobs.running("command")
        job = max(live, key=lambda j: j.started) if live else None
    if job is None:
        return "No command is running."
    if not jobs.stop(job):
        return f"`{job.label}` had already finished."
    return f"Stopped `{job.label}` and everything it had started."


def run_command(parameters: dict, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "run").strip().lower()
    command = str(p.get("command") or "").strip()
    job_id = str(p.get("job_id") or "").strip()

    if action == "status":
        return _status(job_id)
    if action == "stop":
        return _stop(job_id)
    if action not in ("run", "terminal"):
        return "Unknown action. Use run, terminal, status or stop."

    project = str(p.get("project") or "").strip()
    if project:
        cwd, problem = projects.resolve(project)
        if cwd is None:
            return problem
    else:
        cwd = projects.current() or Path.home()

    if action == "terminal" and not command:
        return _open_terminal("", cwd)
    if not command:
        return "Tell me the exact command to run."

    try:
        timeout = int(p.get("timeout_seconds") or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_S
    timeout = max(5, min(timeout, MAX_TIMEOUT_S))

    tier, why = shell_policy.classify(command)
    _log(player, f"SYS: $ {command[:120]}  [{tier}]")
    if tier == shell_policy.BLOCKED:
        return f"I refused to run `{command}`: {why}. Nothing was run."

    if tier == shell_policy.SAFE:
        return _open_terminal(command, cwd) if action == "terminal" \
            else _run_now(command, cwd, timeout, player)

    from core import confirm
    if confirm.pending_title():
        return ("A confirmation is already waiting on the HUD. Ask the user to answer "
                "that one first; nothing was run.")
    where = "a visible terminal" if action == "terminal" else "the background"
    detail = (f"{command}\n\nFolder: {cwd}\nWhy it asks: {why}.\n"
              f"Runs in {where} once you confirm.")

    def go() -> str:
        if action == "terminal":
            return _open_terminal(command, cwd)
        try:
            job, _state, _lock = _start(command, cwd, timeout, player, background=True)
        except RuntimeError as e:
            announce.say(f"[COMMAND_FINISHED] The confirmed command `{command}` could "
                         f"not start: {e}. Tell the user briefly.")
            return f"could not start: {e}"
        return f"started as job {job.id}"

    return confirm.request("run_command", f"Run: {command[:60]}", detail, go)


TOOL = {
    "name": "run_command",
    "description": (
        "Runs a terminal command on the user's computer, in the current project "
        "folder (PowerShell on Windows). Use when the user asks to run, build, "
        "test, install or check something in a terminal: 'run the tests', 'build "
        "it', 'what version of node do I have', 'install requests'. Read-only "
        "commands and test/build runs start at once; anything that installs, "
        "deletes or changes things waits for the user to press CONFIRM on the "
        "HUD; destructive ones are refused. A command still running after ten "
        "seconds continues in the background and its result is announced later. "
        "action='terminal' opens a visible terminal window instead (for "
        "interactive commands or when the user wants to watch). action='status' "
        "or 'stop' checks or stops a running command. An exit code of 0 means "
        "the command ended without error, not that the goal was met. For git "
        "use git_project; for reading or editing project files use "
        "project_workspace; for a large coding job use delegate_to_agent."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "description": "run (default) | terminal | status | stop"},
            "command": {"type": "STRING",
                        "description": "The exact command line, in the syntax of this OS's shell"},
            "project": {"type": "STRING",
                        "description": "Project name or folder; omit to use the current project"},
            "timeout_seconds": {"type": "INTEGER",
                                "description": "Stop the command after this long (default 120, max 1800)"},
            "job_id": {"type": "STRING",
                       "description": "For status/stop: which job (default: the latest)"},
        },
    },
    "handler": run_command,
}
