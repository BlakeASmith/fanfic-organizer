"""Host-wide AO3 rate limiting and robots.txt policy.

AO3's robots.txt (User-agent: *) has no Crawl-delay, but disallows
``/downloads/`` and ``/works?`` so search engines do not index those URLs.
This tool still fetches work pages, user-requested search listings, and
native EPUB files for personal library backup.

CLI and the Calibre plugin’s ao3kit subprocess share one
on-disk limiter (see ``ao3kit.rate_store``) so concurrent processes on the
same host pace together:

- adaptive spacing for light paths (tag profiles) — start fast, back off on pressure
- a short dedicated interval for login (GET form + POST)
- work, search, and EPUB downloads share the host-wide engine floor (default 1.5s, adaptive on pressure)
- 429 + Retry-After pause every interface for that cooldown (not a new cruise interval)
"""

from __future__ import annotations

import os
import random
import threading
import time
import urllib.robotparser
from dataclasses import replace
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from ao3kit.rate_store import (
    RateEvent,
    RateSnapshot,
    SharedRateStore,
    default_rate_db_path,
)

ROBOTS_URL = "https://archiveofourown.org/robots.txt"
# General work-page pacing when no adaptive tag lane applies.
DEFAULT_MIN_INTERVAL = 1.5
ABSOLUTE_MIN_INTERVAL = 1.0
# Tag profiles start here and adapt up/down based on AO3 responses.
TAG_SOFT_INTERVAL = 1.5
TAG_MAX_INTERVAL = 8.0
# Tag 429 without Retry-After: brief pause; the tag lane already doubles.
TAG_DEFAULT_RETRY_AFTER = 2.0
# Login is two light requests; never inherit a leftover 429 wait.
LOGIN_MIN_INTERVAL = 1.0
LOGIN_MAX_WAIT = 2.0
# Leftover next_allowed_at beyond this is treated as a stuck lock (crash /
# cancelled wait), not a live Retry-After. Must stay above typical Cloudflare
# headers (~2–3 min) so another process does not punch through the cooldown.
STALE_LOCK_SECONDS = 360.0
STALE_LOCK_WAIT = 15.0
MAX_MIN_INTERVAL = 60.0
JITTER = 0.08
ROBOTS_TTL_SECONDS = 24 * 3600.0
SUCCESS_STREAK_TO_SPEED_UP = 8
USER_AGENT = "ao3-scraper/0.1 (+personal library backup; respects crawl-delay)"

