"""Host-wide exclusive lock so only one agent can restart Calibre at a time."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, TextIO

from ao3kit.proc import pid_is_alive, read_json, utc_now

LOCK_ENV = "AO3KIT_CALIBRE_LOCK"
_THREAD_LOCK = threading.Lock()


def default_lock_path() -> Path:
    env = os.environ.get(LOCK_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    from ao3kit.paths import calibre_lock_file

    return calibre_lock_file()


class RestartLocked(Exception):
    """Another process or thread already holds the Calibre restart lock."""

    def __init__(self, message: str, snapshot: dict[str, Any] | None = None):
        super().__init__(message)
        self.snapshot = dict(snapshot or {})


def lock_snapshot(path: Path) -> dict[str, Any] | None:
    data = read_json(path)
    if not data:
        return None
    pid = data.get("pid")
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        pid_i = 0
    if pid_i and not pid_is_alive(pid_i):
        return None
    return data


def _flock_exclusive(handle: TextIO, *, nonblocking: bool) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        mode = msvcrt.LK_NBLCK if nonblocking else msvcrt.LK_LOCK
        try:
            msvcrt.locking(handle.fileno(), mode, 1)
        except OSError as exc:
            raise BlockingIOError from exc
        return
    import fcntl

    flags = fcntl.LOCK_EX
    if nonblocking:
        flags |= fcntl.LOCK_NB
    fcntl.flock(handle.fileno(), flags)


def _flock_unlock(handle: TextIO) -> None:
    if os.name == "nt":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


class RestartLock:
    """fcntl (POSIX) / msvcrt (Windows) lock plus JSON holder metadata."""

    def __init__(self, path: Path):
        self.path = path
        self._fh: TextIO | None = None

    def acquire(self, payload: dict[str, Any], timeout: float = 0.0) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            try:
                _flock_exclusive(handle, nonblocking=True)
                break
            except BlockingIOError:
                if timeout <= 0 or time.monotonic() >= deadline:
                    handle.close()
                    return False
                time.sleep(0.1)
        handle.seek(0)
        handle.truncate()
        record = dict(payload)
        record.setdefault("pid", os.getpid())
        record.setdefault("started_at", utc_now())
        handle.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        self._fh = handle
        return True

    def release(self) -> None:
        handle = self._fh
        self._fh = None
        if handle is None:
            return
        try:
            handle.seek(0)
            handle.truncate()
            handle.flush()
            _flock_unlock(handle)
        finally:
            handle.close()


@contextmanager
def hold_restart_lock(
    holder: str,
    *,
    path: Path | None = None,
    agent_id: str = "",
    action: str = "restart",
    timeout: float = 0.0,
) -> Iterator[dict[str, Any]]:
    """Serialize in-process threads, then take the host-wide file lock."""
    lock_path = path or default_lock_path()
    blocking = timeout > 0
    deadline = time.monotonic() + timeout if blocking else None
    acquired_thread = _THREAD_LOCK.acquire(
        blocking=blocking,
        timeout=timeout if blocking else -1,
    )
    if not acquired_thread:
        snap = lock_snapshot(lock_path) or {}
        raise RestartLocked(
            "Calibre restart is already in progress in this process.",
            snapshot=snap,
        )
    file_lock = RestartLock(lock_path)
    payload = {
        "holder": holder,
        "agent_id": agent_id,
        "action": action,
        "pid": os.getpid(),
        "started_at": utc_now(),
    }
    try:
        remaining = 0.0
        if deadline is not None:
            remaining = max(0.0, deadline - time.monotonic())
        if not file_lock.acquire(payload, timeout=remaining):
            raise RestartLocked(
                "Calibre restart is already in progress.",
                snapshot=lock_snapshot(lock_path) or payload,
            )
        try:
            yield payload
        finally:
            file_lock.release()
    finally:
        _THREAD_LOCK.release()
