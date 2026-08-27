"""Suggest tag names from the local SQLite cache (no AO3 fetch).

AO3 disallows ``/autocomplete/`` in robots.txt, so plugin text fields and
``python -m ao3kit tags suggest`` rank names already stored in the tag cache
(plus optional extra names from the Calibre library).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

DEFAULT_LIMIT = 25
FETCH_CAP = 400


def escape_like(text: str) -> str:
    """Escape ``\\``, ``%``, and ``_`` for a SQLite ``LIKE … ESCAPE '\\'`` pattern."""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def category_values(category: str | None) -> tuple[str, ...] | None:
    """Lowercased category names to match, or ``None`` for any category."""
    if category is None:
        return None
    key = str(category).strip().casefold()
    if not key:
        return None
    if key in {"freeform", "additional tags"}:
        return ("freeform", "additional tags")
    return (key,)


def rank_tuple(name: str, query: str, *, canonical: bool = False) -> tuple:
    """Sort key: exact, prefix, word-ish, contains; then canonical; then length."""
    folded = name.casefold()
    q = query.casefold().strip()
    if folded == q:
        bucket = 0
    elif folded.startswith(q):
        bucket = 1
    elif f" {q}" in f" {folded}" or f"/{q}" in folded or f"({q}" in folded:
        bucket = 2
    elif q in folded:
        bucket = 3
    else:
        bucket = 4
    return (bucket, 0 if canonical else 1, len(name), folded)


def current_csv_token(text: str, cursor: int | None = None) -> tuple[int, int, str]:
    """Return ``(start, end, token)`` for the comma-separated value at ``cursor``."""
    raw = text or ""
    if cursor is None:
        cursor = len(raw)
    cursor = max(0, min(int(cursor), len(raw)))
    start = raw.rfind(",", 0, cursor) + 1
    end = raw.find(",", cursor)
    if end < 0:
        end = len(raw)
    token = raw[start:end]
    lead = len(token) - len(token.lstrip())
    trail = len(token) - len(token.rstrip())
    token_start = start + lead
    token_end = end - trail
    if token_end < token_start:
        token_end = token_start
    return token_start, token_end, raw[token_start:token_end]


def replace_csv_token(
    text: str, replacement: str, cursor: int | None = None
) -> tuple[str, int]:
    """Replace the current CSV token. Returns ``(new_text, new_cursor)``."""
    start, end, _token = current_csv_token(text, cursor)
    raw = text or ""
    inserted = str(replacement or "")
    new = raw[:start] + inserted + raw[end:]
    return new, start + len(inserted)


def merge_and_rank(
    cache_rows: Sequence[tuple[str, str]],
    extra: Iterable[str] | None,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    """Dedupe cache ``(name, status)`` rows with extras and rank them."""
    q = str(query or "").strip()
    if not q:
        return []
    cap = max(1, int(limit))
    items: list[tuple[tuple, str]] = []
    seen: set[str] = set()
    for name, status in cache_rows:
        text = str(name or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        items.append(
            (rank_tuple(text, q, canonical=str(status) == "canonical"), text)
        )
    needle = q.casefold()
    for name in extra or []:
        text = str(name or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        if needle not in key:
            continue
        seen.add(key)
        items.append((rank_tuple(text, q, canonical=False), text))
    items.sort(key=lambda row: row[0])
    return [name for _rank, name in items[:cap]]


def suggest_from_connection(
    conn: sqlite3.Connection,
    query: str,
    *,
    category: str | None = None,
    extra: Iterable[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    fetch: int = FETCH_CAP,
) -> list[str]:
    """Rank matching ``entries.name`` rows from an open tag-cache connection."""
    q = str(query or "").strip()
    if not q:
        return []
    pattern = f"%{escape_like(q)}%"
    wanted = category_values(category)
    fetch_n = max(int(limit), int(fetch))
    sql = """
        SELECT name, status
        FROM entries
        WHERE name LIKE ? ESCAPE '\\'
    """
    params: list[Any] = [pattern]
    if wanted:
        placeholders = ",".join("?" * len(wanted))
        sql += f" AND LOWER(IFNULL(category, '')) IN ({placeholders})"
        params.extend(wanted)
    sql += " LIMIT ?"
    params.append(fetch_n)
    try:
        rows = [
            (str(row[0]), str(row[1]))
            for row in conn.execute(sql, params).fetchall()
        ]
    except sqlite3.Error:
        rows = []
    return merge_and_rank(rows, extra, q, limit=limit)


def suggest_from_sqlite_path(
    path: Path | str | None,
    query: str,
    *,
    category: str | None = None,
    extra: Iterable[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    fetch: int = FETCH_CAP,
) -> list[str]:
    """Open the cache file read-only (no seed import) and suggest names."""
    if path is None:
        return merge_and_rank([], extra, query, limit=limit)
    cache_path = Path(path)
    if not cache_path.is_file():
        return merge_and_rank([], extra, query, limit=limit)
    try:
        conn = sqlite3.connect(
            f"file:{cache_path.resolve()}?mode=ro", uri=True, timeout=5
        )
    except sqlite3.Error:
        return merge_and_rank([], extra, query, limit=limit)
    try:
        return suggest_from_connection(
            conn,
            query,
            category=category,
            extra=extra,
            limit=limit,
            fetch=fetch,
        )
    finally:
        conn.close()


def suggest_tag_names(
    query: str,
    *,
    cache=None,
    cache_path: Path | str | None = None,
    category: str | None = None,
    extra: Iterable[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    """Suggest names from a ``TagCache``, a sqlite path, and/or extra strings."""
    if cache is not None:
        opener = getattr(cache, "_open", None)
        if callable(opener):
            return suggest_from_connection(
                opener(),
                query,
                category=category,
                extra=extra,
                limit=limit,
            )
    path = cache_path
    if path is None:
        from ao3kit.paths import tag_cache_file

        path = tag_cache_file()
    return suggest_from_sqlite_path(
        path, query, category=category, extra=extra, limit=limit
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Suggest tag names from the local tag cache (no AO3 fetch).",
    )
    parser.add_argument("query", help="Text to match (case-insensitive)")
    parser.add_argument(
        "--type",
        default="",
        choices=("Fandom", "Character", "Relationship", "Freeform"),
        help="Restrict to this AO3 tag category",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum names to print (default {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Tag cache SQLite path (default: XDG cache)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON array instead of one name per line",
    )
    args = parser.parse_args(argv)
    names = suggest_tag_names(
        args.query,
        cache_path=args.cache,
        category=args.type or None,
        limit=max(1, int(args.limit)),
    )
    if args.json:
        json.dump(names, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        for name in names:
            print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
