"""
core/shell_policy.py — which terminal commands run at once, which wait for the
user, and which are refused.

THE THREE TIERS
    SAFE     reads, inspects, or runs the project's own tests and builds
             → runs immediately
    CONFIRM  anything else: installs, deletes, moves, scripts, network changes,
             anything this module does not recognise → the HUD's CONFIRM
             button (core/confirm.py — a token the model cannot forge)
    BLOCKED  destroys data or the system and has no business coming from a
             voice request (format, recursive delete of a drive or the home
             folder, git reset --hard, force push, encoded or downloaded-and-
             piped scripts) → refused with the reason

    The idea is the PrinceRaval1706/Mark-LIII fork's execute_command; its code
    was not taken. It matched substrings of the whole line, so "npm update" was
    SAFE because it contains "date", and "python tools.py" because it contains
    "ls". Here the line is lexed the way a shell would see it — quotes
    respected, split at | ; && || & — and each piece is judged by its program
    and arguments. A pipeline is as safe as its least safe piece. Anything that
    writes a file through a redirection, substitutes a command ($(…), `…`), or
    is not recognised lands in CONFIRM: the default is "ask", never "run".

    Tests and builds count as SAFE on purpose: they execute project code, but
    "run the tests" is the request this whole feature exists for, and a button
    press on every test run would be the button nobody reads.
"""

from __future__ import annotations

import re

SAFE, CONFIRM, BLOCKED = "SAFE", "CONFIRM", "BLOCKED"
_RANK = {SAFE: 0, CONFIRM: 1, BLOCKED: 2}

# ── whole-line checks, before any lexing ────────────────────────────────────
_ENCODED = re.compile(r"(^|\s)-(e|en|enc|enco|encod|encode|encoded|encodedc\w*|ec)\s",
                      re.IGNORECASE)
_PWSH = re.compile(r"\b(pwsh|powershell)(\.exe)?\b", re.IGNORECASE)
_DOWNLOAD = re.compile(r"\b(iwr|irm|invoke-webrequest|invoke-restmethod|curl|wget)\b",
                       re.IGNORECASE)
_EXEC_SINK = re.compile(r"\|\s*(iex|invoke-expression|sh|bash|zsh|python3?|py|pwsh|"
                        r"powershell|cmd)(\.exe)?\b", re.IGNORECASE)

# ── per-program rules ───────────────────────────────────────────────────────
_READ_ANY_ARGS = {
    # PowerShell cmdlets and their aliases that only read or format.
    "get-childitem", "gci", "ls", "dir", "get-content", "gc", "cat", "type",
    "get-item", "gi", "get-itemproperty", "gp", "test-path", "resolve-path",
    "get-location", "gl", "pwd", "select-string", "sls", "findstr", "find",
    "measure-object", "measure", "sort-object", "sort", "select-object",
    "select", "where-object", "where", "?", "format-table", "ft", "format-list",
    "fl", "format-wide", "fw", "out-string", "write-output", "echo",
    "write-host", "get-date", "get-process", "gps", "ps", "get-service", "gsv",
    "get-command", "gcm", "get-help", "help", "man", "get-filehash",
    "get-computerinfo", "get-netipaddress", "get-netadapter", "test-connection",
    "test-netconnection", "get-volume", "get-psdrive", "gdr", "get-hotfix",
    "get-ciminstance", "compare-object", "diff", "group-object", "group",
    "get-member", "gm", "convertto-json", "convertfrom-json", "get-uptime",
    "get-unique", "gu", "join-path", "split-path", "get-acl", "start-sleep",
    "sleep",
    # Changing directory only affects the shell this command runs in.
    "cd", "set-location", "sl", "chdir", "pushd", "popd",
    # Native read-only tools.
    "whoami", "hostname", "systeminfo", "tasklist", "ping", "tracert",
    "traceroute", "nslookup", "pathping", "netstat", "which", "where.exe",
    "tree", "wc", "head", "tail", "grep", "egrep", "fgrep", "rg", "du", "df",
    "uname", "uptime", "pytest", "py.test", "ver", "vol", "file", "stat",
    "less", "more", "nproc", "lscpu", "lsblk", "free", "id",
}
_VERSION_ARGS = {"--version", "-v", "-V", "version", "--help", "-h", "/?", "help"}

