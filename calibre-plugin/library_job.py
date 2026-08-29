# -*- coding: utf-8 -*-
"""Whole-library job estimates and option helpers (Calibre-free).

Counts use Calibre field snapshots plus the local AO3 tag-cache SQLite file.
Nothing here fetches URLs.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_REQUEST_INTERVAL = 1.5
_LOOKUP_CHUNK = 400
_SKIP_TAG_KEYS = frozenset({'completed', 'complete', 'fanfiction'})
_WORK_ID_RE = re.compile(
    r'(?:https?://)?(?:www\.)?archiveofourown\.org/works/(\d+)',
    re.IGNORECASE,
)
_SERIES_ID_RE = re.compile(
    r'(?:https?://)?(?:www\.)?archiveofourown\.org/series/(\d+)',
    re.IGNORECASE,
)

PREF_KEYS = (
    'library_simplify_tags',
    'library_drop_unmarked',
    'library_fill_series',
    'library_import_series',
    'library_download_epubs',
    'library_generate_covers',
    'library_recompute_collections',
    'library_cover_on_download',
    'library_update_existing',
)


@dataclass
class LibraryJobOptions:
    """What the Process library dialog should run."""

    simplify_tags: bool = True
    drop_unmarked: bool = True
    fill_series: bool = False
    import_series: bool = False
    download_epubs: bool = False
    generate_covers: bool = False
    recompute_collections: bool = False
    cover_on_download: bool = True
    update_existing: bool = True

    def any_selected(self) -> bool:
        return bool(
            self.simplify_tags
            or self.fill_series
            or self.import_series
            or self.download_epubs
            or self.generate_covers
            or self.recompute_collections
        )

    def needs_ao3_id(self) -> bool:
        return bool(self.fill_series or self.import_series or self.download_epubs)

    def to_dict(self) -> dict[str, Any]:
        return {
            'simplify_tags': bool(self.simplify_tags),
            'drop_unmarked': bool(self.drop_unmarked),
            'fill_series': bool(self.fill_series),
            'import_series': bool(self.import_series),
            'download_epubs': bool(self.download_epubs),
            'generate_covers': bool(self.generate_covers),
            'recompute_collections': bool(self.recompute_collections),
            'cover_on_download': bool(self.cover_on_download),
            'update_existing': bool(self.update_existing),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> LibraryJobOptions:
        data = data or {}
        return cls(
            simplify_tags=bool(data.get('simplify_tags', True)),
            drop_unmarked=bool(data.get('drop_unmarked', True)),
            fill_series=bool(data.get('fill_series', False)),
            import_series=bool(data.get('import_series', False)),
            download_epubs=bool(data.get('download_epubs', False)),
            generate_covers=bool(data.get('generate_covers', False)),
            recompute_collections=bool(data.get('recompute_collections', False)),
            cover_on_download=bool(data.get('cover_on_download', True)),
            update_existing=bool(data.get('update_existing', True)),
        )


def options_from_prefs(prefs: dict[str, Any] | None) -> LibraryJobOptions:
    """Restore the last Process library choices, else conservative defaults."""
    prefs = prefs or {}
    has_saved = any(key in prefs for key in PREF_KEYS)
    if not has_saved:
        return LibraryJobOptions()
    return LibraryJobOptions(
        simplify_tags=bool(prefs.get('library_simplify_tags', True)),
        drop_unmarked=bool(prefs.get('library_drop_unmarked', True)),
        fill_series=bool(prefs.get('library_fill_series', False)),
        import_series=bool(prefs.get('library_import_series', False)),
        download_epubs=bool(prefs.get('library_download_epubs', False)),
        generate_covers=bool(prefs.get('library_generate_covers', False)),
        recompute_collections=bool(
            prefs.get('library_recompute_collections', False)
        ),
        cover_on_download=bool(prefs.get('library_cover_on_download', True)),
        update_existing=bool(prefs.get('library_update_existing', True)),
    )


def prefs_from_options(options: LibraryJobOptions) -> dict[str, Any]:
    return {
        'library_simplify_tags': bool(options.simplify_tags),
        'library_drop_unmarked': bool(options.drop_unmarked),
        'library_fill_series': bool(options.fill_series),
        'library_import_series': bool(options.import_series),
        'library_download_epubs': bool(options.download_epubs),
        'library_generate_covers': bool(options.generate_covers),
        'library_recompute_collections': bool(options.recompute_collections),
        'library_cover_on_download': bool(options.cover_on_download),
        'library_update_existing': bool(options.update_existing),
    }


@dataclass
class LibraryBook:
    """Local snapshot of one Calibre book. No network."""

    book_id: int
    title: str = ''
    authors: tuple[str, ...] = ()
    identifiers: dict[str, str] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    fandoms: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()
    characters: tuple[str, ...] = ()
    original_tags: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    series_name: str = ''
    series_index: float | None = None
    has_epub: bool = False
    uuid: str = ''
    wordcount: int | None = None
    is_complete: bool | None = None

    @property
    def work_id(self) -> str:
        ids = self.identifiers or {}
        text = str(ids.get('ao3') or '').strip()
        if text:
            return text
        match = _WORK_ID_RE.search(str(ids.get('url') or ''))
        return match.group(1) if match else ''

    @property
    def url(self) -> str:
        ids = self.identifiers or {}
        text = str(ids.get('url') or '').strip()
        if text:
            return text
        work_id = self.work_id
        if work_id:
            return f'https://archiveofourown.org/works/{work_id}'
        return ''

    @property
    def series_id(self) -> str:
        ids = self.identifiers or {}
        text = str(ids.get('ao3series') or ids.get('series') or '').strip()
        if text.isdigit():
            return text
        match = _SERIES_ID_RE.search(text)
        return match.group(1) if match else ''

    def series_is_complete(self) -> bool:
        """True when id, name, and part number are already stored locally."""
        return bool(self.series_id) and bool(self.series_name) and (
            self.series_index is not None
        )


@dataclass
class LibraryEstimate:
    book_count: int = 0
    with_ao3: int = 0
    without_ao3: int = 0
    unique_tags: int = 0
    cached_tags: int = 0
    uncached_tags: int = 0
    cache_available: bool = False
    missing_epub: int = 0
    has_epub: int = 0
    series_complete: int = 0
    series_incomplete: int = 0
    series_known: int = 0
    cover_ready: int = 0
    request_interval: float = DEFAULT_REQUEST_INTERVAL
    tag_fetch_seconds: float = 0.0
    series_fetch_seconds: float = 0.0
    epub_fetch_seconds: float = 0.0


def field_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    if not text:
        return ()
    if ',' in text:
        return tuple(part.strip() for part in text.split(',') if part.strip())
    if '|' in text:
        return tuple(part.strip() for part in text.split('|') if part.strip())
    return (text,)


def unique_tag_names(books: Iterable[LibraryBook]) -> list[str]:
    """Stable unique tag/fandom/ship/character names for cache lookup."""
    seen: set[str] = set()
    ordered: list[str] = []
    for book in books:
        for name in (
            *book.tags,
            *book.fandoms,
            *book.relationships,
            *book.characters,
            *book.original_tags,
        ):
            text = str(name).strip()
            if not text or text.casefold() in _SKIP_TAG_KEYS:
                continue
            if text in seen:
                continue
            seen.add(text)
            ordered.append(text)
    return ordered


def names_present_in_cache(path: Path | str | None, names: Iterable[str]) -> set[str]:
    """Names that already have a row in the tag-cache SQLite file."""
    wanted = [str(name).strip() for name in names if str(name).strip()]
    if not wanted or path is None:
        return set()
    cache_path = Path(path)
    if not cache_path.is_file():
        return set()
    found: set[str] = set()
    try:
        conn = sqlite3.connect(
            f'file:{cache_path.resolve()}?mode=ro', uri=True, timeout=5
        )
    except sqlite3.Error:
        return set()
    try:
        for start in range(0, len(wanted), _LOOKUP_CHUNK):
            chunk = wanted[start : start + _LOOKUP_CHUNK]
            placeholders = ','.join('?' * len(chunk))
            try:
                rows = conn.execute(
                    f'SELECT name FROM entries WHERE name IN ({placeholders})',
                    chunk,
                )
            except sqlite3.Error:
                return found
            for row in rows:
                name = row[0] if not isinstance(row, sqlite3.Row) else row['name']
                if name:
                    found.add(str(name))
    finally:
        conn.close()
    return found


def uncached_tag_names(path: Path | str | None, names: Iterable[str]) -> list[str]:
    ordered = [str(name).strip() for name in names if str(name).strip()]
    cached = names_present_in_cache(path, ordered)
    return [name for name in ordered if name not in cached]


def parse_min_request_interval(text: str) -> float:
    """Read top-level ``min_request_interval`` from a config.yaml body."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if not stripped.startswith('min_request_interval:'):
            continue
        raw = stripped.split(':', 1)[1].strip().split('#', 1)[0].strip()
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_REQUEST_INTERVAL
        if value > 0:
            return value
    return DEFAULT_REQUEST_INTERVAL


