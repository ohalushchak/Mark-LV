"""Installing an engine: pip packages and model archives.

Everything here runs on a worker thread the user started with a button, and
reports progress through `log` because a 70 MB download with no sign of life
reads as a hung app.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from core.wake_engines import Logger, WakeEngineError


def pip_install(packages: tuple[str, ...], log: Logger) -> None:
    """pip-install into the interpreter running the app. Raises on failure."""
    if not packages:
        return
    log(f"Wake word: installing {', '.join(packages)} (one-time)…")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", *packages],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
        raise WakeEngineError(f"pip install failed: {tail[0][:160]}")


def fetch(url: str, dest: Path, log: Logger, label: str) -> None:
    """Download `url` to `dest` through a .part file, logging every quarter."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "MARK-LV wake word"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, next_mark = 0, 25
            if total:
                log(f"Wake word: downloading {label} ({total / 1e6:.0f} MB)…")
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and done * 100 >= next_mark * total and next_mark < 100:
                    log(f"Wake word: {label} {next_mark}%")
                    next_mark += 25
        part.replace(dest)
    except Exception as e:
        part.unlink(missing_ok=True)
        raise WakeEngineError(f"download failed ({label}): {e}") from e


def install_archive(url: str, models_dir: Path, name: str, log: Logger,
                    keep: Callable[[str], bool] = lambda _n: True) -> Path:
    """Download an archive and unpack the folder `name` into models_dir.

    Unpacks into a temporary folder next to the target and renames it into
    place only when complete, so an interrupted download never leaves a
    half-written model that looks ready. `keep(filename)` picks which files
    to extract — a model archive often carries test clips and fp32 copies
    nobody here needs.
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / name
    with tempfile.TemporaryDirectory(dir=models_dir, prefix=".dl-") as tmp:
        tmp = Path(tmp)
        archive = tmp / url.rsplit("/", 1)[-1]
        fetch(url, archive, log, name)
        log(f"Wake word: unpacking {name}…")
        out = tmp / "out"
        try:
            if archive.suffix == ".zip":
                with zipfile.ZipFile(archive) as z:
                    for m in z.infolist():
                        if not m.is_dir() and keep(Path(m.filename).name):
                            z.extract(m, out)
            else:
                with tarfile.open(archive) as t:
                    members = [m for m in t.getmembers()
                               if m.isfile() and keep(Path(m.name).name)]
                    t.extractall(out, members=members, filter="data")
        except Exception as e:
            raise WakeEngineError(f"could not unpack {name}: {e}") from e
        src = out / name
        if not src.is_dir():
            # Archive without the expected top folder: take whatever it holds.
            dirs = [p for p in out.iterdir() if p.is_dir()]
            src = dirs[0] if len(dirs) == 1 else out
        if target.exists():
            shutil.rmtree(target)
        src.replace(target)
    return target
