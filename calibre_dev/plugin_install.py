"""Calibre plugin install helpers without restart-lock or ao3kit dependencies.

Used by the curl installer bundle and by ``calibre_dev.calibre`` / ``install_release``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

PLUGIN_NAME = "Fanfic Organizer"
LEGACY_PLUGIN_NAMES = ("AO3 Scraper", "Wranglekit")
MAC_CALIBRE_BIN = Path("/Applications/calibre.app/Contents/MacOS")
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
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", "")) / "Calibre2" / f"{name}.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "calibre" / f"{name}.exe",
            Path.home() / "AppData" / "Local" / "Programs" / "calibre" / f"{name}.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
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


def run_checked(
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


def calibre_config_dir() -> Path:
    override = (os.environ.get("CALIBRE_CONFIG_DIRECTORY") or "").strip()
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "calibre"
    if os.name == "nt":
        appdata = (os.environ.get("APPDATA") or "").strip()
        if appdata:
            return Path(appdata) / "calibre"
        return Path.home() / "AppData" / "Roaming" / "calibre"
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "calibre"
    return Path.home() / ".config" / "calibre"


def _rename_legacy_plugin_value(
    value: Any,
    *,
    legacy: tuple[str, ...],
    current: str,
) -> Any:
    if isinstance(value, str):
        return current if value in legacy else value
    if isinstance(value, list):
        out: list[Any] = []
        seen_current = False
        for item in value:
            replaced = _rename_legacy_plugin_value(
                item, legacy=legacy, current=current
            )
            if replaced == current:
                if seen_current:
                    continue
                seen_current = True
            out.append(replaced)
        return out
    if isinstance(value, dict):
        renamed: dict[Any, Any] = {}
        for key, item in value.items():
            new_key = key
            if isinstance(key, str):
                for old in legacy:
                    new_key = new_key.replace(old, current)
            renamed[new_key] = _rename_legacy_plugin_value(
                item, legacy=legacy, current=current
            )
        return renamed
    return value


def apply_fanfic_organizer_gui_names(
    config_dir: Path | None = None,
    *,
    name: str = PLUGIN_NAME,
    legacy_names: tuple[str, ...] = LEGACY_PLUGIN_NAMES,
) -> bool:
    """Point leftover AO3 Scraper / Wranglekit toolbar entries at Fanfic Organizer."""
    path = (config_dir or calibre_config_dir()) / "gui.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    updated = _rename_legacy_plugin_value(
        data, legacy=legacy_names, current=name
    )
    if updated == data:
        return False
    path.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def remove_legacy_calibre_plugins(
    customize: str,
    names: tuple[str, ...] = LEGACY_PLUGIN_NAMES,
) -> list[str]:
    removed: list[str] = []
    for name in names:
        try:
            run_checked([customize, "-r", name], timeout=60)
        except subprocess.CalledProcessError:
            continue
        removed.append(name)
    return removed


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


__all__ = [
    "PLUGIN_NAME",
    "LEGACY_PLUGIN_NAMES",
    "apply_fanfic_organizer_gui_names",
    "calibre_config_dir",
    "find_calibre",
    "find_calibre_customize",
    "find_calibre_tool",
    "is_calibre_gui_command",
    "list_calibre_gui_pids",
    "parse_ps_line",
    "remove_legacy_calibre_plugins",
    "run_checked",
    "shutdown_calibre_gui",
    "start_calibre_gui",
    "wait_for_gui",
]
