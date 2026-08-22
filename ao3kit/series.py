"""Fetch AO3 series pages and expand work lists with series-mates."""

from __future__ import annotations

from typing import Any, Callable

import requests
from bs4 import BeautifulSoup

from ao3kit.http import AO3_BASE, create_session, is_login_wall
from ao3kit.rate import apply_request_delay
from ao3kit.scrape import (
    SeriesMembership,
    WorkRecord,
    build_series_url,
    fetch_page,
    parse_pagination_max,
    parse_search_page,
    parse_series_memberships,
)

StatusCallback = Callable[[str], None]
WorkCallback = Callable[[WorkRecord], None]
PageCallback = Callable[[Any, str], None]

SERIES_LOGIN_MARKERS = (
    "only visible to registered users",
    "only available to registered users",
)

# Keep extra JSONL fields when expanding dict records.
_PRESERVE_RECORD_KEYS = (
    "cleaned",
    "epub_file",
    "epub_error",
    "download_status",
)


def series_requires_login(html: str) -> bool:
    lower = html.lower()
    return any(marker in lower for marker in SERIES_LOGIN_MARKERS)


def parse_series_from_html(html: str) -> list[SeriesMembership]:
    soup = BeautifulSoup(html, "lxml")
    meta = soup.select_one("dl.work.meta") or soup.select_one("#main") or soup
    return parse_series_memberships(meta)


def scrape_series(
    series_id: str,
    *,
    session: requests.Session | None = None,
    start_page: int = 1,
    request_delay: float | None = None,
    on_page: PageCallback | None = None,
    on_work: WorkCallback | None = None,
) -> list[WorkRecord]:
    """Fetch every work listed on an AO3 series page (same blurbs as search)."""
    session = session or create_session()
    apply_request_delay(request_delay)
    series_id = str(series_id).strip()
    if not series_id.isdigit():
        raise ValueError(f"Invalid AO3 series id: {series_id!r}")

    matched: list[WorkRecord] = []
    seen: set[str] = set()
    page = max(1, int(start_page or 1))
    max_page: int | None = None

    while True:
        url = build_series_url(series_id, page)
        html = fetch_page(url, session=session)
        if series_requires_login(html):
            raise ValueError(
                "This series is only visible to registered AO3 users. "
                "Log in with username/password or AO3_USERNAME / AO3_PASSWORD."
            )
        search_page = parse_search_page(html)
        if max_page is None:
            max_page = parse_pagination_max(html)
        if on_page:
            on_page(search_page, url)
        page_new = 0
        for work in search_page.works:
            if work.work_id in seen:
                continue
            seen.add(work.work_id)
            matched.append(work)
            page_new += 1
            if on_work:
                on_work(work)
        if not search_page.works:
            break
        if (
            search_page.page_end is not None
            and search_page.total_results is not None
            and search_page.page_end >= search_page.total_results
        ):
            break
        if max_page is not None and page >= max_page:
            break
        if max_page is None and (page_new == 0 or len(search_page.works) < 20):
            break
        page += 1
        if max_page is not None and page > max_page:
            break
    return matched


def series_membership_is_complete(work: WorkRecord) -> bool:
    """True when we already have id, name, and part number for at least one series."""
    return any(
        bool(item.series_id) and bool(item.name) and item.position is not None
        for item in work.series
    )


def fill_series_from_work_pages(
    works: list[WorkRecord],
    session: requests.Session,
    *,
    force: bool = False,
    on_status: StatusCallback | None = None,
) -> set[str]:
    """Fetch work pages for records whose series membership is incomplete.

    Returns work ids that were looked up (including works that are not in a
    series). Login walls leave the existing ``series`` field unchanged.
    """
    pending = [
        work
        for work in works
        if work.work_id and (force or not series_membership_is_complete(work))
    ]
    looked_up: set[str] = set()
    if not pending:
        if on_status and works:
            on_status("All works already have series metadata.")
        return looked_up
    skip_n = len(works) - len(pending)
    if on_status and skip_n:
        on_status(
            f"{skip_n} already have series metadata; looking up {len(pending)}…"
        )
    total = len(pending)
    for index, work in enumerate(pending, start=1):
        url = work.url or f"{AO3_BASE}/works/{work.work_id}"
        if on_status:
            title = work.title or work.work_id
            on_status(
                f"[{index}/{total}] Looking up series for {work.work_id} {title}…"
            )
        html = fetch_page(url, session=session)
        if is_login_wall(html) or series_requires_login(html):
            if on_status:
                on_status(
                    f"Series lookup needs AO3 login for {work.work_id}."
                )
            continue
        work.series = parse_series_from_html(html)
        looked_up.add(work.work_id)
    return looked_up


