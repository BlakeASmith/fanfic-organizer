"""Host-wide AO3 rate limiting and robots.txt policy.

AO3's robots.txt (User-agent: *) has no Crawl-delay, but disallows
``/downloads/`` and ``/works?`` so search engines do not index those URLs.
This tool still fetches work pages, user-requested search listings, and
native EPUB files for personal library backup.

All interfaces (CLI, web UI, REST API, Calibre→ao3kit subprocess) share one
on-disk limiter (see ``ao3kit.rate_store``) so concurrent processes on the
same host pace together:

- adaptive spacing for light paths (tag profiles) — start fast, back off on pressure
- longer gaps for paths robots.txt marks Disallow (search / downloads)
- 429 / Cloudflare pressure raises the floor for every interface
"""

from __future__ import annotations

import random
import threading
import time
import urllib.robotparser
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from ao3kit.rate_store import (
    DEFAULT_RATE_DB_PATH,
    RateSnapshot,
    SharedRateStore,
    default_rate_db_path,
)

ROBOTS_URL = "https://archiveofourown.org/robots.txt"
# General work-page pacing when no adaptive tag lane applies.
DEFAULT_MIN_INTERVAL = 1.0
ABSOLUTE_MIN_INTERVAL = 0.4
# Tag profiles start here and adapt up/down based on AO3 responses.
TAG_SOFT_INTERVAL = 0.5
TAG_MAX_INTERVAL = 8.0
# Search listings and /downloads/ — keep a wider gap.
HEAVY_MIN_INTERVAL = 5.0
MAX_MIN_INTERVAL = 60.0
JITTER = 0.08
ROBOTS_TTL_SECONDS = 3600.0
SUCCESS_STREAK_TO_SPEED_UP = 8
USER_AGENT = "ao3-scraper/0.1 (+personal library backup; respects crawl-delay)"

StatusCallback = Callable[[str], None]


class RateLimitState:
    """Process-local robots cache + handle to the shared host-wide store."""

    def __init__(self, store: SharedRateStore | None = None) -> None:
        self.lock = threading.Lock()
        self.store = store or SharedRateStore(default_rate_db_path())
        self.robots: urllib.robotparser.RobotFileParser | None = None
        self.robots_loaded_at = 0.0
        self.robots_text = ""
        self.skip_wait = False

    @property
    def crawl_delay(self) -> float | None:
        return self.store.read().crawl_delay

    @property
    def base_interval(self) -> float:
        return self.store.read().base_interval

    @base_interval.setter
    def base_interval(self, value: float) -> None:
        def mutator(snap: RateSnapshot) -> RateSnapshot:
            return RateSnapshot(
                next_allowed_at=snap.next_allowed_at,
                base_interval=float(value),
                tag_interval=snap.tag_interval,
                success_streak=snap.success_streak,
                crawl_delay=snap.crawl_delay,
            )

        self.store.update(mutator)

    @property
    def tag_interval(self) -> float:
        return self.store.read().tag_interval

    @tag_interval.setter
    def tag_interval(self, value: float) -> None:
        def mutator(snap: RateSnapshot) -> RateSnapshot:
            return RateSnapshot(
                next_allowed_at=snap.next_allowed_at,
                base_interval=snap.base_interval,
                tag_interval=float(value),
                success_streak=snap.success_streak,
                crawl_delay=snap.crawl_delay,
            )

        self.store.update(mutator)

    @property
    def success_streak(self) -> int:
        return self.store.read().success_streak

    @success_streak.setter
    def success_streak(self, value: int) -> None:
        def mutator(snap: RateSnapshot) -> RateSnapshot:
            return RateSnapshot(
                next_allowed_at=snap.next_allowed_at,
                base_interval=snap.base_interval,
                tag_interval=snap.tag_interval,
                success_streak=int(value),
                crawl_delay=snap.crawl_delay,
            )

        self.store.update(mutator)

    @property
    def next_allowed_at(self) -> float:
        return self.store.read().next_allowed_at


_STATE = RateLimitState()


