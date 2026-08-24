"""Find, install into, quit, and start the Calibre GUI.

Restart is gated by ``calibre_dev.lock`` so two agents cannot kill/start
Calibre at the same time. Installing without restart does not take the lock.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from calibre_dev.lock import (
    RestartLocked,
    default_lock_path,
    hold_restart_lock,
    lock_snapshot,
)
from calibre_dev.plugin_install import (
    PLUGIN_NAME,
    apply_fanfic_organizer_gui_names,
    find_calibre,
    find_calibre_customize,
    find_calibre_tool,
    is_calibre_gui_command,
    list_calibre_gui_pids,
    parse_ps_line,
    remove_legacy_calibre_plugins,
    shutdown_calibre_gui,
    start_calibre_gui,
    wait_for_gui,
)
import calibre_dev.plugin_install as plugin_install

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "calibre-plugin"
DEV_PROJECT_STAMP = "dev_project.json"
SHUTDOWN_WAIT = 20.0
TERM_WAIT = 5.0
START_WAIT = 25.0


def write_dev_project_stamp(
    plugin_dir: Path,
    project_root: Path | None = None,
) -> Path:
    """Record the git checkout so the installed plugin can find ao3kit."""
    root = (project_root or ROOT).resolve()
    path = plugin_dir / DEV_PROJECT_STAMP
    path.write_text(
        json.dumps({"project": str(root)}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def install_plugin_zip(plugin_dir: Path | None = None) -> None:
    customize = find_calibre_customize()
    target = plugin_dir or PLUGIN_DIR
    write_dev_project_stamp(target)
    remove_legacy_calibre_plugins(customize)
    plugin_install.run_checked([customize, "-b", str(target)], timeout=120)
    apply_fanfic_organizer_gui_names()


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
        sleep: Any = time.sleep,
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
        write_dev_project_stamp(self.plugin_dir)
        customize = self._find_customize()
        remove_legacy_calibre_plugins(customize)
        plugin_install.run_checked([customize, "-b", str(self.plugin_dir)], timeout=120)
        apply_fanfic_organizer_gui_names()

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

    def _install_result(self) -> dict[str, Any]:
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
        return {
            "ok": True,
            "installed": True,
            "restarted": False,
            "message": (
                "Plugin installed. Restart Calibre to load code changes "
                "(or rerun with --restart / MCP restart=true when iterating)."
            ),
        }

    def install(
        self,
        *,
        restart: bool = False,
        agent_id: str = "",
        lock_timeout: float = 0.0,
        holder: str = "makeplugin:install",
    ) -> dict[str, Any]:
        if not restart:
            return self._install_result()
        try:
            with hold_restart_lock(
                holder,
                path=self.lock_path,
                agent_id=agent_id,
                action="restart",
                timeout=lock_timeout,
            ):
                return self._restart_unlocked(install=True)
        except RestartLocked as exc:
            installed = self._install_result()
            if not installed.get("ok"):
                return installed
            result = _locked_error(exc)
            result["installed"] = True
            result["message"] = (
                "Plugin installed. Calibre restart skipped: another agent "
                "holds the lock."
            )
            return result
        except FileNotFoundError as exc:
            return {"ok": False, "error": "not_found", "message": str(exc)}

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

    def _restart_unlocked(self, *, install: bool = False) -> dict[str, Any]:
        before = self._gui_pids()
        was_running = bool(before)
        self._shutdown()
        self._force_quit_leftovers()
        leftover = self._gui_pids()
        if leftover:
            result: dict[str, Any] = {
                "ok": False,
                "error": "still_running",
                "message": f"Calibre did not quit (pids {leftover}).",
                "was_running": was_running,
                "pids_before": before,
                "pids_after": leftover,
                "restarted": False,
            }
            if install:
                installed = self._install_result()
                result["installed"] = bool(installed.get("ok"))
                if not installed.get("ok"):
                    result["message"] = installed.get("message") or result["message"]
                    result["error"] = installed.get("error") or result["error"]
            return result
        if install:
            installed = self._install_result()
            if not installed.get("ok"):
                self._start()
                self._wait_gui(running=True, timeout=START_WAIT)
                installed["restarted"] = False
                installed["message"] = (
                    f"{installed.get('message', 'Install failed')} "
                    "Calibre was quit; trying to start it again."
                )
                return installed
        self._start()
        started = self._wait_gui(running=True, timeout=START_WAIT)
        after = self._gui_pids()
        if not started:
            result = {
                "ok": False,
                "error": "did_not_start",
                "message": "Calibre quit but did not come back in time.",
                "was_running": was_running,
                "pids_before": before,
                "pids_after": after,
                "restarted": False,
            }
            if install:
                result["installed"] = True
            return result
        result = {
            "ok": True,
            "restarted": True,
            "was_running": was_running,
            "pids_before": before,
            "pids_after": after,
            "message": "Calibre restarted." if was_running else "Calibre started.",
        }
        if install:
            result["installed"] = True
            result["message"] = "Plugin installed and Calibre restarted."
        return result


__all__ = [
    "PLUGIN_NAME",
    "apply_fanfic_organizer_gui_names",
    "write_dev_project_stamp",
    "CalibreCtl",
    "RestartLocked",
    "default_lock_path",
    "find_calibre",
    "find_calibre_customize",
    "find_calibre_tool",
    "install_plugin_zip",
    "is_calibre_gui_command",
    "list_calibre_gui_pids",
    "parse_ps_line",
    "remove_legacy_calibre_plugins",
]