# AO3 User-agent: * (no Crawl-delay). Used when the on-disk cache is cold so a
# new CLI/plugin process does not block on fetching robots.txt.
DEFAULT_AO3_ROBOTS = """\
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
        self._robots_refreshing = False

    @property
    def crawl_delay(self) -> float | None:
        return self.store.read().crawl_delay

    @property
    def base_interval(self) -> float:
        return self.store.read().base_interval

    @base_interval.setter
    def base_interval(self, value: float) -> None:
        def mutator(snap: RateSnapshot) -> RateSnapshot:
            return replace(snap, base_interval=float(value))

        self.store.update(mutator)

    @property
    def tag_interval(self) -> float:
        return self.store.read().tag_interval

    @tag_interval.setter
    def tag_interval(self, value: float) -> None:
        def mutator(snap: RateSnapshot) -> RateSnapshot:
            return replace(snap, tag_interval=float(value))

        self.store.update(mutator)

    @property
    def success_streak(self) -> int:
        return self.store.read().success_streak

    @success_streak.setter
    def success_streak(self, value: int) -> None:
        def mutator(snap: RateSnapshot) -> RateSnapshot:
            return replace(snap, success_streak=int(value))

        self.store.update(mutator)

    @property
    def next_allowed_at(self) -> float:
        return self.store.read().next_allowed_at


_STATE = RateLimitState()

_RATE_SETTINGS: RateLimitSettings | None = None
_RATE_MIN_INTERVAL: float | None = None


def reset_rate_settings_cache() -> None:
    """Clear cached config knobs (tests / after config edits)."""
    global _RATE_SETTINGS, _RATE_MIN_INTERVAL
    _RATE_SETTINGS = None
    _RATE_MIN_INTERVAL = None


def refresh_rate_settings_from_config() -> float:
    """Load pacing knobs from XDG config; return ``min_request_interval``."""
    global _RATE_SETTINGS, _RATE_MIN_INTERVAL
    from ao3kit.config import load_rate_limit_settings

    _RATE_MIN_INTERVAL, _RATE_SETTINGS = load_rate_limit_settings()
    return float(_RATE_MIN_INTERVAL)


def get_default_retry_after() -> float:
    """Default 429 pause when AO3 omits Retry-After on tag fetches."""
    return _rcfg().default_retry_after


def _rcfg() -> RateLimitSettings:
    if _RATE_SETTINGS is None:
        refresh_rate_settings_from_config()
    return _RATE_SETTINGS


def _configured_min_interval() -> float:
    if _RATE_MIN_INTERVAL is None:
        refresh_rate_settings_from_config()
    return max(float(_RATE_MIN_INTERVAL), ABSOLUTE_MIN_INTERVAL)


# Late import avoids a cycle; config does not import rate.
from ao3kit.config import RateLimitSettings  # noqa: E402


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
    reset_rate_settings_cache()


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
        return replace(
            snap,
            base_interval=base,
            tag_interval=tag,
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


def _clamp_snapshot(snap: RateSnapshot) -> RateSnapshot:
    """Enforce host-wide floors and keep the tag lane from outrunning scrape/search."""
    cfg = _rcfg()
    floor = _floor(snap.crawl_delay)
    min_iv = _configured_min_interval()
    soft = max(floor, cfg.tag_soft_interval, min_iv)
    base = max(floor, min_iv, float(snap.base_interval))
    tag = max(floor, soft, float(snap.tag_interval))
    tag = max(tag, min(base, soft))
    return replace(
        snap,
        base_interval=base,
        tag_interval=tag,
    )


def ensure_rate_limits() -> float:
    """Refresh shared work/search/download floors from config + the limiter engine.

    Raises the host-wide floor to the configured minimum but does **not**
    reset an elevated adaptive interval. A new job must not wipe backoff
    another job just applied.
    """
    refresh_rate_settings_from_config()
    return configure_min_interval()


def configure_min_interval(requested: float | None = None) -> float:
    """Set the shared host-wide general interval (tag lane keeps its own pace).

    ``requested`` is retained for tests only; production code should call
    :func:`ensure_rate_limits` and let the adaptive engine manage pacing.
    """

    def mutator(snap: RateSnapshot) -> RateSnapshot:
        floor = _floor(snap.crawl_delay)
        if requested and requested > 0:
            base = max(floor, float(requested))
            tag = snap.tag_interval
            if float(requested) < tag:
                tag = max(floor, float(requested))
        else:
            base = max(snap.base_interval, floor, _configured_min_interval())
            tag = snap.tag_interval
        return _clamp_snapshot(
            replace(
                snap,
                base_interval=base,
                tag_interval=tag,
            )
        )

    return _STATE.store.update(mutator).base_interval


def current_tag_interval() -> float:
    return _STATE.store.read().tag_interval


def note_retry_after(seconds: float, *, url: str | None = None) -> None:
    """AO3 sent 429 — pause the whole host for ``seconds``.

    Retry-After is a one-shot IP cooldown (often Cloudflare ~2–3 min). It must
    block scrape, tags, and download together. It is not a new cruise interval:
    ``base_interval`` stays put so a 178s header does not become a 60s floor.
    ``url`` is the 429'd path (for callers/logs); the pause is per-IP, not
    per-route. The tag lane still doubles its adaptive interval.
    """
    pause = max(float(seconds), 1.0)
    now = time.time()
    _ = url  # per-IP cooldown; path does not change the host pause

    cfg = _rcfg()

    def mutator(snap: RateSnapshot) -> RateSnapshot:
        return replace(
            snap,
            next_allowed_at=max(snap.next_allowed_at, now + pause),
            tag_interval=min(
                max(
                    snap.tag_interval * cfg.retry_after_tag_multiplier,
                    cfg.retry_after_tag_floor,
                ),
                cfg.tag_max_interval,
            ),
            success_streak=0,
            retry_after_until=now + pause,
        )

    _STATE.store.update(mutator)


def note_request_pressure(*, status_code: int | None = None) -> None:
    """Transient edge pressure (5xx / Cloudflare) — slow the shared tag lane."""
    del status_code  # reserved for future tuning
    cfg = _rcfg()

    def mutator(snap: RateSnapshot) -> RateSnapshot:
        return replace(
            snap,
            base_interval=min(
                max(snap.base_interval * cfg.pressure_base_multiplier, cfg.pressure_floor),
                cfg.max_interval,
            ),
            tag_interval=min(
                max(snap.tag_interval * cfg.pressure_tag_multiplier, cfg.pressure_floor),
                cfg.tag_max_interval,
            ),
            success_streak=0,
        )

    _STATE.store.update(mutator)


def note_request_success(url: str) -> None:
    """Healthy response — gradually speed tag fetches back up (shared)."""
    if not _is_tag_profile_url(url):
        return

    cfg = _rcfg()

    def mutator(snap: RateSnapshot) -> RateSnapshot:
        streak = snap.success_streak + 1
        tag = snap.tag_interval
        if streak >= cfg.success_streak:
            streak = 0
            floor = _floor(snap.crawl_delay)
            tag = max(
                floor,
                min(cfg.tag_soft_interval, snap.tag_interval * cfg.success_speed_factor),
            )
        return replace(
            snap,
            tag_interval=tag,
            success_streak=streak,
            retry_after_until=None,
        )

    _STATE.store.update(mutator)


def _is_work_listing_url(url: str) -> bool:
    path = (urlparse(url).path or "/").rstrip("/") or "/"
    if path.startswith("/series/"):
        return True
    if path.startswith("/collections/") and path.endswith("/works"):
        return True
    if path.startswith("/users/") and (
        path.endswith("/works")
        or path.endswith("/works/collected")
        or path.endswith("/bookmarks")
    ):
        return True
    if path.startswith("/tags/") and path.endswith("/works"):
        return True
    if path in {"/works", "/works/search"}:
        return True
    return False


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
    path = (parsed.path or "/").rstrip("/") or "/"
    if not path.startswith("/tags/"):
        return False
    rest = path[len("/tags/") :]
    if not rest or rest == "search" or rest.startswith("search/"):
        return False
    # /tags/Name/works and /tags/Name/bookmarks are listings, not profiles.
    if "/" in rest:
        return False
    return True


def _is_login_url(url: str) -> bool:
    path = urlparse(url).path or "/"
    return path.startswith("/users/login") or path.startswith("/users/sign_in")


def url_kind(url: str) -> str:
    """Coarse path class for the request log (query strings are not stored)."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    if _is_login_url(url):
        return "login"
    if path.startswith("/downloads/"):
        return "download"
    if _is_tag_profile_url(url):
        return "tag"
    if _is_work_listing_url(url):
        return "search"
    if path_is_robots_disallow(url):
        return "search"
    if path.startswith("/works"):
        return "work"
    if path.startswith("/tags"):
        return "tag"
    return "other"