_DELETE = {"rm", "del", "erase", "rd", "rmdir", "remove-item", "ri", "rmdir.exe"}
_RECURSIVE = {"-r", "-rf", "-fr", "-R", "-Rf", "-rF", "--recursive", "-recurse",
              "/s", "-force", "-recu", "-rec"}
_ROOT_TARGET = re.compile(r"^(/|/\*|~|~/|~\\|\*|\.\.?|\.\.?[\\/]\*?|\.git[\\/]?|\$home|\$env:userprofile|"
                          r"\$env:systemroot|\$env:windir|[a-z]:[\\/]?\*?|"
                          r"[a-z]:[\\/](windows|users|program files.*)[\\/]?)$",
                          re.IGNORECASE)
_SYSTEM_KILLERS = {
    "format", "format-volume", "diskpart", "fdisk", "parted", "clear-disk",
    "initialize-disk", "remove-partition", "bcdedit", "shutdown",
    "restart-computer", "stop-computer", "reboot", "poweroff", "halt",
    "clear-eventlog", "wipefs", "mkswap",
}
_SECRET_NAME = re.compile(r"(api_keys\.json|credentials\.json|\.env(\.|$)|id_rsa|"
                          r"id_ed25519|\.pem$|\.key$|token\.json|secrets?\.)",
                          re.IGNORECASE)

_GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
_GIT_READ = {"status", "diff", "log", "show", "blame", "shortlog", "describe",
             "rev-parse", "ls-files", "ls-tree", "grep", "cat-file",
             "whatchanged", "count-objects", "version", "help", "rev-list",
             "name-rev", "for-each-ref", "show-ref", "check-ignore", "merge-base"}
_GIT_BRANCH_READ_FLAGS = {"-a", "-r", "--list", "-l", "-v", "-vv", "--all",
                          "--remotes", "--show-current", "--merged", "--no-merged",
                          "--contains", "--sort", "--format", "--color", "--no-color"}

_GH_READ = {("pr", "list"), ("pr", "view"), ("pr", "status"), ("pr", "diff"),
            ("pr", "checks"), ("issue", "list"), ("issue", "view"),
            ("issue", "status"), ("repo", "view"), ("run", "list"), ("run", "view"),
            ("auth", "status"), ("release", "list"), ("release", "view"),
            ("workflow", "list"), ("workflow", "view"), ("search", "repos"),
            ("search", "issues"), ("search", "prs")}

# Package managers and build tools: what counts as "inspect, test or build".
_TOOL_SAFE = {
    "npm": {"ls", "list", "outdated", "view", "info", "why", "test", "t",
            "audit", "doctor", "root", "bin", "prefix", "explain", "query"},
    "pnpm": {"ls", "list", "outdated", "why", "test", "t", "audit"},
    "yarn": {"list", "outdated", "why", "info", "test", "audit"},
    "pip": {"list", "show", "freeze", "check", "index", "inspect"},
    "pip3": {"list", "show", "freeze", "check", "index", "inspect"},
    "cargo": {"test", "check", "build", "clippy", "tree", "metadata", "bench", "doc"},
    "go": {"test", "vet", "build", "list", "env", "doc"},
    "dotnet": {"test", "build", "--info", "--list-sdks", "--list-runtimes", "list"},
    "mvn": {"test", "verify", "compile", "package", "dependency:tree", "validate"},
    "gradle": {"test", "check", "build", "tasks", "dependencies", "assemble"},
    "gradlew": {"test", "check", "build", "tasks", "dependencies", "assemble"},
    "make": {"test", "check", "lint", "build"},
    "flutter": {"test", "analyze", "doctor", "devices"},
}
_NPM_SAFE_SCRIPTS = {"test", "lint", "build", "typecheck", "type-check", "check",
                     "format:check", "test:unit"}