def fill_record_dicts(
    records: list[dict[str, Any]],
    *,
    session: requests.Session | None = None,
    request_delay: float | None = None,
    force: bool = False,
    on_status: StatusCallback | None = None,
    score_config=None,
) -> list[dict[str, Any]]:
    """Fill ``series`` on existing JSONL records without adding series-mates."""
    session = session or create_session()
    apply_request_delay(request_delay)
    paired: list[tuple[dict[str, Any], WorkRecord]] = []
    works: list[WorkRecord] = []
    for record in records:
        work = WorkRecord.from_dict(record)
        if work is None:
            continue
        paired.append((record, work))
        works.append(work)
    looked_up = fill_series_from_work_pages(
        works,
        session,
        force=force,
        on_status=on_status,
    )
    in_series = sum(1 for work in works if work.series)
    if on_status:
        on_status(
            f"Series lookup finished ({in_series}/{len(works)} in a series)."
        )
    out: list[dict[str, Any]] = []
    for record, work in paired:
        merged = dict(record)
        data = work.to_dict(score_config=score_config)
        for key in _PRESERVE_RECORD_KEYS:
            if key in record:
                merged[key] = record[key]
        if work.work_id in looked_up:
            if work.series:
                merged["series"] = data.get("series") or [
                    item.to_dict() for item in work.series
                ]
            else:
                merged.pop("series", None)
        elif work.series and not merged.get("series"):
            merged["series"] = data.get("series") or [
                item.to_dict() for item in work.series
            ]
        out.append(merged)
    return out


def unique_series_ids(works: list[WorkRecord]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for work in works:
        for membership in work.series:
            series_id = membership.series_id
            if series_id and series_id not in seen:
                seen.add(series_id)
                ids.append(series_id)
    return ids


def expand_with_series(
    works: list[WorkRecord],
    *,
    session: requests.Session | None = None,
    request_delay: float | None = None,
    fetch_missing: bool = True,
    on_status: StatusCallback | None = None,
    on_work: WorkCallback | None = None,
    on_page: PageCallback | None = None,
) -> list[WorkRecord]:
    """Return ``works`` plus every other part of each series they belong to.

    Search filters are not applied to the extra parts. Original works keep
    their place at the front; new series-mates are appended in series order.
    """
    session = session or create_session()
    apply_request_delay(request_delay)
    works = [work for work in works if work.work_id]
    if fetch_missing:
        fill_series_from_work_pages(works, session, on_status=on_status)

    by_id: dict[str, WorkRecord] = {}
    order: list[str] = []
    for work in works:
        if work.work_id in by_id:
            if not by_id[work.work_id].series and work.series:
                by_id[work.work_id].series = work.series
            continue
        by_id[work.work_id] = work
        order.append(work.work_id)

    added: list[WorkRecord] = []
    for series_id in unique_series_ids([by_id[wid] for wid in order]):
        if on_status:
            on_status(f"Fetching AO3 series {series_id}…")
        try:
            series_works = scrape_series(
                series_id,
                session=session,
                request_delay=request_delay,
                on_page=on_page,
            )
        except ValueError as exc:
            if on_status:
                on_status(str(exc))
            continue
        if on_status:
            on_status(
                f"Series {series_id}: {len(series_works)} work"
                f"{'' if len(series_works) == 1 else 's'} on AO3."
            )
        for work in series_works:
            existing = by_id.get(work.work_id)
            if existing is not None:
                if not existing.series and work.series:
                    existing.series = work.series
                continue
            by_id[work.work_id] = work
            added.append(work)
            if on_work:
                on_work(work)

    if on_status and added:
        series_n = len(unique_series_ids(list(by_id.values())))
        noun = "work" if len(added) == 1 else "works"
        series_noun = "series" if series_n == 1 else "series"
        on_status(f"Added {len(added)} more {noun} from {series_n} {series_noun}.")
    elif on_status:
        on_status("No additional series works to add.")
    return [by_id[wid] for wid in order] + added


def expand_record_dicts(
    records: list[dict[str, Any]],
    *,
    session: requests.Session | None = None,
    request_delay: float | None = None,
    fetch_missing: bool = True,
    on_status: StatusCallback | None = None,
    on_work: WorkCallback | None = None,
    on_page: PageCallback | None = None,
    score_config=None,
) -> list[dict[str, Any]]:
    """Expand JSONL-shaped records with series-mates. Preserves ``cleaned`` / EPUB fields."""
    originals: dict[str, dict[str, Any]] = {}
    works: list[WorkRecord] = []
    for record in records:
        work = WorkRecord.from_dict(record)
        if work is None:
            continue
        originals[work.work_id] = record
        works.append(work)
    expanded = expand_with_series(
        works,
        session=session,
        request_delay=request_delay,
        fetch_missing=fetch_missing,
        on_status=on_status,
        on_work=on_work,
        on_page=on_page,
    )
    out: list[dict[str, Any]] = []
    for work in expanded:
        data = work.to_dict(score_config=score_config)
        src = originals.get(work.work_id) or {}
        for key in _PRESERVE_RECORD_KEYS:
            if key in src:
                data[key] = src[key]
        out.append(data)
    return out
