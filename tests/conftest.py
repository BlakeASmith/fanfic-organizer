"""Isolate the process-wide AO3 rate limiter in tests."""

from __future__ import annotations

import pytest

import ao3_rate

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
def isolate_ao3_session_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AO3KIT_SESSION_CACHE", "0")


@pytest.fixture(autouse=True)
def isolate_ao3_rate() -> None:
    ao3_rate.reset_rate_limit_state()
    ao3_rate.load_robots_text(AO3_ROBOTS_STAR)
    ao3_rate._STATE.skip_wait = True
    yield
    ao3_rate.reset_rate_limit_state()
