"""
core/jobs.py — commands that keep running after the tool call returns.

WHY THIS EXISTS
    `pytest` on a real project takes twenty seconds, `npm install` two minutes.
    A voice tool that blocks on them says nothing for that long, which from the
    user's chair is a crash. So a command is a job: it starts, the tool waits a
    short while (most commands are done by then), and anything longer keeps
    running here with its output collected, to be polled, stopped, or announced
    when it ends.

    The engine is the BlackStarG5/Mark-LIV fork's command_runner: argv lists
    (never a shell string re-split by Python), stdin closed, no console window,
    the last 16 KB of output kept, the whole process tree killed on stop or
    timeout. Changed: jobs carry a label and an on_done callback (the announcer
    uses it), and output is decoded as UTF-8 with the OEM code page as the
    fallback, which is what Windows console tools still write.
"""

from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

_OS = platform.system()

MAX_RUNNING = 4
MAX_KEPT = 24
TAIL_BYTES = 16_000


@dataclass
class Job:
    id: str
    label: str
    argv: list[str]
    cwd: str
    timeout: float
    kind: str = "command"
    started: float = field(default_factory=time.monotonic)
    proc: subprocess.Popen | None = None
    output: bytearray = field(default_factory=bytearray)
    bytes_seen: int = 0
    finished: bool = False
    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    error: str = ""
    ended: float = 0.0
    on_done: list[Callable[["Job"], None]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round((self.ended or time.monotonic()) - self.started, 1)

    @property
    def ok(self) -> bool:
        return self.finished and self.exit_code == 0 and not (
            self.timed_out or self.cancelled or self.error)

    def text(self) -> str:
        with _lock:
            raw = bytes(self.output)
        return decode(raw)

    @property
    def truncated(self) -> bool:
        return self.bytes_seen > TAIL_BYTES


_lock = threading.RLock()
_jobs: dict[str, Job] = {}


def decode(raw: bytes) -> str:
    """UTF-8 first; the OEM code page (what cmd tools such as ipconfig write)
    when that fails; never raises."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if _OS == "Windows":
        try:
            import ctypes
            return raw.decode(f"cp{ctypes.windll.kernel32.GetOEMCP()}")
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def kill_tree(pid: int) -> None:
    """Kill a process and everything it started (a build's compilers, an
    agent's shells). Never raises."""
    try:
        import psutil
        parent = psutil.Process(pid)
        for child in reversed(parent.children(recursive=True)):
            try:
                child.kill()
            except psutil.Error:
                pass
        parent.kill()
        return
    except Exception:
        pass
    try:
        if _OS == "Windows":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=10)
        else:
            os.kill(pid, 9)
    except Exception:
        pass


def hidden_window_flags() -> dict:
    """Popen kwargs that keep a console window from flashing up on Windows."""
    if _OS == "Windows":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def running(kind: str | None = None) -> list[Job]:
    with _lock:
        return [j for j in _jobs.values()
                if not j.finished and (kind is None or j.kind == kind)]


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(str(job_id or "").strip())


def latest(kind: str | None = None) -> Job | None:
    with _lock:
        js = [j for j in _jobs.values() if kind is None or j.kind == kind]
    return max(js, key=lambda j: j.started) if js else None


def start(argv: list[str], cwd: str, timeout: float = 120.0, label: str = "",
          kind: str = "command", env: dict | None = None,
          on_done: Callable[[Job], None] | None = None) -> Job:
    """Start `argv` in `cwd`. Raises RuntimeError when too many are running or
    the program cannot be started."""
    with _lock:
        if len(running()) >= MAX_RUNNING:
            raise RuntimeError(f"{MAX_RUNNING} commands are already running. "
                               "Wait for one or stop it first.")
        done = sorted((j for j in _jobs.values() if j.finished), key=lambda j: j.started)
        for old in done[:max(0, len(_jobs) - MAX_KEPT + 1)]:
            _jobs.pop(old.id, None)
        job = Job(id=uuid.uuid4().hex[:6], label=label or " ".join(argv)[:80],
                  argv=list(argv), cwd=str(cwd), timeout=max(1.0, float(timeout)),
                  kind=kind)
        if on_done:
            job.on_done.append(on_done)
        try:
            job.proc = subprocess.Popen(
                argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, **hidden_window_flags())
        except OSError as e:
            raise RuntimeError(f"could not start {argv[0]}: {e}") from e
        _jobs[job.id] = job
    threading.Thread(target=_collect, args=(job,), daemon=True,
                     name=f"job-{job.id}").start()
    return job


def wait(job: Job, seconds: float) -> bool:
    """Wait up to `seconds` for the job to finish; True when it has."""
    until = time.monotonic() + max(0.0, seconds)
    while not job.finished and time.monotonic() < until:
        time.sleep(0.05)
    return job.finished


def stop(job: Job) -> bool:
    """Stop a running job, process tree included. False when it had ended."""
    if job.finished or job.proc is None:
        return False
    job.cancelled = True
    kill_tree(job.proc.pid)
    return True


def _collect(job: Job) -> None:
    proc = job.proc

    def read():
        try:
            while True:
                chunk = proc.stdout.read(1024)
                if not chunk:
                    break
                with _lock:
                    job.bytes_seen += len(chunk)
                    job.output += chunk
                    if len(job.output) > TAIL_BYTES:
                        del job.output[:len(job.output) - TAIL_BYTES]
        except Exception:
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=job.timeout)
    except subprocess.TimeoutExpired:
        job.timed_out = True
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    except Exception as e:
        job.error = str(e)
    finally:
        reader.join(timeout=2)
        job.exit_code = proc.poll()
        job.ended = time.monotonic()
        job.finished = True
    for fn in list(job.on_done):
        try:
            fn(job)
        except Exception as e:
            print(f"[Jobs] on_done for {job.id} failed: {e}")


def reset() -> None:
    """Tests only: stop and forget every job."""
    with _lock:
        js = list(_jobs.values())
        _jobs.clear()
    for j in js:
        stop(j)