_PY_MODULE_SAFE = {"pytest", "unittest", "compileall", "py_compile", "mypy",
                   "flake8", "pylint", "pyflakes", "tabnanny", "doctest"}
_LINTERS = {"ruff", "mypy", "flake8", "pylint", "eslint", "tsc", "black",
            "prettier", "shellcheck", "isort"}
_FIXING_FLAGS = {"--fix", "--write", "-w", "--fix-only", "--unsafe-fixes"}


def _worst(a: tuple[str, str], b: tuple[str, str]) -> tuple[str, str]:
    return b if _RANK[b[0]] > _RANK[a[0]] else a


def _lex(line: str) -> tuple[list[list[str]], list[str]]:
    """Split a command line into segments of tokens, the way a shell would.

    Returns (segments, flags). Flags name constructs that make the line
    unpredictable: 'subst' (command substitution), 'redirect' (writes a file).
    """
    segments: list[list[str]] = [[]]
    flags: list[str] = []
    tok, quote, i, n = "", "", 0, len(line)

    def end_token():
        nonlocal tok
        if tok:
            segments[-1].append(tok)
        tok = ""

    while i < n:
        ch = line[i]
        if quote:
            if ch == quote:
                quote = ""
            elif quote == '"' and (ch == "`" or line.startswith("$(", i)):
                flags.append("subst")
                tok += ch
            else:
                tok += ch
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if ch == "`" or line.startswith("$(", i):
            flags.append("subst")
            tok += ch
            i += 1
            continue
        if ch.isspace():
            end_token()
            i += 1
            continue
        if ch in "|;&":
            # 2>&1 is part of a redirection, handled below; any other & | ;
            # ends the current piece.
            end_token()
            j = i + 1
            if j < n and line[j] == ch and ch in "|&":
                j += 1
            if segments[-1]:
                segments.append([])
            i = j
            continue
        if ch == ">":
            stream = tok if tok in ("1", "2", "3", "4", "5", "6", "*") else ""
            if stream:
                tok = ""
            end_token()
            j = i + 1
            if j < n and line[j] == ">":
                j += 1
            if j < n and line[j] == "&":           # 2>&1 — stream merge
                j += 1
                while j < n and line[j].isdigit():
                    j += 1
                i = j
                continue
            while j < n and line[j].isspace():
                j += 1
            k = j
            while k < n and not line[k].isspace() and line[k] not in "|;&":
                k += 1
            target = line[j:k].strip("'\"").lower()
            if target not in ("$null", "nul", "/dev/null"):
                flags.append("redirect")
            i = k
            continue
        if ch == "<":
            end_token()
            i += 1
            continue
        tok += ch
        i += 1
    end_token()
    return [s for s in segments if s], flags


