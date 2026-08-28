"""Whole-library estimates from JSONL + the local tag cache (no AO3 fetches).

Used by ``python -m ao3kit library estimate`` and by the Calibre plugin's
Process library dialog (plugin-side snapshot math lives in
``calibre-plugin/library_job.py`` and stays in relative parity).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ao3kit.epubs import load_jsonl_records
from ao3kit.series import series_membership_is_complete
from ao3kit.scrape import WorkRecord
from ao3kit.tags.cache import TagCache, default_tag_cache_path
from ao3kit.tags.clean import collect_unique_tag_names
from ao3kit.tags.warm import uncached_names


DEFAULT_REQUEST_INTERVAL = 1.5


def _series_complete(row: dict[str, Any]) -> bool:
    work = WorkRecord.from_dict(row)
    if work is not None:
        return series_membership_is_complete(work)
    series = row.get("series") or []
    if not isinstance(series, list):
        return False
    return any(
        bool(item.get("series_id"))
        and bool(item.get("name"))
        and item.get("position") is not None
        for item in series
        if isinstance(item, dict)
    )


def estimate_records(
    records: list[dict[str, Any]],
    *,
    cache: TagCache | None = None,
    request_interval: float = DEFAULT_REQUEST_INTERVAL,
) -> dict[str, Any]:
    """Local counts for a JSONL dump. Does not hit AO3."""
    names = collect_unique_tag_names(
        records, include_fandoms=True, include_relationships=True, extra_keys=("characters",)
    )
    missing: list[str] = list(names)
    cache_available = False
    if cache is not None:
        cache_available = True
        missing = uncached_names(cache, names)
    with_ao3 = 0
    missing_epub = 0
    has_epub = 0
    series_complete = 0
    series_incomplete = 0
    series_known = 0
    cover_ready = 0
    for row in records:
        work_id = str(row.get("work_id") or "").strip()
        url = str(row.get("url") or "").strip()
        has_id = bool(work_id or url)
        if has_id:
            with_ao3 += 1
        else:
            pass
        if str(row.get("epub_file") or "").strip():
            has_epub += 1
        elif has_id:
            missing_epub += 1
        if _series_complete(row):
            series_complete += 1
        elif has_id:
            series_incomplete += 1
        series = row.get("series") or []
        if isinstance(series, list) and any(
            str((item or {}).get("series_id") or "").strip()
            for item in series
            if isinstance(item, dict)
        ):
            series_known += 1
        if str(row.get("title") or "").strip():
            cover_ready += 1
    pace = float(request_interval) if request_interval > 0 else DEFAULT_REQUEST_INTERVAL
    uncached = len(missing)
    return {
        "works": len(records),
        "with_ao3": with_ao3,
        "without_ao3": len(records) - with_ao3,
        "unique_tags": len(names),
        "cached_tags": max(0, len(names) - uncached),
        "uncached_tags": uncached,
        "uncached_names": missing[:50],
        "cache_available": cache_available,
        "missing_epub": missing_epub,
        "has_epub": has_epub,
        "series_complete": series_complete,
        "series_incomplete": series_incomplete,
        "series_known": series_known,
        "cover_ready": cover_ready,
        "request_interval": pace,
        "tag_fetch_seconds": uncached * pace,
        "series_fetch_seconds": series_incomplete * pace,
        "epub_fetch_seconds": missing_epub * pace,
    }


def _add_estimate_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "estimate",
        help="Count unmatched tags / missing EPUBs / incomplete series from JSONL (no AO3)",
    )
    parser.add_argument(
        "--jsonl",
        "-i",
        required=True,
        help="Work records JSONL (library dump or scrape results)",
    )
    parser.add_argument(
        "--cache",
        help="Tag-cache SQLite path (default: XDG cache)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Seconds per AO3 request for duration hints (default: config min_request_interval)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ao3kit library",
        description=(
            "Whole-library helpers. Estimate uses the local tag cache and JSONL "
            "only — it does not load AO3 URLs."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    _add_estimate_parser(sub)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "estimate":
        from ao3kit.config import load_rate_limit_settings

        records = load_jsonl_records(args.jsonl)
        interval = args.interval
        if interval is None:
            interval, _rate = load_rate_limit_settings()
        cache_path = Path(args.cache) if args.cache else default_tag_cache_path()
        cache = TagCache.load(cache_path) if cache_path.is_file() else None
        try:
            payload = estimate_records(
                records, cache=cache, request_interval=float(interval)
            )
        finally:
            if cache is not None:
                cache.close()
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        uncached = int(payload.get("uncached_tags") or 0)
        print(
            f"{payload['works']} works, {payload['unique_tags']} unique tags, "
            f"{uncached} unmatched in cache.",
            file=sys.stderr,
        )
        return 0
    parser.error(f"Unknown command: {args.command}")
    return 2
