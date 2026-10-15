"""
core/projects.py — "the project", found by name and remembered.

WHY THIS EXISTS
    A voice request never carries a path. "Run the tests", "what changed?",
    "hand this to Claude" all mean "in the project we are working on", so the
    coding tools share one current project: named once ("open project Mark
    LIV"), then implied. The BlackStarG5/Mark-LIV fork's tools demanded an
    explicit absolute root on every call — sound for an agent loop, unusable
    by voice.

FINDING A PROJECT BY NAME
    A name is matched against the folders directly inside the usual places
    people keep code (~/Projects, ~/source/repos, X:\\Projects on every fixed
    drive, …), the extra folders in config `project_dirs`, and recently used
    projects. Spelling is forgiving: case, spaces, '-' and '_' are ignored, so
    "mark liv" finds "Mark-LIV". An exact match wins; otherwise a unique prefix
    or substring match; several candidates are returned for the model to ask
    which one, never guessed.
"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path

_OS = platform.system()

# Where people keep code, relative to the home folder.
_HOME_DIRS = ("Projects", "projects", "source/repos", "Documents/GitHub",
              "Documents/Projects", "Desktop/JarvisProjects", "dev", "code",
              "src", "repos", "Desktop")
# Checked at the root of every fixed drive on Windows.
_DRIVE_DIRS = ("Projects", "projects", "dev", "src", "repos", "code")


# Files that hold keys or passwords. The coding tools never read, search,
# edit or commit them: what they read goes to the model, and the fork is public.
_SECRET = re.compile(r"(^|[\/])(api_keys\.json|credentials\.json|token\.json|"
                     r"\.env(\..*)?|id_rsa|id_ed25519|id_ecdsa|[^\/]*\.pem|[^\/]*\.key|"
                     r"[^\/]*\.pfx|client_secret[^\/]*\.json|\.netrc|\.npmrc|\.pypirc)$",
                     re.IGNORECASE)


def is_secret(path: str | Path) -> bool:
    return bool(_SECRET.search(str(path).replace("\\", "/")))


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-.]+", "", str(name or "").lower())


def _fixed_drives() -> list[str]:
    if _OS != "Windows":
        return []
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        out = []
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(65 + i)}:\\"
                # 3 = DRIVE_FIXED. Removable and network drives can stall a
                # lookup for seconds, and nobody keeps a project on a DVD.
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                    out.append(root)
        return out
    except Exception:
        return []


def search_roots() -> list[Path]:
    from memory.config_manager import get_project_dirs
    home = Path.home()
    cands = [Path(d).expanduser() for d in get_project_dirs()]
    cands += [home / d for d in _HOME_DIRS]
    for drive in _fixed_drives():
        cands += [Path(drive) / d for d in _DRIVE_DIRS]
    seen, out = set(), []
    for c in cands:
        try:
            key = os.path.normcase(str(c.resolve()))
        except OSError:
            continue
        if key not in seen and c.is_dir():
            seen.add(key)
            out.append(c)
    return out


def _candidates() -> list[Path]:
    from memory.config_manager import get_recent_projects
    out: list[Path] = []
    for r in get_recent_projects():
        p = Path(r)
        if p.is_dir():
            out.append(p)
    for root in search_roots():
        try:
            for child in root.iterdir():
                if child.is_dir() and not child.name.startswith((".", "$")):
                    out.append(child)
        except OSError:
            continue
    seen, uniq = set(), []
    for p in out:
        key = os.path.normcase(str(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def find(query: str) -> tuple[Path | None, list[Path]]:
    """(the project, other candidates). A path is taken as given; a name is
    looked up. (None, [...]) means ambiguous; (None, []) means not found."""
    q = str(query or "").strip().strip('"')
    if not q:
        return None, []
    p = Path(q).expanduser()
    if p.is_absolute() or q.startswith((".", "~")) or "/" in q or "\\" in q:
        return (p.resolve(), []) if p.is_dir() else (None, [])
    want = _norm(q)
    if not want:
        return None, []
    cands = _candidates()
    exact = [c for c in cands if _norm(c.name) == want]
    if exact:
        return exact[0], exact[1:]
    for test in (lambda n: n.startswith(want), lambda n: want in n):
        hits = [c for c in cands if test(_norm(c.name))]
        if len(hits) == 1:
            return hits[0], []
        if hits:
            return None, hits[:6]
    return None, []


def current() -> Path | None:
    from memory.config_manager import get_current_project
    p = get_current_project()
    return Path(p) if p and Path(p).is_dir() else None


def set_current(path: Path) -> None:
    from memory.config_manager import save_current_project
    save_current_project(str(Path(path).resolve()))


def resolve(query: str = "", make_current: bool = True) -> tuple[Path | None, str]:
    """The folder a coding tool should work in, or (None, a sentence for the
    model). A named project becomes the current one unless make_current is
    False."""
    q = str(query or "").strip()
    if not q:
        cur = current()
        if cur is None:
            return None, ("No project is open. Ask the user which project to work "
                          "in (a name like 'Mark-LIV' or a folder path).")
        return cur, ""
    hit, others = find(q)
    if hit is not None:
        if make_current and current() != hit:
            set_current(hit)
        return hit, ""
    if others:
        names = "; ".join(str(o) for o in others)
        return None, (f"Several projects match '{q}': {names}. Ask the user which "
                      "one they mean.")
    return None, (f"I could not find a project called '{q}'. Ask the user for the "
                  "folder path, or check the name.")


def describe(root: Path) -> str:
    """One line: what kind of project this is and which git branch it is on."""
    markers = {"pyproject.toml": "Python", "requirements.txt": "Python",
               "setup.py": "Python", "package.json": "Node.js", "go.mod": "Go",
               "Cargo.toml": "Rust", "pom.xml": "Java (Maven)",
               "build.gradle": "Java/Kotlin (Gradle)", "build.gradle.kts": "Kotlin (Gradle)",
               "CMakeLists.txt": "C/C++ (CMake)", "composer.json": "PHP",
               "Gemfile": "Ruby", "pubspec.yaml": "Dart/Flutter"}
    kinds = sorted({k for f, k in markers.items() if (root / f).exists()})
    if not kinds and any(root.glob("*.sln")):
        kinds = [".NET"]
    branch = ""
    head = root / ".git" / "HEAD"
    try:
        txt = head.read_text(encoding="utf-8").strip()
        branch = txt.rsplit("/", 1)[-1] if txt.startswith("ref:") else txt[:8]
    except OSError:
        pass
    parts = [f"Project {root.name} at {root}"]
    if kinds:
        parts.append(", ".join(kinds))
    parts.append(f"git branch {branch}" if branch else "not a git repository")
    return " — ".join(parts) + "."