def _prog(token: str) -> str:
    name = re.split(r"[\\/]", token.strip("'\""))[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(ext) and name != "where.exe":
            name = name[: -len(ext)]
    return name


def _segment(tokens: list[str]) -> tuple[str, str]:
    prog = _prog(tokens[0])
    args = tokens[1:]
    low = [a.lower() for a in args]

    if prog in ("&", "."):
        return CONFIRM, "it runs a script or program"
    if prog in _SYSTEM_KILLERS or prog.startswith("mkfs"):
        if prog == "shutdown" or prog.endswith("-computer"):
            return BLOCKED, ("shutting down or restarting is done by its own tool, "
                             "which asks first")
        return BLOCKED, "it can erase disks or break the system"
    if prog == "dd" and any(a.startswith("of=") for a in low):
        return BLOCKED, "dd writes raw data over a disk or file"
    if prog in ("vssadmin", "wbadmin") and "delete" in low:
        return BLOCKED, "it deletes backups or restore points"
    if prog == "cipher" and any(a.startswith("/w") for a in low):
        return BLOCKED, "it wipes free disk space"
    if prog == "wevtutil" and low[:1] in (["cl"], ["clear-log"]):
        return BLOCKED, "it clears system event logs"
    if prog in _DELETE:
        recursive = any(a in _RECURSIVE or re.fullmatch(r"-[a-z]*r[a-z]*", a) for a in low)
        # Windows switches look like /s or /q; a bare "/" is the POSIX root.
        targets = [a for a in args
                   if not a.startswith("-") and not re.fullmatch(r"/[a-z?]", a.lower())]
        if recursive and any(_ROOT_TARGET.match(t.strip("'\"")) for t in targets):
            return BLOCKED, "it would recursively delete a drive, the home folder or .git"
        return CONFIRM, "it deletes files"
    if prog == "git":
        return _git(args)
    if prog == "gh":
        pos = [a for a in low if not a.startswith("-")]
        if tuple(pos[:2]) in _GH_READ:
            return SAFE, "it only reads from GitHub"
        return CONFIRM, "it changes something on GitHub"

    if any(_SECRET_NAME.search(a.strip("'\"")) for a in args):
        return CONFIRM, "it touches a file that holds keys or passwords"

    if args and len(args) == 1 and args[0] in _VERSION_ARGS:
        return SAFE, "it only prints a version or help"
    if prog == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok") for a in low):
        return CONFIRM, "find with -delete or -exec changes files"
    if prog == "ipconfig":
        if all(a in ("/all", "/displaydns", "-all") for a in low):
            return SAFE, "it only reads the network settings"
        return CONFIRM, "it changes the network connection"
    if prog == "arp":
        return (SAFE, "it only reads") if all(a in ("-a", "/a") for a in low) \
            else (CONFIRM, "it changes the network tables")
    if prog == "route":
        return (SAFE, "it only reads") if low[:1] == ["print"] \
            else (CONFIRM, "it changes the network routes")
    if prog in _READ_ANY_ARGS:
        return SAFE, "it only reads"

    if prog in ("python", "python3", "py"):
        if low[:1] == ["-m"] and len(low) > 1:
            mod = low[1]
            if mod in _PY_MODULE_SAFE:
                return SAFE, "it runs the project's tests or checks"
            if mod in ("pip", "pip3") and len(low) > 2 and low[2] in _TOOL_SAFE["pip"]:
                return SAFE, "it only lists packages"
            if mod in ("pip", "pip3"):
                return CONFIRM, "it installs or removes packages"
            if mod in _LINTERS and not (set(low) & _FIXING_FLAGS):
                return SAFE, "it runs a checker"
        return CONFIRM, "it runs a Python script or command"
    if prog in ("node", "deno", "bun", "ruby", "perl", "php", "java"):
        return CONFIRM, "it runs a script"
    if prog in _LINTERS:
        if set(low) & _FIXING_FLAGS:
            return CONFIRM, "it rewrites files to fix them"
        if prog == "tsc" and "--noemit" not in low:
            return CONFIRM, "tsc writes compiled files"
        if prog in ("black", "prettier", "isort") and "--check" not in low:
            return CONFIRM, "it reformats files"
        if prog == "ruff" and low[:1] == ["format"] and "--check" not in low:
            return CONFIRM, "it reformats files"
        return SAFE, "it runs a checker"
    if prog in ("npm", "pnpm", "yarn"):
        pos = [a for a in low if not a.startswith("-")]
        if pos[:1] == ["run"] and len(pos) > 1:
            return (SAFE, "it runs the project's tests or build") \
                if pos[1] in _NPM_SAFE_SCRIPTS else (CONFIRM, "it runs a project script")
        if pos[:1] == ["config"] and pos[1:2] in (["get"], ["list"], ["ls"]):
            return SAFE, "it only reads settings"
        if pos[:1] == ["audit"] and "fix" in pos:
            return CONFIRM, "it changes installed packages"
        if pos and pos[0] in _TOOL_SAFE[prog]:
            return SAFE, "it inspects, tests or builds the project"
        return CONFIRM, "it installs, removes or runs packages"
    if prog in _TOOL_SAFE:
        pos = [a for a in low if not a.startswith("-") or a in ("--info", "--list-sdks",
                                                               "--list-runtimes")]
        if prog in ("mvn", "gradle", "gradlew") and pos and all(p in _TOOL_SAFE[prog]
                                                               for p in pos):
            return SAFE, "it inspects, tests or builds the project"
        if pos and pos[0] in _TOOL_SAFE[prog]:
            if prog == "cargo" and pos[0] == "clippy" and "--fix" in low:
                return CONFIRM, "it rewrites files to fix them"
            return SAFE, "it inspects, tests or builds the project"
        return CONFIRM, "it installs, changes or runs something"
    return CONFIRM, "it may change files or the system"


def _git(args: list[str]) -> tuple[str, str]:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in _GIT_GLOBAL_WITH_VALUE else 1
    if i >= len(args):
        return SAFE, "it only prints git help"
    sub, rest = args[i].lower(), args[i + 1:]
    low = [a.lower() for a in rest]
    if sub == "reset" and "--hard" in low:
        return BLOCKED, "git reset --hard throws away uncommitted work for good"
    if sub == "clean" and any(re.fullmatch(r"-[a-z]*f[a-z]*", a) or a == "--force"
                              for a in low):
        return BLOCKED, "git clean -f deletes untracked files for good"
    if sub == "push" and (any(a in ("-f", "--force", "--mirror") or
                              a.startswith("--force") for a in low)
                          or any(a.startswith("+") for a in rest)):
        return BLOCKED, "a force push can erase other people's commits"
    if sub in _GIT_READ:
        if sub == "diff" and "--output" in " ".join(low):
            return CONFIRM, "it writes a file"
        return SAFE, "it only reads the repository"
    if sub == "branch":
        if all(a in _GIT_BRANCH_READ_FLAGS or a.startswith(("--sort=", "--format="))
               for a in low):
            return SAFE, "it only lists branches"
        return CONFIRM, "it creates, renames or deletes a branch"
    if sub == "tag" and all(a in ("-l", "--list", "-n") for a in low):
        return SAFE, "it only lists tags"
    if sub == "remote" and (not low or low[0] in ("-v", "show", "get-url")):
        return SAFE, "it only lists remotes"
    if sub == "stash" and low[:1] in (["list"], ["show"]):
        return SAFE, "it only lists stashes"
    if sub == "reflog" and (not low or low[0] == "show"):
        return SAFE, "it only reads the reflog"
    if sub == "config" and any(a in ("--get", "--list", "-l", "--get-all",
                                     "--get-regexp", "--show-origin") for a in low):
        return SAFE, "it only reads git settings"
    if sub == "worktree" and low[:1] == ["list"]:
        return SAFE, "it only lists worktrees"
    return CONFIRM, f"git {sub} changes the repository or a remote"


def classify(command: str) -> tuple[str, str]:
    """(tier, reason) for one command line."""
    line = str(command or "").strip()
    if not line:
        return BLOCKED, "the command is empty"
    if ":(){" in line.replace(" ", ""):
        return BLOCKED, "that is a fork bomb"
    if ((_PWSH.search(line) and _ENCODED.search(" " + line + " "))
            or "frombase64string" in line.lower()):
        return BLOCKED, ("encoded commands hide what they do — give the command "
                         "in plain text")
    if _DOWNLOAD.search(line) and _EXEC_SINK.search(line):
        return BLOCKED, "it downloads code and runs it straight away"
    if len(line) > 2000:
        return CONFIRM, "the command is very long"

    segments, flags = _lex(line)
    if not segments:
        return BLOCKED, "the command is empty"
    verdict = None
    for seg in segments:
        v = _segment(seg)
        verdict = v if verdict is None else _worst(verdict, v)
        if verdict[0] == BLOCKED:
            return verdict
    if "subst" in flags:
        verdict = _worst(verdict, (CONFIRM, "it runs a command inside the command"))
    if "redirect" in flags:
        verdict = _worst(verdict, (CONFIRM, "it writes its output to a file"))
    return verdict