def record_request_event(
    *,
    url: str,
    method: str,
    outcome: str,
    wait_s: float = 0.0,
    interval_s: float | None = None,
    elapsed_s: float | None = None,
    status: int | None = None,
    retry_after_s: float | None = None,
    retry_after_from_header: bool | None = None,
    attempt: int = 1,
) -> None:
    """Append one AO3 attempt to the shared request log. Never raises."""
    parsed = urlparse(url)
    try:
        snap = _STATE.store.read()
    except Exception:
        snap = None
    event = RateEvent(
        ts=time.time(),
        kind=url_kind(url),
        method=(method or "GET").upper(),
        path=parsed.path or "/",
        status=status,
        outcome=outcome,
        wait_s=float(wait_s),
        interval_s=interval_s,
        elapsed_s=elapsed_s,
        retry_after_s=retry_after_s,
        retry_after_from_header=retry_after_from_header,
        attempt=int(attempt),
        base_interval=None if snap is None else snap.base_interval,
        tag_interval=None if snap is None else snap.tag_interval,
        success_streak=None if snap is None else snap.success_streak,
        pid=os.getpid(),
    )
    try:
        _STATE.store.record_event(event)
    except Exception:
        return


def rate_report(
    *,
    since_hours: float | None = None,
    recent: int = 20,
    hourly_hours: float = 48.0,
) -> dict:
    """Current limiter snapshot, request stats, and tuning analysis."""
    snap = _STATE.store.read()
    now = time.time()
    since = None
    if since_hours is not None:
        since = now - max(float(since_hours), 0.0) * 3600.0
    stats = _STATE.store.event_stats(since=since)
    stats_24h = _STATE.store.event_stats(since=now - 24 * 3600)
    stats_7d = _STATE.store.event_stats(since=now - 7 * 24 * 3600)
    hourly_since = now - max(float(hourly_hours), 0.0) * 3600.0
    hourly = _STATE.store.hourly_series(since=hourly_since)
    db_path = str(_STATE.store.path) if _STATE.store.path else ":memory:"
    return {
        "db": db_path,
        "snapshot": {
            "base_interval": snap.base_interval,
            "tag_interval": snap.tag_interval,
            "success_streak": snap.success_streak,
            "crawl_delay": snap.crawl_delay,
            "next_allowed_in_s": round(max(0.0, snap.next_allowed_at - now), 3),
        },
        "retention": {
            "raw_events_days": 30,
            "hourly_days": 180,
            "raw_event_cap": 50_000,
        },
        "window_hours": since_hours,
        "stats": stats,
        "windows": {"24h": stats_24h, "7d": stats_7d},
        "interval_vs_429": _STATE.store.interval_outcome_table(
            since=since if since is not None else now - 7 * 24 * 3600
        ),
        "hourly": hourly,
        "hourly_totals": _hourly_totals(hourly),
        "suggestions": rate_suggestions(snap, stats_24h),
        "recent": [
            event.to_dict()
            for event in _STATE.store.recent_events(limit=recent, since=since)
        ],
    }


