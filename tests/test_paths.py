"""XDG Base Directory locations for fanfic-organizer user files."""

from __future__ import annotations

from pathlib import Path

import pytest

from ao3kit import paths


def test_xdg_homes_win_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cch"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    monkeypatch.delenv("AO3KIT_HOME", raising=False)
    monkeypatch.delenv("AO3KIT_CONFIG_DIR", raising=False)
    monkeypatch.delenv("AO3KIT_CACHE_DIR", raising=False)
    monkeypatch.delenv("AO3KIT_STATE_DIR", raising=False)

    assert paths.config_dir() == tmp_path / "cfg" / "fanfic-organizer"
    assert paths.cache_dir() == tmp_path / "cch" / "fanfic-organizer"
    assert paths.state_dir() == tmp_path / "st" / "fanfic-organizer"
    assert paths.jobs_dir() == tmp_path / "st" / "fanfic-organizer" / "jobs"
    assert paths.tag_cache_file().name == "ao3_tag_cache.sqlite"
    assert paths.rate_db_file().parent == paths.state_dir()
    assert paths.session_file().parent == paths.state_dir()


def test_relative_xdg_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "not-absolute")
    monkeypatch.delenv("AO3KIT_HOME", raising=False)
    monkeypatch.delenv("AO3KIT_CONFIG_DIR", raising=False)
    # Autouse isolate_xdg already set an absolute XDG_CONFIG_HOME; overwrite with relative.
    got = paths.config_dir()
    assert got.is_absolute()
    assert "not-absolute" not in str(got)


def test_ao3kit_home_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "custom-home"
    monkeypatch.setenv("AO3KIT_HOME", str(home))
    assert paths.config_dir() == home.resolve()


def test_checkout_ao3kit_is_ignored_when_xdg_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "repo"
    leftover = checkout / ".ao3kit"
    leftover.mkdir(parents=True)
    (leftover / "config.yaml").write_text("request_delay: 2\n", encoding="utf-8")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(paths, "_home", lambda: fake_home)
    for name in (
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "AO3KIT_HOME",
        "AO3KIT_CONFIG_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    assert paths.config_dir() == fake_home / ".config" / "fanfic-organizer"
    assert leftover not in paths.config_dir().parents
    assert paths.session_file() == (
        fake_home / ".local" / "state" / "fanfic-organizer" / "ao3_session.json"
    )


def test_python_stamp_is_under_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    project = tmp_path / "proj"
    project.mkdir()
    stamp = paths.python_stamp_file(project)
    assert stamp.parent == tmp_path / "st" / "fanfic-organizer" / "python-stamps"
    assert stamp.name != "python"


def test_calibre_lock_uses_runtime_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    assert paths.calibre_lock_file().parent == tmp_path / "run" / "fanfic-organizer"
