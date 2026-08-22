from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from calibre_dev.calibre import (
    CalibreCtl,
    is_calibre_gui_command,
    list_calibre_gui_pids,
    parse_ps_line,
)
from calibre_dev.lock import RestartLocked, hold_restart_lock, lock_snapshot
import makeplugin

ROOT = Path(__file__).resolve().parents[1]


def test_is_calibre_gui_command_matches_app_binary_only():
    assert is_calibre_gui_command(
        "/Applications/calibre.app/Contents/MacOS/calibre"
    )
    assert is_calibre_gui_command(r"C:\Calibre2\calibre.exe")
    assert not is_calibre_gui_command(
        "/Applications/calibre.app/Contents/MacOS/calibre-customize -b /tmp"
    )
    assert not is_calibre_gui_command(
        "/Applications/calibre.app/Contents/MacOS/calibre-parallel"
    )
    assert not is_calibre_gui_command(
        "/Applications/calibre.app/Contents/MacOS/calibre --shutdown-running-calibre"
    )
    assert not is_calibre_gui_command("ebook-viewer /tmp/book.epub")
    assert not is_calibre_gui_command("")


def test_list_calibre_gui_pids_from_ps_text():
    ps = """
  111 /Applications/calibre.app/Contents/MacOS/calibre
  222 /Applications/calibre.app/Contents/MacOS/calibre-customize -b /repo
  333 /Applications/calibre.app/Contents/MacOS/calibre-parallel
  444 /Applications/calibre.app/Contents/MacOS/calibre --shutdown-running-calibre
  not-a-pid something
"""
    assert list_calibre_gui_pids(ps_output=ps) == [111]
    assert parse_ps_line("  42 /bin/calibre") == (42, "/bin/calibre")


class FakeCtl(CalibreCtl):
    def __init__(self, tmp_path: Path, pids: list[int] | None = None):
        super().__init__(lock_path=tmp_path / "calibre_restart.lock", plugin_dir=tmp_path)
        self.pids = list(pids or [])
        self.actions: list[str] = []
        self.stay_alive = False
        self.start_ok = True
        self.fail_install = False

    def _gui_pids(self) -> list[int]:
        return list(self.pids)

    def _find_calibre(self) -> str:
        return "/fake/calibre"

    def _find_customize(self) -> str:
        return "/fake/calibre-customize"

    def _install(self) -> None:
        if self.fail_install:
            raise FileNotFoundError("calibre-customize not found")
        self.actions.append("install")

    def _shutdown(self) -> None:
        self.actions.append("shutdown")
        if not self.stay_alive:
            self.pids.clear()

    def _start(self) -> None:
        self.actions.append("start")
        if self.start_ok:
            self.pids = [99]

    def _signal(self, pid: int, sig: int) -> None:
        self.actions.append(f"signal:{sig}")

    def _wait_gui(self, *, running: bool, timeout: float) -> bool:
        return bool(self._gui_pids()) == running


def test_install_without_restart_does_not_touch_gui(tmp_path: Path):
    ctl = FakeCtl(tmp_path, pids=[7])
    result = ctl.install(restart=False)
    assert result["ok"] is True
    assert result["restarted"] is False
    assert ctl.actions == ["install"]
    assert ctl.pids == [7]


def test_install_with_restart_shuts_down_and_starts(tmp_path: Path):
    ctl = FakeCtl(tmp_path, pids=[7])
    result = ctl.install(restart=True, agent_id="ui-pass")
    assert result["ok"] is True
    assert result["installed"] is True
    assert result["restarted"] is True
    assert ctl.actions == ["install", "shutdown", "start"]
    assert ctl.pids == [99]