def reset_rate_limit_state(
    *,
    path: str | Path | None = None,
    memory: bool = True,
    seed: bool = True,
) -> None:
    """Test helper — restore defaults.

    By default uses an in-process memory DB so tests do not touch the host file.
    Pass ``path`` for a shared on-disk DB (multi-connection tests).
    ``seed=False`` rebinds to an existing DB without wiping intervals.
    """
    global _STATE
    if path is not None:
        store = SharedRateStore(Path(path))
    else:
        store = SharedRateStore(None)
    if seed:
        store.update(
            lambda _snap: RateSnapshot(
                next_allowed_at=time.time(),
                base_interval=DEFAULT_MIN_INTERVAL,
                tag_interval=TAG_SOFT_INTERVAL,
                success_streak=0,
                crawl_delay=None,
            )
        )
    _STATE = RateLimitState(store=store)
    _STATE.skip_wait = False


def _emit(on_status: StatusCallback | None, message: str) -> None:
    if on_status:
        on_status(message)


def load_robots_text(text: str, *, fetched_at: float | None = None) -> None:
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(text.splitlines())
    delay = parser.crawl_delay(USER_AGENT)
    if delay is None:
        delay = parser.crawl_delay("*")
    crawl = float(delay) if delay else None

    with _STATE.lock:
        _STATE.robots = parser
        _STATE.robots_text = text
        _STATE.robots_loaded_at = (
            fetched_at if fetched_at is not None else time.monotonic()
        )

    def mutator(snap: RateSnapshot) -> RateSnapshot:
        base = snap.base_interval
        tag = snap.tag_interval
        if crawl:
            base = max(base, crawl)
            tag = max(tag, crawl)
        return RateSnapshot(
            next_allowed_at=snap.next_allowed_at,
            base_interval=base,
            tag_interval=tag,
            success_streak=snap.success_streak,
            crawl_delay=crawl,
        )

    _STATE.store.update(mutator)


def robots_loaded() -> bool:
    return _STATE.robots is not None


def _floor(crawl_delay: float | None) -> float:
    floor = ABSOLUTE_MIN_INTERVAL
    if crawl_delay:
        floor = max(floor, crawl_delay)
    return floor


def configure_min_interval(requested: float | None) -> float:
    """Set the shared host-wide general interval (tag lane keeps its own pace)."""

    def mutator(snap: RateSnapshot) -> RateSnapshot:
        floor = _floor(snap.crawl_delay)
        if requested and requested > 0:
            base = max(floor, float(requested))
            tag = snap.tag_interval
            if float(requested) < tag:
                tag = max(floor, float(requested))
        else:
            base = max(snap.base_interval, floor, DEFAULT_MIN_INTERVAL)
            tag = snap.tag_interval
        return RateSnapshot(
            next_allowed_at=snap.next_allowed_at,
            base_interval=base,
            tag_interval=tag,
            success_streak=snap.success_streak,
            crawl_delay=snap.crawl_delay,
        )

    return _STATE.store.update(mutator).base_interval


def current_tag_interval() -> float:
    return _STATE.store.read().tag_interval


def note_retry_after(seconds: float) -> None:
    """AO3 sent 429 — wait at least this long and raise the shared floor."""
    pause = max(float(seconds), 1.0)
    now = time.time()

    def mutator(snap: RateSnapshot) -> RateSnapshot:
        return RateSnapshot(
            next_allowed_at=max(snap.next_allowed_at, now + pause),
            base_interval=min(max(snap.base_interval * 1.5, pause), MAX_MIN_INTERVAL),
            tag_interval=min(max(snap.tag_interval * 2.0, 2.0), TAG_MAX_INTERVAL),
            success_streak=0,
            crawl_delay=snap.crawl_delay,
        )

    _STATE.store.update(mutator)


def note_request_pressure(*, status_code: int | None = None) -> None:
    """Transient edge pressure (5xx / Cloudflare) — slow the shared tag lane."""
    del status_code  # reserved for future tuning

    def mutator(snap: RateSnapshot) -> RateSnapshot:
        return RateSnapshot(
            next_allowed_at=snap.next_allowed_at,
            base_interval=min(max(snap.base_interval * 1.2, 1.5), MAX_MIN_INTERVAL),
            tag_interval=min(max(snap.tag_interval * 1.5, 1.5), TAG_MAX_INTERVAL),
            success_streak=0,
            crawl_delay=snap.crawl_delay,
        )

    _STATE.store.update(mutator)


