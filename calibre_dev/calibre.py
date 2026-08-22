"""Find, install into, quit, and start the Calibre GUI.

Restart is gated by ``calibre_dev.lock`` so two agents cannot kill/start
Calibre at the same time. Installing without restart does not take the lock.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from calibre_dev.lock import (
    RestartLocked,
    default_lock_path,
    hold_restart_lock,
    lock_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "calibre-plugin"
MAC_CALIBRE_BIN = Path("/Applications/calibre.app/Contents/MacOS")
SHUTDOWN_WAIT = 20.0
TERM_WAIT = 5.0
START_WAIT = 25.0
SleepFn = Callable[[float], None]


def is_calibre_gui_command(command: str) -> bool:
    """True for the Calibre GUI binary, not customize/parallel/ebook tools."""
    tokens = command.strip().split()
    if not tokens:
        return False
    name = tokens[0].replace("\\", "/").rstrip("/").split("/")[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name != "calibre":
        return False
    if "--shutdown-running-calibre" in tokens or "-s" in tokens:
        return False
    return True


def parse_ps_line(line: str) -> tuple[int, str] | None:
    text = line.strip()
    if not text:
        return None
    pid_s, sep, cmd = text.partition(" ")
    if not sep:
        return None
    try:
        pid = int(pid_s)
    except ValueError:
        return None
    if pid <= 0:
        return None
    return pid, cmd.strip()


def find_calibre_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    mac = MAC_CALIBRE_BIN / name
    if mac.exists():
        return str(mac)
    raise FileNotFoundError(
        f"{name} not found. Install Calibre or add it to PATH."
    )


def find_calibre() -> str:
    return find_calibre_tool("calibre")


def find_calibre_customize() -> str:
    return find_calibre_tool("calibre-customize")


def list_calibre_gui_pids(
    *,
    ps_output: str | None = None,
) -> list[int]:
    if ps_output is None:
        try:
            ps_output = subprocess.check_output(
                ["ps", "-ax", "-o", "pid=,command="],
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return []
    pids: list[int] = []
    for line in ps_output.splitlines():
        parsed = parse_ps_line(line)
        if parsed is None:
            continue
        pid, command = parsed
        if is_calibre_gui_command(command):
            pids.append(pid)
    return pids


def _run_checked(
    argv: list[str],
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=True,
        timeout=timeout,
        capture_output=True,
        text=True,
    )


def install_plugin_zip(plugin_dir: Path | None = None) -> None:
    customize = find_calibre_customize()
    target = plugin_dir or PLUGIN_DIR
    _run_checked([customize, "-b", str(target)], timeout=120)


def shutdown_calibre_gui(calibre_bin: str | None = None) -> None:
    binary = calibre_bin or find_calibre()
    subprocess.run(
        [binary, "--shutdown-running-calibre"],
        check=False,
        timeout=30,
        capture_output=True,
        text=True,
    )
    if sys.platform == "darwin" and list_calibre_gui_pids():
        subprocess.run(
            ["osascript", "-e", 'tell application "calibre" to quit'],
            check=False,
            timeout=15,
            capture_output=True,
            text=True,
        )


def start_calibre_gui(calibre_bin: str | None = None) -> None:
    binary = calibre_bin or find_calibre()
    if sys.platform == "darwin" and Path("/Applications/calibre.app").exists():
        subprocess.Popen(
            ["open", "-a", "calibre"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([binary], **kwargs)


def wait_for_gui(
    *,
    running: bool,
    timeout: float,
    sleep: SleepFn = time.sleep,
    pids_fn: Callable[[], list[int]] | None = None,
) -> bool:
    get_pids = pids_fn or list_calibre_gui_pids
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = bool(get_pids())
        if alive == running:
            return True
        sleep(0.2)
    return bool(get_pids()) == running


def _locked_error(exc: RestartLocked) -> dict[str, Any]:
    snap = dict(exc.snapshot or {})
    return {
        "ok": False,
        "error": "locked",
        "message": str(exc),
        "holder": snap.get("holder") or "",
        "agent_id": snap.get("agent_id") or "",
        "pid": snap.get("pid"),
        "action": snap.get("action") or "",
        "started_at": snap.get("started_at") or "",
        "lock": snap,
    }


class CalibreCtl:
    """Install the plugin and optionally restart the Calibre GUI under a lock."""

    def __init__(
        self,
        *,
        lock_path: Path | None = None,
        plugin_dir: Path | None = None,
        sleep: SleepFn = time.sleep,
    ):
        self.lock_path = lock_path or default_lock_path()
        self.plugin_dir = plugin_dir or PLUGIN_DIR
        self.sleep = sleep

    def _gui_pids(self) -> list[int]:
        return list_calibre_gui_pids()

    def _find_calibre(self) -> str:
        return find_calibre()

    def _find_customize(self) -> str:
        return find_calibre_customize()

    def _install(self) -> None:
        customize = self._find_customize()
        _run_checked([customize, "-b", str(self.plugin_dir)], timeout=120)

    def _shutdown(self) -> None:
        shutdown_calibre_gui(self._find_calibre())

    def _start(self) -> None:
        start_calibre_gui(self._find_calibre())

    def _signal(self, pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _wait_gui(self, *, running: bool, timeout: float) -> bool:
        return wait_for_gui(
            running=running,
            timeout=timeout,
            sleep=self.sleep,
            pids_fn=self._gui_pids,
        )

    def _force_quit_leftovers(self) -> None:
        if self._wait_gui(running=False, timeout=SHUTDOWN_WAIT):
            return
        for pid in self._gui_pids():
            self._signal(pid, signal.SIGTERM)
        if self._wait_gui(running=False, timeout=TERM_WAIT):
            return
        for pid in self._gui_pids():
            self._signal(pid, signal.SIGKILL)
        self._wait_gui(running=False, timeout=2.0)

    def status(self) -> dict[str, Any]:
        pids = self._gui_pids()
        calibre = ""
        customize = ""
        try:
            calibre = self._find_calibre()
        except FileNotFoundError as exc:
            calibre = str(exc)
        try:
            customize = self._find_customize()
        except FileNotFoundError as exc:
            customize = str(exc)
        snap = lock_snapshot(self.lock_path)
        lock: dict[str, Any] = {
            "held": bool(snap),
            "path": str(self.lock_path),
        }
        if snap:
            lock.update(snap)
        return {
            "ok": True,
            "running": bool(pids),
            "pids": pids,
            "calibre": calibre,
            "customize": customize,
            "lock": lock,
        }

    def install(
        self,
        *,
        restart: bool = False,
        agent_id: str = "",
        lock_timeout: float = 0.0,
        holder: str = "makeplugin:install",
    ) -> dict[str, Any]:
        try:
            self._install()
        except FileNotFoundError as exc:
            return {"ok": False, "error": "not_found", "message": str(exc)}
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or exc.stdout or str(exc)).strip()
            return {
                "ok": False,
                "error": "install_failed",
                "message": err or f"calibre-customize exited {exc.returncode}",
            }
        if not restart:
            return {
                "ok": True,
                "installed": True,
                "restarted": False,
                "message": (
                    "Plugin installed. Restart Calibre to load code changes "
                    "(or rerun with --restart / MCP restart=true when iterating)."
                ),
            }
        result = self.restart(
            agent_id=agent_id,
            lock_timeout=lock_timeout,
            holder=holder,
        )
        result["installed"] = True
        if result.get("ok"):
            result["message"] = "Plugin installed and Calibre restarted."
        elif result.get("error") == "locked":
            result["message"] = (
                "Plugin installed. Calibre restart skipped: another agent "
                "holds the lock."
            )
        return result

    def restart(
        self,
        *,
        agent_id: str = "",
        lock_timeout: float = 0.0,
        holder: str = "makeplugin:restart",
    ) -> dict[str, Any]:
        try:
            with hold_restart_lock(
                holder,
                path=self.lock_path,
                agent_id=agent_id,
                action="restart",
                timeout=lock_timeout,
            ):
                return self._restart_unlocked()
        except RestartLocked as exc:
            return _locked_error(exc)
        except FileNotFoundError as exc:
            return {"ok": False, "error": "not_found", "message": str(exc)}

    def _restart_unlocked(self) -> dict[str, Any]:
        before = self._gui_pids()
        was_running = bool(before)
        self._shutdown()
        self._force_quit_leftovers()
        leftover = self._gui_pids()
        if leftover:
            return {
                "ok": False,
                "error": "still_running",
                "message": f"Calibre did not quit (pids {leftover}).",
                "was_running": was_running,
                "pids_before": before,
                "pids_after": leftover,
                "restarted": False,
            }
        self._start()
        started = self._wait_gui(running=True, timeout=START_WAIT)
        after = self._gui_pids()
        if not started:
            return {
                "ok": False,
                "error": "did_not_start",
                "message": "Calibre quit but did not come back in time.",
                "was_running": was_running,
                "pids_before": before,
                "pids_after": after,
                "restarted": False,
            }
        return {
            "ok": True,
            "restarted": True,
            "was_running": was_running,
            "pids_before": before,
            "pids_after": after,
            "message": "Calibre restarted." if was_running else "Calibre started.",
        }


__all__ = [
    "CalibreCtl",
    "RestartLocked",
    "default_lock_path",
    "find_calibre",
    "find_calibre_customize",
    "install_plugin_zip",
    "is_calibre_gui_command",
    "list_calibre_gui_pids",
    "parse_ps_line",
]
