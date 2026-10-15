"""
actions/git_project.py — everyday git in the current project, by voice.

FROM THE BlackStarG5/Mark-LIV FORK, AND WHAT CHANGED
    Kept: a fixed set of operations, each a fixed argv (no shell string), git
    and gh never allowed to prompt, a commit of named files that leaves other
    staged work alone (--only), pull only as a fast-forward, PRs as drafts, and
    no force push, reset, clean or branch deletion at all.
    Changed:
      * The repository is the current project (core/projects.py) or any folder
        inside it — the fork demanded the exact top-level path on every call.
      * push and pr_create wait for the HUD's CONFIRM button: they publish, and
        a push cannot be taken back. Their outcome is announced when it is in.
      * commit, branch and switch go on the undo stack instead: an unwanted
        commit is `reset --soft` away, an unwanted switch is a switch back.
      * commit "all" stages tracked changes only, names the untracked files it
        left out, and refuses outright when a key file (api_keys.json, .env,
        id_rsa…) would go in — the fork is public, the keys must not be.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from core import announce, jobs, projects

_DIFF_CHARS = 6000


def _env() -> dict:
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1",
            "GIT_PAGER": "cat", "PAGER": "cat", "GH_PAGER": "cat",
            "GIT_EDITOR": "true", "NO_COLOR": "1"}


def _run(argv: list[str], cwd: Path, timeout: float = 30) -> tuple[int, str]:
    if argv[0] == "git":
        argv = ["git", "-c", "core.quotepath=off", "-c", "color.ui=never", *argv[1:]]
    elif argv[0] == "gh":
        exe = shutil.which("gh")
        if not exe:
            return 127, ("The GitHub CLI (gh) is not installed. Local git works; "
                         "pull requests need gh, signed in with `gh auth login`.")
        argv = [exe, *argv[1:]]
    try:
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=timeout, env=_env(),
                              **jobs.hidden_window_flags())
    except FileNotFoundError:
        return 127, f"{argv[0]} is not installed or not on PATH."
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]} {argv[1] if len(argv) > 1 else ''} timed out after {int(timeout)} s."
    out = (jobs.decode(done.stdout) + jobs.decode(done.stderr)).strip()
    return done.returncode, out


def _git(cwd: Path, *args: str, timeout: float = 30) -> tuple[int, str]:
    return _run(["git", *args], cwd, timeout)


def _log(player, text: str) -> None:
    if player is not None:
        try:
            player.write_log(text)
        except Exception:
            pass


def _repo(project: str) -> tuple[Path | None, str]:
    folder, problem = projects.resolve(project)
    if folder is None:
        return None, problem
    code, top = _git(folder, "rev-parse", "--show-toplevel")
    if code != 0:
        return None, f"{folder.name} is not a git repository."
    return Path(top.strip()), ""


def _branch(root: Path) -> str:
    return _git(root, "branch", "--show-current")[1].strip()


def _upstream(root: Path) -> str:
    code, out = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    return out.strip() if code == 0 else ""


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit} more characters)"


# ── read-only ───────────────────────────────────────────────────────────────

def _status(root: Path) -> str:
    code, out = _git(root, "status", "--short", "--branch")
    if code:
        return f"git status failed: {out}"
    lines = out.splitlines()
    head, files = (lines[0] if lines else ""), lines[1:]
    if not files:
        return f"{root.name}: {head.lstrip('# ')} — nothing changed, the working tree is clean."
    return (f"{root.name}: {head.lstrip('# ')} — {len(files)} changed file(s) "
            f"(XY codes: M modified, A added, D deleted, ?? untracked):\n"
            + _cap("\n".join(files), 3000))


def _diff(root: Path, path: str, staged: bool) -> str:
    base = ["diff", "--no-ext-diff", "--no-textconv"] + (["--cached"] if staged else [])
    tail = ["--", path] if path else []
    _, stat = _git(root, *base, "--stat", *tail)
    if not stat.strip():
        what = "staged" if staged else "unstaged"
        return f"No {what} changes{' in ' + path if path else ''}."
    _, patch = _git(root, *base, *tail)
    return f"Summary:\n{stat}\n\nChanges:\n{_cap(patch, _DIFF_CHARS)}"


def _log_cmd(root: Path, count: int) -> str:
    code, out = _git(root, "log", f"-{count}", "--date=short",
                     "--pretty=format:%h %ad %an: %s")
    return out if code == 0 and out else (out or "No commits yet.")


def _branches(root: Path) -> str:
    code, out = _git(root, "branch", "--list", "-vv", "--sort=-committerdate")
    return out if code == 0 else f"Could not list branches: {out}"


# ── reversible changes ──────────────────────────────────────────────────────

def _valid_branch(root: Path, name: str) -> bool:
    if not name or name.startswith("-"):
        return False
    return _git(root, "check-ref-format", "--branch", name)[0] == 0


def _switch(root: Path, name: str, create: bool, player) -> str:
    if not _valid_branch(root, name):
        return f"'{name}' is not a valid branch name."
    before = _branch(root)
    code, out = _git(root, "switch", *(["-c"] if create else []), name)
    if code:
        return f"git switch failed: {out}"
    from core.undo import push_undo

    def undo() -> str:
        c, o = _git(root, "switch", before)
        return f"Switched back to {before}." if c == 0 else f"Could not switch back: {o}"
    if before:
        push_undo(f"switched {root.name} to branch {name}", undo)
    _log(player, f"SYS: git — {'created and switched to' if create else 'switched to'} {name}")
    return (f"{'Created and switched to' if create else 'Switched to'} branch {name} "
            f"in {root.name}" + (f" (was {before})." if before else "."))


def _commit(root: Path, message: str, paths: list[str], all_tracked: bool, player) -> str:
    message = message.strip()
    if not message:
        return "A commit needs a message. Ask the user what to call it, or propose one."
    if not paths and not all_tracked:
        return ("Say which files to commit, or commit all tracked changes "
                "(all=true). Untracked files are never added implicitly.")
    for p in paths:
        if not p or Path(p).is_absolute() or p.startswith(":") or ".." in Path(p).parts:
            return f"'{p}' is not a path inside the repository."
        if projects.is_secret(p):
            return f"I will not commit {p}: it looks like a file with keys or passwords."
    code, before = _git(root, "rev-parse", "HEAD")
    before = before.strip() if code == 0 else ""

    if paths:
        code, out = _git(root, "--literal-pathspecs", "add", "--", *paths)
        if code:
            return f"git add failed: {out}"
        names = paths
        # --only commits exactly these paths and leaves other staged work alone.
        code, out = _git(root, "--literal-pathspecs", "commit", "--only", "-m",
                         message, "--", *paths)
    else:
        _, names_out = _git(root, "diff", "--name-only", "HEAD") if before \
            else _git(root, "ls-files", "--modified")
        names = [n for n in names_out.splitlines() if n.strip()]
        _, staged_out = _git(root, "diff", "--cached", "--name-only")
        names += [n for n in staged_out.splitlines() if n.strip() and n not in names]
        bad = [n for n in names if projects.is_secret(n)]
        if bad:
            return (f"I did not commit: {', '.join(bad)} would go in, and that looks "
                    f"like a file with keys or passwords.")
        code, out = _git(root, "add", "-u")
        if code:
            return f"git add failed: {out}"
        code, out = _git(root, "commit", "-m", message)
    if code:
        if "nothing to commit" in out or "no changes added" in out:
            return "There was nothing to commit."
        return f"git commit failed: {out}"

    _, sha = _git(root, "rev-parse", "HEAD")
    sha = sha.strip()
    _, stat = _git(root, "show", "--stat", "--format=", "HEAD")
    summary = stat.strip().splitlines()[-1] if stat.strip() else ""
    _, untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    left_out = [u for u in untracked.splitlines() if u.strip()]

    from core.undo import push_undo

    def undo() -> str:
        c, head = _git(root, "rev-parse", "HEAD")
        if c or head.strip() != sha:
            return "The commit is no longer the latest one, so I left history alone."
        c, o = (_git(root, "reset", "--soft", "HEAD~1") if before
                else _git(root, "update-ref", "-d", "HEAD"))
        return ("Commit undone; its changes are still there, staged."
                if c == 0 else f"Could not undo the commit: {o}")
    push_undo(f"commit '{message[:40]}' in {root.name}", undo)
    _log(player, f"SYS: git — committed {sha[:7]} “{message[:60]}”")
    reply = f"Committed {sha[:7]} “{message}” on {_branch(root) or 'a detached HEAD'}"
    reply += f" — {summary}." if summary else "."
    if left_out and not paths:
        shown = ", ".join(left_out[:8]) + (" …" if len(left_out) > 8 else "")
        reply += f" Untracked files were left out: {shown}."
    return reply


# ── network ─────────────────────────────────────────────────────────────────

def _fetch(root: Path) -> str:
    code, out = _git(root, "fetch", "--prune", timeout=120)
    if code:
        return f"git fetch failed: {out}"
    _, st = _git(root, "status", "--short", "--branch")
    return f"Fetched. {(st.splitlines() or [''])[0].lstrip('# ')}"


def _pull(root: Path) -> str:
    code, out = _git(root, "pull", "--ff-only", timeout=120)
    if code:
        if "Not possible to fast-forward" in out or "diverged" in out:
            return ("Pull stopped: the branch has diverged from the remote, so it "
                    "cannot fast-forward. Merging or rebasing is the user's call.")
        return f"git pull failed: {out}"
    return _cap(out or "Already up to date.", 1500)


def _push(root: Path, player) -> str:
    branch = _branch(root)
    if not branch:
        return "HEAD is detached; switch to a branch before pushing."
    up = _upstream(root)
    if up:
        _, ahead = _git(root, "log", "--oneline", f"{up}..HEAD")
        commits = [c for c in ahead.splitlines() if c.strip()]
        if not commits:
            return f"{branch} has nothing to push; it matches {up}."
        argv, where = ["push"], up
    else:
        _, remotes = _git(root, "remote")
        if "origin" not in remotes.split():
            return "This repository has no 'origin' remote to push to."
        _, ahead = _git(root, "log", "--oneline", "-20", "HEAD", "--not", "--remotes")
        commits = [c for c in ahead.splitlines() if c.strip()]
        argv, where = ["push", "--set-upstream", "origin", "HEAD"], f"origin/{branch} (new)"

    from core import confirm
    if confirm.pending_title():
        return "A confirmation is already waiting on the HUD; ask the user to answer it first."
    shown = "\n".join(commits[:10]) + (f"\n… and {len(commits) - 10} more"
                                       if len(commits) > 10 else "")
    detail = (f"Push {len(commits)} commit(s) from {branch} to {where}:\n{shown}\n\n"
              f"Repository: {root}")

    def go() -> str:
        code, out = _git(root, *argv, timeout=180)
        ok = code == 0
        _log(player, f"SYS: git push — {'done' if ok else 'failed'}")
        announce.say(
            f"[GIT_PUSH] Push of {branch} to {where} "
            f"{'succeeded' if ok else 'FAILED'}.\n{_cap(out, 1200)}\n\nTell the user "
            f"in one short sentence.", note=f"git push {branch} — {'done' if ok else 'failed'}")
        return "pushed" if ok else f"failed: {out[:200]}"
    return confirm.request("git_push", f"git push {branch} → {where}", detail, go)


def _pr_list(root: Path) -> str:
    code, out = _run(["gh", "pr", "list", "--limit", "10", "--json",
                      "number,title,headRefName,isDraft,url"], root, timeout=30)
    if code:
        return f"Could not list pull requests: {out}"
    import json
    try:
        prs = json.loads(out or "[]")
    except ValueError:
        return out
    if not prs:
        return "No open pull requests."
    return "\n".join(f"#{p['number']} {p['title']} ({p['headRefName']}"
                     f"{', draft' if p.get('isDraft') else ''}) {p['url']}" for p in prs)


def _pr_create(root: Path, title: str, body: str, base: str, player) -> str:
    if not title.strip() or not body.strip():
        return "A pull request needs a title and a description; write both, then call again."
    up = _upstream(root)
    branch = _branch(root)
    if not up:
        return (f"{branch or 'This branch'} has not been pushed yet. Push it first "
                f"(action push), then create the pull request.")
    from core import confirm
    if confirm.pending_title():
        return "A confirmation is already waiting on the HUD; ask the user to answer it first."
    detail = (f"Draft pull request from {branch}" + (f" into {base}" if base else "")
              + f"\n\nTitle: {title}\n\n{_cap(body, 900)}")

    def go() -> str:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "body.md"
            path.write_text(body, encoding="utf-8")
            argv = ["gh", "pr", "create", "--draft", "--title", title,
                    "--body-file", str(path)] + (["--base", base] if base else [])
            code, out = _run(argv, root, timeout=120)
        ok = code == 0
        _log(player, f"SYS: pull request — {'created' if ok else 'failed'}")
        announce.say(
            f"[GIT_PR] Creating the draft pull request '{title}' "
            f"{'succeeded' if ok else 'FAILED'}.\n{_cap(out, 800)}\n\nTell the user in "
            f"one short sentence (do not read the URL out).",
            note=f"pull request — {'created' if ok else 'failed'}")
        return out[:200]
    return confirm.request("git_pr", f"Create draft PR: {title[:50]}", detail, go)


def git_project(parameters: dict, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "status").strip().lower()
    root, problem = _repo(str(p.get("project") or ""))
    if root is None:
        return problem
    _log(player, f"SYS: git {action} — {root.name}")
    if action == "status":
        return _status(root)
    if action == "diff":
        return _diff(root, str(p.get("path") or "").strip(), bool(p.get("staged")))
    if action == "log":
        try:
            count = max(1, min(50, int(p.get("count") or 10)))
        except (TypeError, ValueError):
            count = 10
        return _log_cmd(root, count)
    if action == "branches":
        return _branches(root)
    if action in ("branch", "switch"):
        return _switch(root, str(p.get("branch") or "").strip(), action == "branch", player)
    if action == "commit":
        paths = [str(x).strip() for x in (p.get("paths") or []) if str(x).strip()]
        return _commit(root, str(p.get("message") or ""), paths, bool(p.get("all")), player)
    if action == "fetch":
        return _fetch(root)
    if action == "pull":
        return _pull(root)
    if action == "push":
        return _push(root, player)
    if action == "pr_list":
        return _pr_list(root)
    if action == "pr_create":
        return _pr_create(root, str(p.get("title") or ""), str(p.get("body") or ""),
                          str(p.get("base") or "").strip(), player)
    return ("Unknown git action. Use status, diff, log, branches, branch, switch, "
            "commit, fetch, pull, push, pr_list or pr_create.")


TOOL = {
    "name": "git_project",
    "description": (
        "Git in the current project (or a named one): status ('what changed?'), "
        "diff (the actual changes; staged=true for staged ones), log (recent "
        "commits), branches, branch (create and switch to a new branch), switch, "
        "commit (named files, or all=true for every tracked change — never "
        "untracked files implicitly), fetch, pull (fast-forward only), push, "
        "pr_list and pr_create (a draft GitHub pull request via gh). Commit, "
        "branch and switch can be undone with the undo tool. push and pr_create "
        "wait for the user to press CONFIRM on the HUD and their outcome is "
        "announced afterwards. Only commit or push when the user asked. No force "
        "push, reset or clean exists here; for other git commands use "
        "run_command. To see what an agent changed, use diff."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "description": "status | diff | log | branches | branch | switch | "
                                      "commit | fetch | pull | push | pr_list | pr_create"},
            "project": {"type": "STRING",
                        "description": "Project name or folder; omit for the current project"},
            "path": {"type": "STRING", "description": "diff: limit to this file or folder"},
            "staged": {"type": "BOOLEAN", "description": "diff: show staged changes"},
            "count": {"type": "INTEGER", "description": "log: how many commits (default 10)"},
            "branch": {"type": "STRING", "description": "branch/switch: the branch name"},
            "message": {"type": "STRING", "description": "commit: the commit message"},
            "paths": {"type": "ARRAY", "items": {"type": "STRING"},
                      "description": "commit: files to commit, relative to the repository"},
            "all": {"type": "BOOLEAN",
                    "description": "commit: commit every change to tracked files"},
            "title": {"type": "STRING", "description": "pr_create: pull request title"},
            "body": {"type": "STRING", "description": "pr_create: pull request description"},
            "base": {"type": "STRING", "description": "pr_create: target branch (optional)"},
        },
    },
    "handler": git_project,
}
