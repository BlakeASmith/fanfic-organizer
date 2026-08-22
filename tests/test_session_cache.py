"""Tests for cached AO3 session cookies."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from requests.cookies import RequestsCookieJar

from ao3kit.session_cache import (
    SESSION_MAX_AGE_DAYS,
    clear_session_cache,
    cookies_look_authenticated,
    load_session_cookies,
    save_session_cookies,
)


def _auth_jar() -> RequestsCookieJar:
    jar = RequestsCookieJar()
    jar.set(
        "user_credentials",
        "token",
        domain="archiveofourown.org",
        path="/",
    )
    return jar


def test_save_and_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AO3KIT_SESSION_CACHE", "1")
    path = tmp_path / "ao3_session.json"
    saved = save_session_cookies("Emily", _auth_jar(), path=path)
    assert saved == path
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = load_session_cookies("emily", path=path)
    assert loaded is not None
    assert cookies_look_authenticated(loaded)
    assert loaded["user_credentials"] == "token"


def test_load_rejects_other_username(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AO3KIT_SESSION_CACHE", "1")
    path = tmp_path / "ao3_session.json"
    save_session_cookies("emily", _auth_jar(), path=path)
    assert load_session_cookies("blake", path=path) is None


def test_load_rejects_stale_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AO3KIT_SESSION_CACHE", "1")
    path = tmp_path / "ao3_session.json"
    old = time.time() - (SESSION_MAX_AGE_DAYS + 1) * 86400
    save_session_cookies("emily", _auth_jar(), path=path, now=old)
    assert load_session_cookies("emily", path=path) is None


def test_disabled_cache_does_not_read_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AO3KIT_SESSION_CACHE", "0")
    path = tmp_path / "ao3_session.json"
    assert save_session_cookies("emily", _auth_jar(), path=path) is None
    assert not path.exists()
    path.write_text("{}", encoding="utf-8")
    assert load_session_cookies("emily", path=path) is None


def test_clear_session_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AO3KIT_SESSION_CACHE", "1")
    path = tmp_path / "ao3_session.json"
    save_session_cookies("emily", _auth_jar(), path=path)
    clear_session_cache(path)
    assert not path.exists()
    clear_session_cache(path)