def test_restart_reports_locked_when_another_holder_is_active(tmp_path: Path):
    lock_path = tmp_path / "calibre_restart.lock"
    ctl = FakeCtl(tmp_path, pids=[7])
    started = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with hold_restart_lock("other", path=lock_path, agent_id="agent-a", timeout=0):
            started.set()
            release.wait(2)

    thread = threading.Thread(target=hold)
    thread.start()
    assert started.wait(1)
    result = ctl.restart(agent_id="agent-b", lock_timeout=0)
    release.set()
    thread.join(2)
    assert result["ok"] is False
    assert result["error"] == "locked"
    assert result["agent_id"] == "agent-a"
    assert ctl.actions == []


def test_restart_lock_blocks_a_second_process(tmp_path: Path):
    lock_path = tmp_path / "cross.lock"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys, time\n"
                "from pathlib import Path\n"
                "from calibre_dev.lock import hold_restart_lock\n"
                "path = Path(sys.argv[1])\n"
                "with hold_restart_lock('child', path=path, agent_id='child', timeout=0):\n"
                "    time.sleep(2)\n"
            ),
            str(lock_path),
        ],
        cwd=str(ROOT),
    )
    snapshot = None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = lock_snapshot(lock_path)
        if snapshot:
            break
        time.sleep(0.05)
    try:
        assert snapshot is not None
        with pytest.raises(RestartLocked) as exc:
            with hold_restart_lock("parent", path=lock_path, timeout=0):
                pass
        assert exc.value.snapshot.get("agent_id") == "child"
    finally:
        child.terminate()
        child.wait(timeout=3)


def test_makeplugin_install_defaults_to_no_restart(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    class Fake:
        def install(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "message": "installed", "restarted": False}

    monkeypatch.setattr(makeplugin, "_ctl", lambda: Fake())
    assert makeplugin.main(["install"]) == 0
    assert calls == [
        {
            "restart": False,
            "agent_id": "",
            "lock_timeout": 15.0,
            "holder": "makeplugin:install",
        }
    ]


def test_makeplugin_install_restart_flag(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    class Fake:
        def install(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "message": "restarted", "restarted": True}

    monkeypatch.setattr(makeplugin, "_ctl", lambda: Fake())
    assert makeplugin.main(["install", "--restart", "--agent-id", "iter"]) == 0
    assert calls[0]["restart"] is True
    assert calls[0]["agent_id"] == "iter"


def test_install_restart_locked_still_installed(tmp_path: Path):
    lock_path = tmp_path / "calibre_restart.lock"
    ctl = FakeCtl(tmp_path, pids=[7])
    started = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with hold_restart_lock("other", path=lock_path, agent_id="agent-a", timeout=0):
            started.set()
            release.wait(2)

    thread = threading.Thread(target=hold)
    thread.start()
    assert started.wait(1)
    result = ctl.install(restart=True, lock_timeout=0)
    release.set()
    thread.join(2)
    assert result["ok"] is False
    assert result["error"] == "locked"
    assert result["installed"] is True
    assert ctl.actions == ["install"]
    assert "Plugin installed" in result["message"]


def test_makeplugin_restart_flag_requires_install():
    with pytest.raises(SystemExit):
        makeplugin.main(["--restart"])


def test_makeplugin_legacy_dash_i(monkeypatch: pytest.MonkeyPatch):
    class Fake:
        def install(self, **kwargs):
            return {"ok": True, "message": "installed"}

    monkeypatch.setattr(makeplugin, "_ctl", lambda: Fake())
    assert makeplugin.main(["-i"]) == 0


def test_mcp_tools_default_install_does_not_restart(tmp_path: Path):
    pytest.importorskip("fastmcp")
    from calibre_dev.mcp import create_server

    ctl = FakeCtl(tmp_path, pids=[3])
    app = create_server(ctl=ctl)
    tools = asyncio.run(app.get_tools())
    assert set(tools) >= {"calibre_status", "install_plugin", "restart_calibre"}
    install = tools["install_plugin"]
    result = install.fn(restart=False)
    assert result["restarted"] is False
    assert ctl.actions == ["install"]
