"""
core/capabilities.py — "check yourself": can each part of the assistant work
right now?

WHY
    A tool that fails to load is skipped at startup with one console line; a
    missing package is discovered the first time someone asks for the feature;
    a signed-out agent CLI is found out halfway through a request. The user
    hears "I can't" at the worst moment, or worse, nothing.

    From the TeddyDARKVADOR/Jarvis_replacement_new fork's capability probes:
    check everything on demand, record a status per part, and let the
    assistant answer "what is broken?" from the record rather than guess.

WHAT IS CHECKED
    * Every action and plugin file that tried to load: rejected ones are
      broken, with the loader's reason.
    * Each tool's own `probe` (see core/action_loader.py): a harmless check
      the tool knows how to make — a binary on PATH, a signed-in CLI. A probe
      never does anything the user would notice: tools with side effects
      (messages, power, files) either have no probe or only check that they
      could run.
    * The environment: Python version, this OS's packages from
      requirements.txt, git, free disk space, a Gemini key, audio devices.
    * Optionally the test suite, the slow part (about 20 s), in a background
      thread so the conversation carries on.

    Results are kept in ~/.jarvis/health/probes.json so a later "what is
    broken?" can answer without re-running them.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from core import health

REPO = Path(__file__).resolve().parent.parent
PROBE_TIMEOUT = 15.0
TESTS_TIMEOUT = 300.0
MIN_FREE_GB = 1.0

_registries: dict = {"actions": None, "plugins": None}


def bind(actions=None, plugins=None) -> None:
    """main.py hands over the registries it discovered."""
    _registries["actions"] = actions
    _registries["plugins"] = plugins


def results_file() -> Path:
    return health.DIR / "probes.json"


# ── individual checks ───────────────────────────────────────────────────────

def _check_python() -> tuple[bool, str]:
    v = sys.version_info
    ok = (3, 11) <= (v.major, v.minor) <= (3, 13)
    return ok, f"Python {v.major}.{v.minor}.{v.micro}" + ("" if ok else " (supported: 3.11–3.13)")


_REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*)(\[[^\]]*\])?\s*([^;#]*)(;[^#]*)?")


def _marker_applies(marker: str) -> bool:
    marker = marker.strip().lstrip(";").strip()
    if not marker:
        return True
    try:
        from packaging.markers import Marker
        return Marker(marker).evaluate()
    except Exception:
        m = re.search(r"sys_platform\s*==\s*['\"]([^'\"]+)['\"]", marker)
        return (sys.platform == m.group(1)) if m else True


def _check_requirements() -> tuple[bool, str]:
    from importlib import metadata
    path = REPO / "requirements.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return True, "requirements.txt not found (installed build)"
    missing, outdated = [], []
    for line in lines:
        m = _REQ_RE.match(line)
        if not m or line.lstrip().startswith("#"):
            continue
        name, spec, marker = m.group(1), m.group(3).strip(), m.group(4) or ""
        if not _marker_applies(marker):
            continue
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        if spec:
            try:
                from packaging.specifiers import SpecifierSet
                if have not in SpecifierSet(spec):
                    outdated.append(f"{name} {have} (wants {spec})")
            except Exception:
                pass
    if not missing and not outdated:
        return True, "all packages for this OS are installed"
    parts = []
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if outdated:
        parts.append("wrong version: " + ", ".join(outdated))
    # A command that works from any folder. Live, the model ran "python setup.py"
    # in the current project, which has no setup.py.
    names = missing + [o.split(" ")[0] for o in outdated]
    return False, ("; ".join(parts) + " — the user installs them with: python -m pip "
                   "install -U " + " ".join(names))


def _check_git() -> tuple[bool, str]:
    if not shutil.which("git"):
        return False, "git is not on PATH (git_project and self-repair need it)"
    return (REPO / ".git").exists(), ("git found; running from a checkout" if (REPO / ".git").exists()
                                      else "git found, but this is not a git checkout")


def _check_disk() -> tuple[bool, str]:
    free = shutil.disk_usage(REPO).free / 1024 ** 3
    return free >= MIN_FREE_GB, f"{free:.1f} GB free"


def _check_key() -> tuple[bool, str]:
    from memory.config_manager import get_gemini_key
    return bool(get_gemini_key()), ("Gemini key set" if get_gemini_key() else "no Gemini key")


def _check_audio() -> tuple[bool, str]:
    import sounddevice as sd
    devs = sd.query_devices()
    ins = sum(1 for d in devs if d.get("max_input_channels", 0) > 0)
    outs = sum(1 for d in devs if d.get("max_output_channels", 0) > 0)
    return bool(ins and outs), f"{ins} input and {outs} output device(s)"


ENVIRONMENT = [("python", _check_python), ("packages", _check_requirements),
               ("git", _check_git), ("disk", _check_disk), ("gemini_key", _check_key),
               ("audio", _check_audio)]


# ── running them ────────────────────────────────────────────────────────────

def _run_one(fn) -> tuple[bool, str]:
    try:
        out = fn()
        if isinstance(out, tuple) and len(out) == 2:
            return bool(out[0]), str(out[1])[:200]
        return bool(out), ""
    except Exception as e:
        return False, f"probe raised {type(e).__name__}: {e}"[:200]


def run_probes() -> dict:
    """Run every check concurrently, store and return the results:
    {"at", "version", "items": [{"name", "kind", "ok", "detail"}]}."""
    items: list[dict] = []
    jobs: list[tuple[str, str, object]] = [(n, "environment", f) for n, f in ENVIRONMENT]
    for kind in ("actions", "plugins"):
        reg = _registries.get(kind)
        if reg is None:
            continue
        for rec in reg.records():
            if not rec.valid:
                items.append({"name": rec.name, "kind": kind[:-1], "ok": False,
                              "detail": f"did not load: {rec.error}"[:200]})
            elif getattr(rec, "probe", None):
                jobs.append((rec.name, kind[:-1], rec.probe))
    pool = cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix="probe")
    try:
        futures = {pool.submit(_run_one, fn): (name, kind) for name, kind, fn in jobs}
        done, pending = cf.wait(futures, timeout=PROBE_TIMEOUT)
        for fut, (name, kind) in futures.items():
            if fut in done:
                ok, detail = fut.result()
            else:
                ok, detail = False, f"no answer within {PROBE_TIMEOUT:.0f} s"
            items.append({"name": name, "kind": kind, "ok": ok, "detail": detail})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    items.sort(key=lambda i: (i["ok"], i["kind"], i["name"]))
    result = {"at": datetime.now().isoformat(timespec="seconds"),
              "version": health.version(), "items": items}
    _store("probes", result)
    for i in items:
        if not i["ok"]:
            health.log_error("probe", i["detail"], tool=i["name"])
    return result


# ── the test suite ──────────────────────────────────────────────────────────

def test_env() -> dict:
    """The suite runs headless; no key or token it does not need is passed on."""
    env = {k: v for k, v in os.environ.items()
           if not re.search(r"(?i)api_?key|token|secret|password", k)}
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_tests(cwd: Path | None = None, timeout: float = TESTS_TIMEOUT) -> dict:
    """pytest with a JUnit report, read from the XML file rather than from
    console text — a line of output can claim anything, the report is written
    by pytest itself. Returns {"ok", "passed", "failed": [ids], "skipped",
    "total", "seconds", "error"}."""
    if getattr(sys, "frozen", False):
        return {"ok": False, "error": "tests cannot run from the packaged app"}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="jarvis-tests-") as tmp:
        report = Path(tmp) / "report.xml"
        argv = [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
                f"--junitxml={report}"]
        try:
            r = subprocess.run(argv, cwd=cwd or REPO, env=test_env(), capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, **_no_window())
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"the tests did not finish in {timeout:.0f} s",
                    "seconds": time.monotonic() - started}
        out = parse_junit(report)
        out["seconds"] = round(time.monotonic() - started, 1)
        if out.get("error"):
            tail = (r.stdout or "")[-600:] + (r.stderr or "")[-400:]
            out["error"] += f" (exit {r.returncode}) {tail.strip()}"[:800]
        return out


def parse_junit(path: Path) -> dict:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {"ok": False, "error": "pytest wrote no report", "failed": [],
                "passed": 0, "skipped": 0, "total": 0}
    failed, skipped, total = [], 0, 0
    for case in root.iter("testcase"):
        total += 1
        cid = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(cid)
        elif case.find("skipped") is not None:
            skipped += 1
    # A collection error is a <testcase> with an <error> too, so it lands in
    # `failed` and cannot pass for a clean run.
    passed = total - len(failed) - skipped
    return {"ok": not failed and total > 0, "passed": passed, "failed": sorted(failed),
            "skipped": skipped, "total": total, "error": "" if total else "no tests ran"}


def _no_window() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# ── storage ─────────────────────────────────────────────────────────────────

def _store(key: str, value: dict) -> None:
    try:
        data = last()
        data[key] = value
        results_file().parent.mkdir(parents=True, exist_ok=True)
        results_file().write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    except Exception as e:                                   # pragma: no cover
        print(f"[Capabilities] could not store results: {e}")


def store_tests(result: dict) -> None:
    _store("tests", {**result, "at": datetime.now().isoformat(timespec="seconds"),
                     "version": health.version()})


def last() -> dict:
    try:
        return json.loads(results_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def describe(result: dict, failures_only: bool = True) -> str:
    items = result.get("items") or []
    bad = [i for i in items if not i["ok"]]
    lines = [f"Checked {len(items)} part(s) at {result.get('at', '?')}: "
             f"{len(items) - len(bad)} fine, {len(bad)} with a problem."]
    for i in bad if failures_only else items:
        mark = "OK " if i["ok"] else "BAD"
        lines.append(f"{mark} {i['kind']} {i['name']}: {i['detail']}")
    return "\n".join(lines)