def _hourly_totals(rows: list[dict]) -> list[dict]:
    by_hour: dict[int, dict] = {}
    for row in rows:
        hour = int(row["hour_ts"])
        bucket = by_hour.get(hour)
        if bucket is None:
            bucket = {
                "hour_ts": hour,
                "at": row["at"],
                "requests": 0,
                "ok": 0,
                "429": 0,
                "5xx": 0,
                "cloudflare": 0,
            }
            by_hour[hour] = bucket
        for key in ("requests", "ok", "429", "5xx", "cloudflare"):
            bucket[key] += int(row.get(key) or 0)
    totals = sorted(by_hour.values(), key=lambda item: item["hour_ts"], reverse=True)
    for bucket in totals:
        n = bucket["requests"]
        bucket["429_rate"] = round((bucket["429"] / n) if n else 0.0, 4)
    return totals


def _lane_interval(kind: str, snap: RateSnapshot) -> float:
    if kind == "tag":
        return snap.tag_interval
    if kind == "login":
        return max(LOGIN_MIN_INTERVAL, snap.crawl_delay or 0.0)
    return snap.base_interval


def rate_suggestions(snap: RateSnapshot, stats_24h: dict) -> list[dict]:
    """Human-readable hints for retuning lanes. Does not change intervals."""
    hints: list[dict] = []
    by_kind = stats_24h.get("by_kind") or {}
    min_sample = 20
    for kind in ("tag", "work", "search", "download"):
        row = by_kind.get(kind)
        current = _lane_interval(kind, snap)
        if not row:
            continue
        n = int(row.get("requests") or 0)
        n429 = int(row.get("429") or 0)
        pressure = n429 + int(row.get("cloudflare") or 0) + int(row.get("5xx") or 0)
        if n < min_sample:
            if n429:
                hints.append(
                    {
                        "kind": kind,
                        "severity": "watch",
                        "text": (
                            f"{kind}: {n429} 429(s) in {n} request(s) at ~{current:.1f}s "
                            "— too few samples to retune yet."
                        ),
                    }
                )
            continue
        rate_429 = n429 / n
        if rate_429 >= 0.05:
            hints.append(
                {
                    "kind": kind,
                    "severity": "raise",
                    "text": (
                        f"{kind}: {rate_429:.1%} 429 rate over {n} requests "
                        f"(lane ~{current:.1f}s). Raise this interval before going faster."
                    ),
                }
            )
        elif pressure / n >= 0.05:
            hints.append(
                {
                    "kind": kind,
                    "severity": "watch",
                    "text": (
                        f"{kind}: {pressure} pressure responses "
                        f"(429/5xx/Cloudflare) in {n} at ~{current:.1f}s."
                    ),
                }
            )
        elif n429 == 0 and int(row.get("cloudflare") or 0) == 0:
            hints.append(
                {
                    "kind": kind,
                    "severity": "ok",
                    "text": (
                        f"{kind}: 0 429s in {n} requests at ~{current:.1f}s. "
                        "Current pace looks comfortable."
                    ),
                }
            )
    retry = stats_24h.get("retry_after") or {}
    values = [
        item
        for item in (retry.get("values") or [])
        if item.get("from_header") and item.get("seconds")
    ]
    if values:
        top = values[0]
        hints.append(
            {
                "kind": "retry_after",
                "severity": "info",
                "text": (
                    f"AO3 sent Retry-After={float(top['seconds']):.0f}s on "
                    f"{top['count']} 429(s) (60s is used when the header is missing)."
                ),
            }
        )
    if not hints:
        hints.append(
            {
                "kind": "all",
                "severity": "info",
                "text": (
                    "Not enough AO3 traffic yet. After scrape/download/tag enrich, "
                    "hourly rollups and interval-vs-429 tables fill in."
                ),
            }
        )
    return hints


