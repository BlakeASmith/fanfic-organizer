"""Isolate process-wide AO3 rate limiting and XDG dirs in tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ao3kit import rate as ao3_rate

# Mirrors archiveofourown.org/robots.txt User-agent: * (no Crawl-delay).
AO3_ROBOTS_STAR = """\
User-agent: *
Disallow: /works?
Disallow: /autocomplete/
Disallow: /downloads/
Disallow: /external_works/
Disallow: /bookmarks/search?
Disallow: /people/search?
Disallow: /tags/search?
Disallow: /works/search?
"""


@pytest.fixture(autouse=True)
def isolate_xdg(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(root / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(root / "runtime"))
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(autouse=True)
def isolate_ao3_session_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AO3KIT_SESSION_CACHE", "0")


@pytest.fixture(autouse=True)
def isolate_ao3_rate() -> None:
    ao3_rate.reset_rate_limit_state()
    ao3_rate.load_robots_text(AO3_ROBOTS_STAR)
    ao3_rate._STATE.skip_wait = True
    yield
    ao3_rate.reset_rate_limit_state()