def note_request_success(url: str) -> None:
    """Healthy response — gradually speed tag fetches back up (shared)."""
    if not _is_tag_profile_url(url):
        return

    def mutator(snap: RateSnapshot) -> RateSnapshot:
        streak = snap.success_streak + 1
        tag = snap.tag_interval
        if streak >= SUCCESS_STREAK_TO_SPEED_UP:
            streak = 0
            floor = _floor(snap.crawl_delay)
            tag = max(floor, min(TAG_SOFT_INTERVAL, snap.tag_interval * 0.85))
        return RateSnapshot(
            next_allowed_at=snap.next_allowed_at,
            base_interval=snap.base_interval,
            tag_interval=tag,
            success_streak=streak,
            crawl_delay=snap.crawl_delay,
        )

    _STATE.store.update(mutator)


def path_is_robots_disallow(url: str) -> bool:
    """True for paths AO3 asks crawlers not to index (search listings, /downloads/).

    urllib.robotparser treats ``Disallow: /works?`` as a prefix of ``/works``,
    which would block every work page. AO3's comment on that rule is about
    search/index query URLs, so we match path + query explicitly.
    """
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host and "archiveofourown.org" not in host:
        return False
    path = parsed.path or "/"
    query = parsed.query or ""
    if path.startswith("/downloads/"):
        return True
    if path.startswith("/autocomplete/") or path.startswith("/external_works/"):
        return True
    if path == "/works" and query:
        return True
    if path.endswith("/search") and query:
        return True
    return False


def _is_tag_profile_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if not path.startswith("/tags/"):
        return False
    # /tags/search is heavy; individual /tags/Name pages are light.
    return path != "/tags/search" and not path.startswith("/tags/search/")


def interval_for_url(url: str) -> float:
    snap = _STATE.store.read()
    base = snap.base_interval
    tag = snap.tag_interval
    crawl = snap.crawl_delay
    if crawl:
        base = max(base, crawl)
        tag = max(tag, crawl)
    if path_is_robots_disallow(url):
        return max(base, HEAVY_MIN_INTERVAL, crawl or 0.0)
    if _is_tag_profile_url(url):
        return tag
    return base


def wait_for_request(url: str, *, on_status: StatusCallback | None = None) -> float:
    """Block until this host may hit AO3 again. Returns seconds waited.

    Slot reservation is shared across processes via SQLite so CLI / web / API /
    plugin enrich all observe the same pacing.
    """
    if _STATE.skip_wait:
        return 0.0
    interval = interval_for_url(url)
    jittered = interval * random.uniform(1.0 - JITTER, 1.0 + JITTER)
    wait, _snap = _STATE.store.claim_slot(jittered)
    if wait > 0.05:
        _emit(on_status, f"Rate limit: waiting {wait:.1f}s before AO3 request…")
        time.sleep(wait)
    return wait


def ensure_robots(*, fetcher: Callable[[], str] | None = None) -> None:
    """Load robots.txt once (or after TTL). Fail open with defaults."""
    now = time.monotonic()
    with _STATE.lock:
        if _STATE.robots is not None and (now - _STATE.robots_loaded_at) < ROBOTS_TTL_SECONDS:
            return
    try:
        if fetcher is not None:
            text = fetcher()
        else:
            import requests

            response = requests.get(
                ROBOTS_URL,
                timeout=30,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            text = response.text
        load_robots_text(text)
    except Exception:
        if _STATE.robots is None:
            load_robots_text("User-agent: *\nDisallow:\n")


# Re-exports for callers / docs.
__all__ = [
    "ABSOLUTE_MIN_INTERVAL",
    "DEFAULT_MIN_INTERVAL",
    "DEFAULT_RATE_DB_PATH",
    "HEAVY_MIN_INTERVAL",
    "MAX_MIN_INTERVAL",
    "ROBOTS_URL",
    "TAG_MAX_INTERVAL",
    "TAG_SOFT_INTERVAL",
    "USER_AGENT",
    "configure_min_interval",
    "current_tag_interval",
    "default_rate_db_path",
    "ensure_robots",
    "interval_for_url",
    "load_robots_text",
    "note_request_pressure",
    "note_request_success",
    "note_retry_after",
    "path_is_robots_disallow",
    "reset_rate_limit_state",
    "robots_loaded",
    "wait_for_request",
]