def export_rate_log(
    *,
    hourly: bool = False,
    since_hours: float | None = None,
    since_days: float | None = None,
) -> list[dict]:
    """JSON-serializable rows for long-term analysis (events or hourly rollups)."""
    now = time.time()
    since = None
    if since_hours is not None:
        since = now - max(float(since_hours), 0.0) * 3600.0
    elif since_days is not None:
        since = now - max(float(since_days), 0.0) * 86400.0
    if hourly:
        return _STATE.store.hourly_series(since=since)
    return _STATE.store.export_events(since=since)


def clear_rate_events() -> int:
    return _STATE.store.clear_events()


def clear_rate_hourly() -> int:
    return _STATE.store.clear_hourly()


def interval_for_url(url: str) -> float:
    """Seconds to reserve before this URL. Every AO3 path has a floor.

    Login uses a short dedicated interval. Tag profiles use the adaptive tag
    lane. Search, work, download, series, robots.txt, and any other AO3 path
    share the host-wide config floor (never zero).
    """
    snap = _clamp_snapshot(_STATE.store.read())
    base = snap.base_interval
    tag = snap.tag_interval
    crawl = snap.crawl_delay
    if crawl:
        base = max(base, crawl)
        tag = max(tag, crawl)
    floor = _floor(crawl)
    min_iv = _configured_min_interval()
    if _is_login_url(url):
        return max(LOGIN_MIN_INTERVAL, crawl or 0.0)
    if _is_tag_profile_url(url):
        return max(tag, floor, min_iv)
    return max(base, floor, min_iv)


def wait_for_request(url: str, *, on_status: StatusCallback | None = None) -> float:
    """Block until this host may hit AO3 again. Returns seconds waited.

    Slot reservation is shared across processes via SQLite so CLI and
    plugin jobs observe the same pacing.
    """
    if _STATE.skip_wait:
        return 0.0
    interval = interval_for_url(url)
    cfg = _rcfg()
    jittered = interval * random.uniform(1.0 - cfg.jitter, 1.0 + cfg.jitter)
    is_login = _is_login_url(url)
    wait, _snap = _STATE.store.claim_slot(
        jittered,
        max_wait=LOGIN_MAX_WAIT if is_login else None,
        stale_after=None if is_login else STALE_LOCK_SECONDS,
        stale_wait=STALE_LOCK_WAIT,
    )
    if wait > 0.05:
        _emit(on_status, f"Rate limit: waiting {wait:.1f}s before AO3 request…")
        time.sleep(wait)
    return wait


def _disk_robots_fresh(fetched_at: float) -> bool:
    return (time.time() - fetched_at) < ROBOTS_TTL_SECONDS


