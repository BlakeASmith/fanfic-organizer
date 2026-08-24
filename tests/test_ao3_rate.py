"""Tests for host-wide AO3 rate limiting and robots.txt policy."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ao3kit import rate as ao3_rate
from ao3kit.rate import (
    DEFAULT_MIN_INTERVAL,
    configure_min_interval,
    ensure_rate_limits,
    interval_for_url,
    load_robots_text,
    note_retry_after,
    path_is_robots_disallow,
    rate_report,
    record_request_event,
    reset_rate_limit_state,
    url_kind,
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


def test_interval_for_url_search_and_download_match_work_lane():
    work = interval_for_url("https://archiveofourown.org/works/1")
    tag = interval_for_url("https://archiveofourown.org/tags/Fluff")
    listing = interval_for_url("https://archiveofourown.org/works?commit=Search")
    download = interval_for_url("https://archiveofourown.org/downloads/1/x.epub")
    assert work == pytest.approx(DEFAULT_MIN_INTERVAL)
    assert listing == pytest.approx(DEFAULT_MIN_INTERVAL)
    assert download == pytest.approx(DEFAULT_MIN_INTERVAL)
    assert tag == pytest.approx(ao3_rate.TAG_SOFT_INTERVAL)


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


def test_work_search_and_download_share_engine_floor():
    ao3_rate.ensure_rate_limits()
    work = interval_for_url("https://archiveofourown.org/works/1")
    listing = interval_for_url("https://archiveofourown.org/works?commit=Search")
    download = interval_for_url("https://archiveofourown.org/downloads/1/x.epub")
    assert work == pytest.approx(DEFAULT_MIN_INTERVAL)
    assert listing == pytest.approx(DEFAULT_MIN_INTERVAL)
    assert download == pytest.approx(DEFAULT_MIN_INTERVAL)


def test_ensure_rate_limits_uses_engine_floor():
    ao3_rate._STATE.base_interval = 0.4
    ensure_rate_limits()
    assert interval_for_url("https://archiveofourown.org/works/1") >= DEFAULT_MIN_INTERVAL
    assert interval_for_url(
        "https://archiveofourown.org/downloads/1/x.epub"
    ) >= DEFAULT_MIN_INTERVAL


def test_configure_min_interval_raises_tag_lane_with_scrape_delay():
    ao3_rate._STATE.tag_interval = 0.4
    configure_min_interval(1.5)
    assert ao3_rate._STATE.tag_interval >= ao3_rate.TAG_SOFT_INTERVAL
    assert interval_for_url("https://archiveofourown.org/tags/Fluff") >= ao3_rate.TAG_SOFT_INTERVAL


def test_interval_for_url_clamps_persisted_subfloor_tag():
    ao3_rate._STATE.tag_interval = 0.1
    assert interval_for_url("https://archiveofourown.org/tags/Fluff") >= ao3_rate.ABSOLUTE_MIN_INTERVAL


def test_note_request_success_speeds_tag_lane():
    ao3_rate._STATE.tag_interval = 2.0
    ao3_rate._STATE.success_streak = ao3_rate.SUCCESS_STREAK_TO_SPEED_UP - 1
    ao3_rate.note_request_success("https://archiveofourown.org/tags/Fluff")
    assert ao3_rate._STATE.tag_interval < 2.0


def test_wait_for_request_spaces_calls(monkeypatch: pytest.MonkeyPatch):
    ao3_rate._STATE.skip_wait = False
    monkeypatch.setattr("ao3kit.rate.random.uniform", lambda _a, _b: 1.0)
    clock = [1000.0]
    sleeps: list[float] = []
    monkeypatch.setattr("ao3kit.rate.time.time", lambda: clock[0])
    monkeypatch.setattr("ao3kit.rate_store.time.time", lambda: clock[0])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("ao3kit.rate.time.sleep", fake_sleep)

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


def test_note_retry_after_pauses_host_without_raising_cruise():
    before = ao3_rate._STATE.base_interval
    now = time.time()
    note_retry_after(10)
    snap = ao3_rate._STATE.store.read()
    assert snap.next_allowed_at >= now + 9.0
    assert snap.base_interval == pytest.approx(before)


def test_interval_for_url_login_ignores_scrape_and_429_floor():
    configure_min_interval(5.0)
    login = "https://archiveofourown.org/users/login"
    assert interval_for_url(login) == pytest.approx(ao3_rate.LOGIN_MIN_INTERVAL)
    note_retry_after(60)
    assert interval_for_url(login) == pytest.approx(ao3_rate.LOGIN_MIN_INTERVAL)
    assert interval_for_url("https://archiveofourown.org/works/1") == pytest.approx(5.0)


def test_note_retry_after_tag_url_pauses_whole_host():
    configure_min_interval(1.5)
    before = time.time()
    note_retry_after(202, url="https://archiveofourown.org/tags/Humor")
    snap = ao3_rate._STATE.store.read()
    assert snap.next_allowed_at >= before + 201.5
    assert snap.base_interval == pytest.approx(1.5)
    assert snap.tag_interval >= 2.0


def test_wait_for_request_login_caps_leftover(monkeypatch: pytest.MonkeyPatch):
    ao3_rate._STATE.skip_wait = False
    monkeypatch.setattr("ao3kit.rate.random.uniform", lambda _a, _b: 1.0)
    clock = [1000.0]
    sleeps: list[float] = []
    monkeypatch.setattr("ao3kit.rate.time.time", lambda: clock[0])
    monkeypatch.setattr("ao3kit.rate_store.time.time", lambda: clock[0])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("ao3kit.rate.time.sleep", fake_sleep)
    ao3_rate._STATE.store.update(
        lambda snap: type(snap)(
            next_allowed_at=clock[0] + 54.0,
            base_interval=5.0,
            tag_interval=snap.tag_interval,
            success_streak=snap.success_streak,
            crawl_delay=snap.crawl_delay,
        )
    )
    waited = wait_for_request("https://archiveofourown.org/users/login")
    assert waited == pytest.approx(ao3_rate.LOGIN_MAX_WAIT)
    assert sleeps == pytest.approx([ao3_rate.LOGIN_MAX_WAIT])
    leftover = ao3_rate._STATE.store.read().next_allowed_at - clock[0]
    assert leftover == pytest.approx(ao3_rate.LOGIN_MIN_INTERVAL)


def test_wait_for_request_stale_lock_rewinds(monkeypatch: pytest.MonkeyPatch):
    ao3_rate._STATE.skip_wait = False
    monkeypatch.setattr("ao3kit.rate.random.uniform", lambda _a, _b: 1.0)
    clock = [1000.0]
    sleeps: list[float] = []
    monkeypatch.setattr("ao3kit.rate.time.time", lambda: clock[0])
    monkeypatch.setattr("ao3kit.rate_store.time.time", lambda: clock[0])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("ao3kit.rate.time.sleep", fake_sleep)
    ao3_rate._STATE.store.update(
        lambda snap: type(snap)(
            next_allowed_at=clock[0] + 400.0,
            base_interval=snap.base_interval,
            tag_interval=snap.tag_interval,
            success_streak=snap.success_streak,
            crawl_delay=snap.crawl_delay,
        )
    )
    waited = wait_for_request("https://archiveofourown.org/works/1")
    assert waited == pytest.approx(ao3_rate.STALE_LOCK_WAIT)
    leftover = ao3_rate._STATE.store.read().next_allowed_at - clock[0]
    assert leftover == pytest.approx(DEFAULT_MIN_INTERVAL)


def test_wait_for_request_honors_retry_after_cooldown(
    monkeypatch: pytest.MonkeyPatch,
):
    """Cloudflare ~3 min Retry-After must not be treated as a stale lock."""
    ao3_rate._STATE.skip_wait = False
    monkeypatch.setattr("ao3kit.rate.random.uniform", lambda _a, _b: 1.0)
    clock = [1000.0]
    sleeps: list[float] = []
    monkeypatch.setattr("ao3kit.rate.time.time", lambda: clock[0])
    monkeypatch.setattr("ao3kit.rate_store.time.time", lambda: clock[0])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("ao3kit.rate.time.sleep", fake_sleep)
    ao3_rate._STATE.store.update(
        lambda snap: type(snap)(
            next_allowed_at=clock[0] + 178.0,
            base_interval=snap.base_interval,
            tag_interval=snap.tag_interval,
            success_streak=snap.success_streak,
            crawl_delay=snap.crawl_delay,
        )
    )
    waited = wait_for_request("https://archiveofourown.org/works/1")
    assert waited == pytest.approx(178.0)
    assert sleeps == pytest.approx([178.0])


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


def test_url_kind_classifies_common_paths():
    assert url_kind("https://archiveofourown.org/works/1") == "work"
    assert url_kind("https://archiveofourown.org/tags/Fluff") == "tag"
    assert (
        url_kind(
            "https://archiveofourown.org/works?commit=Sort+and+Filter"
        )
        == "search"
    )
    assert (
        url_kind("https://archiveofourown.org/downloads/1/Title.epub")
        == "download"
    )
    assert url_kind("https://archiveofourown.org/users/login") == "login"
    assert url_kind("https://archiveofourown.org/tags/search?query=x") == "search"


def test_request_log_records_and_summarizes():
    record_request_event(
        url="https://archiveofourown.org/works/1",
        method="GET",
        outcome="ok",
        wait_s=1.2,
        interval_s=1.0,
        elapsed_s=0.3,
        status=200,
    )
    record_request_event(
        url="https://archiveofourown.org/works/2?view_adult=true",
        method="GET",
        outcome="429",
        wait_s=1.0,
        interval_s=1.0,
        elapsed_s=0.1,
        status=429,
        retry_after_s=7,
        retry_after_from_header=True,
    )
    report = rate_report()
    totals = report["stats"]["totals"]
    assert totals["requests"] == 2
    assert totals["ok"] == 1
    assert totals["429"] == 1
    assert report["stats"]["by_kind"]["work"]["requests"] == 2
    assert report["stats"]["retry_after"]["with_header"] == 1
    assert report["recent"][0]["path"] == "/works/2"
    assert report["recent"][0]["outcome"] == "429"
    assert report["recent"][0]["base_interval"] is not None
    assert any(
        row["kind"] == "work" and row["429"] == 1
        for row in report["interval_vs_429"]
    )
    assert report["hourly_totals"][0]["requests"] == 2
    assert ao3_rate.clear_rate_events() == 2
    assert rate_report()["stats"]["totals"]["requests"] == 0
    assert rate_report()["hourly_totals"][0]["requests"] == 2


def test_rate_cli_json(capsys):
    record_request_event(
        url="https://archiveofourown.org/tags/Fluff",
        method="GET",
        outcome="ok",
        status=200,
    )
    assert ao3_rate.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["stats"]["totals"]["ok"] == 1
    assert report["stats"]["by_kind"]["tag"]["requests"] == 1
    assert ao3_rate.main(["--clear"]) == 0
    cleared = json.loads(capsys.readouterr().out)
    assert cleared["cleared_events"] == 1


def test_ensure_robots_uses_baked_in_without_network(monkeypatch: pytest.MonkeyPatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("should not fetch robots.txt")

    monkeypatch.setattr("requests.get", boom)
    reset_rate_limit_state(memory=True)
    ao3_rate._STATE.skip_wait = True
    ao3_rate.ensure_robots()
    assert ao3_rate.robots_loaded()
    assert "Disallow: /downloads/" in ao3_rate._STATE.robots_text


def test_ensure_robots_reuses_disk_cache(tmp_path: Path):
    db = tmp_path / "rate.sqlite"
    reset_rate_limit_state(path=db, seed=True)
    ao3_rate._STATE.skip_wait = True
    ao3_rate._STATE.store.write_robots(
        "User-agent: *\nCrawl-delay: 9\nDisallow:\n",
        time.time(),
    )
    reset_rate_limit_state(path=db, seed=False)
    ao3_rate._STATE.skip_wait = True
    assert ao3_rate._STATE.robots is None
    ao3_rate.ensure_robots()
    assert ao3_rate._STATE.crawl_delay == 9


def test_ensure_robots_fetcher_writes_cache(tmp_path: Path):
    db = tmp_path / "rate.sqlite"
    reset_rate_limit_state(path=db, seed=True)
    ao3_rate.ensure_robots(fetcher=lambda: "User-agent: *\nCrawl-delay: 4\n")
    assert ao3_rate._STATE.crawl_delay == 4
    cached = ao3_rate._STATE.store.read_robots()
    assert cached is not None
    assert "Crawl-delay: 4" in cached[0]


def test_hourly_rollups_span_hours():
    from ao3kit.rate_store import RateEvent, hour_bucket

    store = ao3_rate._STATE.store
    now_hour = hour_bucket(time.time())
    t0 = float(now_hour - 3600)
    t1 = float(now_hour)
    for ts, outcome, interval in (
        (t0, "ok", 1.0),
        (t0, "429", 1.0),
        (t1, "ok", 2.0),
    ):
        store.record_event(
            RateEvent(
                ts=ts,
                kind="work",
                method="GET",
                path="/works/1",
                status=200 if outcome == "ok" else 429,
                outcome=outcome,
                wait_s=0.5,
                interval_s=interval,
                elapsed_s=0.2,
                retry_after_s=8.0 if outcome == "429" else None,
                retry_after_from_header=True if outcome == "429" else None,
                attempt=1,
            )
        )
    series = {row["hour_ts"]: row for row in store.hourly_series() if row["kind"] == "work"}
    first = series[hour_bucket(t0)]
    second = series[hour_bucket(t1)]
    assert first["requests"] == 2
    assert first["429"] == 1
    assert first["retry_after_header"] == 1
    assert second["requests"] == 1
    assert second["avg_interval_s"] == pytest.approx(2.0)


def test_rate_cli_export_hourly_jsonl(tmp_path: Path):
    record_request_event(
        url="https://archiveofourown.org/tags/Fluff",
        method="GET",
        outcome="ok",
        interval_s=0.5,
        status=200,
    )
    dest = tmp_path / "hourly.jsonl"
    assert ao3_rate.main(["export", "--hourly", "-o", str(dest)]) == 0
    rows = [json.loads(line) for line in dest.read_text().splitlines() if line]
    assert len(rows) == 1
    assert rows[0]["kind"] == "tag"
    assert rows[0]["requests"] == 1
    assert rows[0]["ok"] == 1


def test_suggestions_flag_high_429_rate():
    for i in range(20):
        record_request_event(
            url="https://archiveofourown.org/works/1",
            method="GET",
            outcome="429" if i < 4 else "ok",
            interval_s=1.0,
            status=429 if i < 4 else 200,
            retry_after_s=12 if i < 4 else None,
            retry_after_from_header=True if i < 4 else None,
        )
    hints = ao3_rate.rate_suggestions(
        ao3_rate._STATE.store.read(),
        ao3_rate._STATE.store.event_stats(),
    )
    raise_hints = [h for h in hints if h["severity"] == "raise" and h["kind"] == "work"]
    assert raise_hints
    retry_hints = [h for h in hints if h["kind"] == "retry_after"]
    assert retry_hints