def load_request_interval(config_path: Path | str | None) -> float:
    if config_path is None:
        return DEFAULT_REQUEST_INTERVAL
    path = Path(config_path)
    if not path.is_file():
        return DEFAULT_REQUEST_INTERVAL
    try:
        return parse_min_request_interval(path.read_text(encoding='utf-8'))
    except OSError:
        return DEFAULT_REQUEST_INTERVAL


def format_duration(seconds: float) -> str:
    value = max(0.0, float(seconds))
    if value < 45:
        return f'about {max(1, int(round(value)))}s' if value else 'none'
    minutes = value / 60.0
    if minutes < 90:
        return f'about {max(1, int(round(minutes)))} min'
    hours = minutes / 60.0
    if hours < 10:
        return f'about {hours:.1f} h'
    return f'about {int(round(hours))} h'


def _ao3_fetch_seconds(count: int, interval: float) -> float:
    n = max(0, int(count))
    if n <= 0:
        return 0.0
    pace = float(interval) if interval and interval > 0 else DEFAULT_REQUEST_INTERVAL
    return n * pace


def estimate_library_job(
    books: Iterable[LibraryBook],
    options: LibraryJobOptions | None = None,
    *,
    cache_path: Path | str | None = None,
    request_interval: float = DEFAULT_REQUEST_INTERVAL,
) -> LibraryEstimate:
    """Count local work for the chosen operations. Does not hit AO3."""
    options = options or LibraryJobOptions()
    items = list(books)
    estimate = LibraryEstimate(
        book_count=len(items),
        request_interval=(
            float(request_interval)
            if request_interval and request_interval > 0
            else DEFAULT_REQUEST_INTERVAL
        ),
    )
    names: list[str] = []
    if options.simplify_tags:
        names = unique_tag_names(items)
    estimate.unique_tags = len(names)
    if cache_path is not None and Path(cache_path).is_file():
        estimate.cache_available = True
    if names:
        cached = names_present_in_cache(cache_path, names) if names else set()
        estimate.cached_tags = len(cached)
        estimate.uncached_tags = len(names) - estimate.cached_tags
        estimate.tag_fetch_seconds = _ao3_fetch_seconds(
            estimate.uncached_tags, estimate.request_interval
        )

    for book in items:
        has_ao3 = bool(book.work_id or book.url)
        if has_ao3:
            estimate.with_ao3 += 1
        else:
            estimate.without_ao3 += 1
        if book.has_epub:
            estimate.has_epub += 1
        elif has_ao3:
            estimate.missing_epub += 1
        if book.series_is_complete():
            estimate.series_complete += 1
        elif has_ao3:
            estimate.series_incomplete += 1
        if book.series_id:
            estimate.series_known += 1
        if str(book.title or '').strip():
            estimate.cover_ready += 1

    estimate.series_fetch_seconds = _ao3_fetch_seconds(
        estimate.series_incomplete, estimate.request_interval
    )
    estimate.epub_fetch_seconds = _ao3_fetch_seconds(
        estimate.missing_epub, estimate.request_interval
    )
    return estimate


