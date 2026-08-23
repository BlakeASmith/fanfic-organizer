"""Shared process helpers for detached ao3kit jobs.

Pid files, daemon spawn, SIGTERM stop, log tail/follow, and atomic JSON.
Used by ``ao3kit.jobs`` and the tag-cache warmer.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

SleepFn = Callable[[float], None]
StopFn = Callable[[], bool]
DEFAULT_LOG_LINES = 80
LOG_READ_MAX_BYTES = 2_000_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def looks_like_calibre_binary(python: str) -> bool:
    """True for Calibre GUI / calibre-debug (not a regular CPython)."""
    name = Path(python).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return "calibre" in name


def ao3kit_argv(
    args: Sequence[str],
    *,
    python: str | None = None,
    launcher: str | None = None,
) -> list[str]:
    """Build ``python -m ao3kit …`` or a Calibre ``calibre-debug -e`` launcher.

    Frozen Calibre Python ignores ``PYTHONPATH`` and does not accept ``-m``,
    so bundled plugin jobs set ``AO3KIT_LAUNCHER`` to ``run_ao3kit.py``.
    """
    python = python or sys.executable
    script = (launcher if launcher is not None else os.environ.get("AO3KIT_LAUNCHER", "")).strip()
    extra = [str(part) for part in args]
    if script:
        if looks_like_calibre_binary(python):
            return [python, "-e", script, "--", *extra]
        return [python, "-u", script, *extra]
    return [python, "-u", "-m", "ao3kit", *extra]


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(
        path, json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"
    )


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_pid(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip().splitlines()[0]
        pid = int(text)
    except (OSError, ValueError, IndexError):
        return None
    return pid if pid > 0 else None


def write_pid(path: Path, pid: int) -> None:
    atomic_write_text(path, f"{pid}\n")


def clear_pid(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_is_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_is_alive_windows(pid: int) -> bool:
    import ctypes

    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


def running_pid(pid_path: Path) -> int | None:
    pid = read_pid(pid_path)
    if pid is None:
        return None
    if pid_is_alive(pid):
        return pid
    clear_pid(pid_path)
    return None


def detach_popen_kwargs(log_handle: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    return kwargs


def spawn_daemon(
    argv: list[str],
    *,
    log_path: Path,
    pid_path: Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    wait_seconds: float = 8.0,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> tuple[int | None, str | None]:
    """Detach ``argv`` (full command). Returns ``(pid, error)``."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "a", encoding="utf-8")
    handle.write(f"\n--- start {utc_now()} ---\n")
    handle.flush()
    try:
        proc = popen(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            **detach_popen_kwargs(handle),
        )
    except OSError as exc:
        handle.close()
        return None, f"failed to start daemon: {exc}"
    handle.close()

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        live = running_pid(pid_path)
        if live is not None:
            return live, None
        if proc.poll() is not None:
            return None, (
                f"daemon exited immediately (code {proc.returncode}). "
                f"See {log_path}"
            )
        time.sleep(0.1)
    live = running_pid(pid_path)
    if live is not None:
        return live, None
    return proc.pid, None


def stop_process(
    pid_path: Path,
    *,
    timeout: float = 10.0,
    noun: str = "process",
) -> tuple[bool, str]:
    """SIGTERM the pid in ``pid_path``. Returns ``(signaled, message)``."""
    pid = read_pid(pid_path)
    if pid is None:
        return False, f"{noun} is not running."
    if not pid_is_alive(pid):
        clear_pid(pid_path)
        return False, f"{noun} is not running."
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        clear_pid(pid_path)
        return False, f"{noun} is not running."
    except OSError as exc:
        return True, f"Could not stop pid {pid}: {exc}"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_is_alive(pid):
            clear_pid(pid_path)
            return True, f"Stopped {noun} (pid {pid})."
        time.sleep(0.1)
    clear_pid(pid_path)
    return True, f"Sent stop to pid {pid} (still exiting)."


def terminate_process(proc: subprocess.Popen, *, timeout: float = 8.0) -> None:
    """Stop a child (process group on POSIX) then kill if needed."""
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, OSError):
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass


def read_log_tail(
    path: Path,
    *,
    lines: int | None = DEFAULT_LOG_LINES,
    max_bytes: int = LOG_READ_MAX_BYTES,
) -> str:
    """Return the last ``lines`` of a log file.

    ``lines`` of ``None`` or ``<= 0`` means the whole file, still capped at
    ``max_bytes`` so a huge log cannot blow memory.
    """
    if not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        if size > max_bytes:
            handle.seek(max(0, size - max_bytes))
            handle.readline()
        text = handle.read()
    if not text or lines is None or lines <= 0:
        return text
    parts = text.splitlines()
    if len(parts) <= lines:
        return text if text.endswith("\n") or not text else text + "\n"
    return "\n".join(parts[-lines:]) + "\n"


def last_log_line(path: Path, *, max_bytes: int = 16_384) -> str:
    """Last non-empty line of a log (for status.message)."""
    if not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        if size > max_bytes:
            handle.seek(max(0, size - max_bytes))
            handle.readline()
        text = handle.read()
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def follow_log(
    path: Path,
    *,
    lines: int | None = DEFAULT_LOG_LINES,
    sleep_fn: SleepFn | None = None,
    should_stop: StopFn | None = None,
    write: Callable[[str], None] | None = None,
    poll: float = 0.5,
) -> int:
    """Print a tail, then stream new bytes until ``should_stop``."""
    pause = sleep_fn or time.sleep

    def _emit(chunk: str) -> None:
        if write is not None:
            write(chunk)
            return
        sys.stdout.write(chunk)
        sys.stdout.flush()

    stop = should_stop or (lambda: False)
    offset = 0
    if path.is_file():
        _emit(read_log_tail(path, lines=lines))
        offset = path.stat().st_size
    else:
        sys.stderr.write(f"Waiting for log {path} …\n")
        sys.stderr.flush()
    while not stop():
        pause(poll)
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size < offset:
            offset = 0
        if size <= offset:
            continue
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            chunk = handle.read()
        offset = size
        if chunk:
            _emit(chunk)
    return 0


def interruptible_sleep(
    seconds: float,
    should_stop: StopFn,
    sleep_fn: SleepFn,
    *,
    tick: float = 0.5,
) -> None:
    if seconds <= 0:
        return
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if should_stop():
            return
        remaining = deadline - time.monotonic()
        sleep_fn(min(tick, remaining) if remaining > 0 else 0)