def _fetch_robots_text(fetcher: Callable[[], str] | None) -> str:
    if fetcher is not None:
        return fetcher()
    wait_for_request(ROBOTS_URL)
    import requests

    response = requests.get(
        ROBOTS_URL,
        timeout=15,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.text


def _refresh_robots_in_background() -> None:
    if _STATE.skip_wait or _STATE._robots_refreshing:
        return
    _STATE._robots_refreshing = True

    def worker() -> None:
        try:
            text = _fetch_robots_text(None)
            load_robots_text(text)
            _STATE.store.write_robots(text, time.time())
        except Exception:
            pass
        finally:
            _STATE._robots_refreshing = False

    threading.Thread(target=worker, daemon=True, name="ao3-robots-refresh").start()


def ensure_robots(
    *,
    fetcher: Callable[[], str] | None = None,
    on_status: StatusCallback | None = None,
) -> None:
    """Load robots.txt without blocking a scrape on the network.

    Process memory and the host-wide rate DB cache are reused across CLI /
    plugin subprocesses. A new process uses the disk cache or baked-in AO3
    defaults immediately; a live fetch only runs in the background (or
    synchronously when ``fetcher`` is passed, for tests).
    """
    now = time.monotonic()
    with _STATE.lock:
        if _STATE.robots is not None and (now - _STATE.robots_loaded_at) < ROBOTS_TTL_SECONDS:
            return

    if fetcher is not None:
        try:
            text = _fetch_robots_text(fetcher)
            load_robots_text(text)
            _STATE.store.write_robots(text, time.time())
        except Exception:
            _emit(on_status, "Could not load robots.txt — using defaults.")
            if _STATE.robots is None:
                load_robots_text(DEFAULT_AO3_ROBOTS)
        return

    cached = _STATE.store.read_robots()
    if cached is not None:
        body, fetched_at = cached
        load_robots_text(body)
        if _disk_robots_fresh(fetched_at):
            return
        _refresh_robots_in_background()
        return

    load_robots_text(DEFAULT_AO3_ROBOTS)
    _refresh_robots_in_background()


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m ao3kit rate`` — snapshot, analysis, JSONL export."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="ao3kit rate",
        description=(
            "Host-wide AO3 rate-limit snapshot, request log, and hourly rollups "
            "for tuning pacing over time."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["export"],
        help="export — write JSONL (raw events or --hourly rollups)",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help="Only rows in the last N hours (default: all retained)",
    )
    parser.add_argument(
        "--days",
        type=float,
        default=None,
        help="With export --hourly: only rollups in the last N days",
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=20,
        help="How many recent raw events to include in the JSON report",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="With export: write JSONL to this path (default: stdout)",
    )
    parser.add_argument(
        "--hourly",
        action="store_true",
        help="With export: write hourly rollups instead of raw events",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete raw request events (keeps hourly rollups)",
    )
    parser.add_argument(
        "--clear-hourly",
        action="store_true",
        help="Delete hourly rollups",
    )
    parser.add_argument(
        "--clear-all",
        action="store_true",
        help="Delete raw events and hourly rollups (does not reset intervals)",
    )
    args = parser.parse_args(argv)

    if args.clear_all or args.clear or args.clear_hourly:
        payload: dict[str, int] = {}
        if args.clear_all or args.clear:
            payload["cleared_events"] = clear_rate_events()
        if args.clear_all or args.clear_hourly:
            payload["cleared_hourly"] = clear_rate_hourly()
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.command == "export":
        rows = export_rate_log(
            hourly=args.hourly,
            since_hours=args.hours,
            since_days=None if args.hours is not None else args.days,
        )
        handle = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
        try:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False))
                handle.write("\n")
        finally:
            if args.output:
                handle.close()
        return 0

    json.dump(
        rate_report(since_hours=args.hours, recent=args.recent),
        sys.stdout,
        indent=2,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


# Re-exports for callers / docs.
__all__ = [
    "ABSOLUTE_MIN_INTERVAL",
    "DEFAULT_MIN_INTERVAL",
    "default_rate_db_path",
    "LOGIN_MAX_WAIT",
    "LOGIN_MIN_INTERVAL",
    "MAX_MIN_INTERVAL",
    "ROBOTS_URL",
    "TAG_DEFAULT_RETRY_AFTER",
    "TAG_MAX_INTERVAL",
    "TAG_SOFT_INTERVAL",
    "USER_AGENT",
    "clear_rate_events",
    "clear_rate_hourly",
    "ensure_rate_limits",
    "configure_min_interval",
    "current_tag_interval",
    "default_rate_db_path",
    "get_default_retry_after",
    "ensure_robots",
    "export_rate_log",
    "interval_for_url",
    "load_robots_text",
    "refresh_rate_settings_from_config",
    "reset_rate_settings_cache",
    "note_request_pressure",
    "note_request_success",
    "note_retry_after",
    "path_is_robots_disallow",
    "rate_report",
    "rate_suggestions",
    "record_request_event",
    "reset_rate_limit_state",
    "robots_loaded",
    "url_kind",
    "wait_for_request",
]