def format_library_estimate(
    estimate: LibraryEstimate,
    options: LibraryJobOptions | None = None,
) -> str:
    """Human text for the Process library dialog."""
    options = options or LibraryJobOptions()
    n = estimate.book_count
    noun = 'book' if n == 1 else 'books'
    lines = [
        f'This library: {n} {noun}'
        + (
            f' ({estimate.with_ao3} with an AO3 id, {estimate.without_ao3} without).'
            if n
            else '.'
        )
    ]
    if not n:
        lines.append('Nothing to do until this library has books.')
        return '\n'.join(lines)

    pace = f'{estimate.request_interval:g}s'
    if options.simplify_tags:
        lines.append('')
        lines.append(
            f'Simplify tags: {estimate.unique_tags} unique tag name(s) '
            'across the library.'
        )
        if not estimate.cache_available:
            lines.append(
                'Tag cache file not found — every uncached name would be '
                'fetched from AO3 on the first simplify.'
            )
        elif estimate.uncached_tags:
            lines.append(
                f'{estimate.cached_tags} already in the local cache; '
                f'{estimate.uncached_tags} still unmatched '
                f'({format_duration(estimate.tag_fetch_seconds)} at {pace} each).'
            )
        else:
            lines.append(
                f'All {estimate.unique_tags} unique names are already in the '
                'tag cache (no AO3 tag fetches).'
            )
        if options.needs_ao3_id() and estimate.without_ao3:
            lines.append(
                f'{estimate.without_ao3} book(s) have no AO3 id — skipped for '
                'series/download; simplify still runs on them.'
            )

    if options.fill_series or options.import_series:
        lines.append('')
        lines.append(
            f'Series: {estimate.series_complete} already have series id + name '
            f'+ part; {estimate.series_incomplete} with an AO3 id still need a '
            f'work-page lookup ({format_duration(estimate.series_fetch_seconds)} '
            f'at {pace}).'
        )
        if options.import_series:
            extra = (
                f'{estimate.series_known} book(s) already store a series id '
                '(series page fetch).'
                if estimate.series_known
                else 'No stored series ids yet — work pages are fetched first.'
            )
            lines.append(
                'Import rest of series: extra parts cannot be counted without '
                f'AO3. {extra}'
            )

    if options.download_epubs:
        lines.append('')
        lines.append(
            f'Download EPUBs: {estimate.missing_epub} with an AO3 id and no EPUB'
            + (
                f' ({format_duration(estimate.epub_fetch_seconds)} at {pace}).'
                if estimate.missing_epub
                else '.'
            )
        )
        bits = []
        if estimate.has_epub:
            bits.append(f'{estimate.has_epub} already have an EPUB')
        if estimate.without_ao3:
            bits.append(f'{estimate.without_ao3} have no AO3 id')
        if bits:
            lines.append('Skipped: ' + '; '.join(bits) + '.')
        if not estimate.missing_epub:
            lines.append('The download step will be skipped (nothing to fetch).')

    if options.generate_covers:
        lines.append('')
        lines.append(
            f'Generate covers: {estimate.cover_ready} book(s) have a title '
            '(local; no AO3).'
        )

    if options.recompute_collections and not options.simplify_tags:
        lines.append('')
        lines.append(
            f'Recompute collections: {n} {noun} (local rules; no AO3).'
        )
    elif options.simplify_tags and options.recompute_collections:
        lines.append('')
        lines.append(
            'Recompute collections is included when simplify runs.'
        )

    if not options.any_selected():
        lines.append('')
        lines.append('Choose at least one task to start a job.')

    lines.append('')
    lines.append(
        'Estimates use the open library and the local tag cache only — '
        'no AO3 URLs are loaded to build this summary.'
    )
    return '\n'.join(lines)


