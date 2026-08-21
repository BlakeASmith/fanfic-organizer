"""Tests for host-wide AO3 rate limiting and robots.txt policy."""

from __future__ import annotations

from pathlib import Path

import pytest

import ao3_rate
from ao3_rate import (
    DEFAULT_MIN_INTERVAL,
    HEAVY_MIN_INTERVAL,
    configure_min_interval,
    interval_for_url,
    load_robots_text,
    note_retry_after,
    path_is_robots_disallow,
    reset_rate_limit_state,
    wait_for_request,
)
from ao3kit.rate_store import SharedRateStore


@pytest.fixture(autouse=True)
def _isolate_rate_state():
    reset_rate_limit_state(memory=True)
    yield
    reset_rate_limit_state(memory=True)


def test_path_is_robots_disallow_matches_ao3_star_rules():
    assert path_is_robots_disallow(
        "https://archiveofourown.org/downloads/1/Title.epub?updated_at=1"
    )
    assert path_is_robots_disallow(
        "https://archiveofourown.org/works?commit=Sort+and+Filter&work_search[sort_column]=kudos_count"
    )
    assert path_is_robots_disallow("https://archiveofourown.org/tags/search?query=foo")
    assert not path_is_robots_disallow("https://archiveofourown.org/works/90860171")


def test_interval_for_url_is_heavier_on_disallow_paths():
    work = interval_for_url("https://archiveofourown.org/works/1")
    tag = interval_for_url("https://archiveofourown.org/tags/Fluff")
    listing = interval_for_url("https://archiveofourown.org/works?commit=Search")
    download = interval_for_url("https://archiveofourown.org/downloads/1/x.epub")
    assert work == pytest.approx(DEFAULT_MIN_INTERVAL)
    assert tag == pytest.approx(ao3_rate.TAG_SOFT_INTERVAL)
    assert listing >= HEAVY_MIN_INTERVAL
    assert download >= HEAVY_MIN_INTERVAL


def test_crawl_delay_raises_floor():
    load_robots_text("User-agent: *\nCrawl-delay: 12\nDisallow:\n")
    assert ao3_rate._STATE.crawl_delay == 12
    assert interval_for_url("https://archiveofourown.org/works/1") >= 12
    assert interval_for_url("https://archiveofourown.org/tags/Fluff") >= 12


def test_configure_min_interval_respects_absolute_floor():
    assert configure_min_interval(0.1) >= ao3_rate.ABSOLUTE_MIN_INTERVAL
    assert configure_min_interval(1.0) == pytest.approx(1.0)
    assert configure_min_interval(20.0) >= 20.0


def test_configure_min_interval_can_lower_previous_value():
    configure_min_interval(5.0)
    assert configure_min_interval(2.0) == pytest.approx(2.0)


def test_note_request_success_speeds_tag_lane():
    ao3_rate._STATE.tag_interval = 2.0
    ao3_rate._STATE.success_streak = ao3_rate.SUCCESS_STREAK_TO_SPEED_UP - 1
    ao3_rate.note_request_success("https://archiveofourown.org/tags/Fluff")
    assert ao3_rate._STATE.tag_interval < 2.0


def test_wait_for_request_spaces_calls(monkeypatch: pytest.MonkeyPatch):
    ao3_rate._STATE.skip_wait = False
    monkeypatch.setattr("ao3_rate.random.uniform", lambda _a, _b: 1.0)
    clock = [1000.0]
    sleeps: list[float] = []
    monkeypatch.setattr("ao3_rate.time.time", lambda: clock[0])
    monkeypatch.setattr("ao3kit.rate_store.time.time", lambda: clock[0])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("ao3_rate.time.sleep", fake_sleep)

    # Seed next_allowed so first call does not wait.
    ao3_rate._STATE.store.update(
        lambda snap: type(snap)(
            next_allowed_at=clock[0],
            base_interval=snap.base_interval,
            tag_interval=snap.tag_interval,
            success_streak=snap.success_streak,
            crawl_delay=snap.crawl_delay,
        )
    )

    assert wait_for_request("https://archiveofourown.org/works/1") == 0.0
    waited = wait_for_request("https://archiveofourown.org/works/1")
    assert waited == pytest.approx(DEFAULT_MIN_INTERVAL)
    assert sleeps == pytest.approx([DEFAULT_MIN_INTERVAL])


def test_note_retry_after_raises_session_floor():
    note_retry_after(10)
    assert ao3_rate._STATE.base_interval >= 10


def test_shared_store_coordinates_two_connections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "rate.sqlite"
    store_a = SharedRateStore(db)
    store_b = SharedRateStore(db)
    monkeypatch.setattr("ao3kit.rate_store.time.time", lambda: 5000.0)

    wait_a, snap_a = store_a.claim_slot(2.0)
    wait_b, snap_b = store_b.claim_slot(2.0)

    assert wait_a == pytest.approx(0.0)
    assert wait_b == pytest.approx(2.0)
    assert snap_b.next_allowed_at == pytest.approx(snap_a.next_allowed_at + 2.0)

    store_a.close()
    store_b.close()


def test_host_file_shared_across_reset(tmp_path: Path):
    db = tmp_path / "shared.sqlite"
    reset_rate_limit_state(path=db, seed=True)
    configure_min_interval(7.0)
    note_retry_after(3)

    # New process-equivalent: new RateLimitState on same file without wiping.
    reset_rate_limit_state(path=db, seed=False)
    assert ao3_rate._STATE.base_interval >= 7.0
