from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from calibre_dev.calibre_install import (
    DEFAULT_LINUX_INSTALL_DIR,
    ensure_calibre_customize,
    install_calibre_linux_isolated,
    isolated_calibre_bin_dirs,
    linux_isolated_install_dir,
    try_find_calibre_tool,
)


def test_linux_isolated_install_dir_default():
    assert linux_isolated_install_dir() == DEFAULT_LINUX_INSTALL_DIR.resolve()


def test_linux_isolated_install_dir_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("FANFIC_ORGANIZER_CALIBRE_DIR", str(tmp_path / "calibre"))
    assert linux_isolated_install_dir() == (tmp_path / "calibre").resolve()


def test_isolated_calibre_bin_dirs_includes_linux_default():
    dirs = isolated_calibre_bin_dirs()
    assert DEFAULT_LINUX_INSTALL_DIR / "calibre" in dirs


def test_install_calibre_linux_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    install_root = tmp_path / "opt"
    customize = install_root / "calibre" / "calibre-customize"

    def fake_download(url: str, *, timeout: float = 300) -> bytes:
        assert url.endswith("linux-installer.sh")
        return b"#!/bin/sh\n"

    def fake_run(argv, **kwargs):
        customize.parent.mkdir(parents=True, exist_ok=True)
        customize.write_text("stub\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(
        "calibre_dev.calibre_install._download_bytes",
        fake_download,
    )
    monkeypatch.setattr(
        "calibre_dev.calibre_install._run_checked",
        fake_run,
    )

    bin_dir = install_calibre_linux_isolated(install_root)
    assert bin_dir == install_root / "calibre"
    assert customize.is_file()


def test_ensure_calibre_customize_installs_when_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = {"count": 0}

    def fake_try(name: str) -> str | None:
        if calls["count"] == 0:
            calls["count"] += 1
            return None
        return "/new/calibre-customize"

    install = MagicMock()
    monkeypatch.setattr(
        "calibre_dev.calibre_install.try_find_calibre_tool",
        fake_try,
    )
    monkeypatch.setattr("calibre_dev.calibre_install.install_calibre", install)

    path = ensure_calibre_customize(install_if_missing=True)
    assert path == "/new/calibre-customize"
    install.assert_called_once()


def test_ensure_calibre_customize_skips_install_when_present(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "calibre_dev.calibre_install.try_find_calibre_tool",
        lambda name: "/usr/bin/calibre-customize",
    )
    install = MagicMock()
    monkeypatch.setattr("calibre_dev.calibre_install.install_calibre", install)
    path = ensure_calibre_customize(install_if_missing=True)
    assert path == "/usr/bin/calibre-customize"
    install.assert_not_called()


def test_ensure_calibre_customize_raises_when_missing_and_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "calibre_dev.calibre_install.try_find_calibre_tool",
        lambda name: None,
    )
    with pytest.raises(FileNotFoundError, match="calibre-customize not found"):
        ensure_calibre_customize(install_if_missing=False)


def test_try_find_calibre_tool_uses_which(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "calibre_dev.calibre_install.shutil.which",
        lambda name: "/opt/calibre/calibre-customize",
    )
    assert try_find_calibre_tool("calibre-customize") == "/opt/calibre/calibre-customize"