def library_job_title(options: LibraryJobOptions, book_count: int) -> str:
    bits: list[str] = []
    if options.simplify_tags:
        bits.append('simplify')
    if options.import_series:
        bits.append('series')
    elif options.fill_series:
        bits.append('fill series')
    if options.download_epubs:
        bits.append('EPUBs')
    if options.generate_covers:
        bits.append('covers')
    if options.recompute_collections and not options.simplify_tags:
        bits.append('collections')
    noun = 'book' if book_count == 1 else 'books'
    label = ', '.join(bits) if bits else 'library'
    return f'Process library ({book_count} {noun}: {label})'


def select_library_job_books(
    books: Iterable[LibraryBook],
    options: LibraryJobOptions,
) -> tuple[list[LibraryBook], list[dict[str, Any]]]:
    """Keep books that can run the chosen jobs; explain skips."""
    local_tasks = (
        options.simplify_tags
        or options.generate_covers
        or options.recompute_collections
    )
    # Skip no-AO3 books only when every chosen task needs a work id.
    require_ao3 = options.needs_ao3_id() and not local_tasks
    ready: list[LibraryBook] = []
    skipped: list[dict[str, Any]] = []
    for book in books:
        title = book.title or f'book {book.book_id}'
        if require_ao3 and not book.work_id and not book.url:
            skipped.append(
                {
                    'book_id': book.book_id,
                    'title': title,
                    'reason': 'no AO3 URL or work id on this book',
                }
            )
            continue
        if options.generate_covers and not str(book.title or '').strip():
            other = (
                options.simplify_tags
                or options.fill_series
                or options.import_series
                or options.download_epubs
                or options.recompute_collections
            )
            if not other:
                skipped.append(
                    {
                        'book_id': book.book_id,
                        'title': title,
                        'reason': 'no title on this book',
                    }
                )
                continue
        ready.append(book)
    return ready, skipped
