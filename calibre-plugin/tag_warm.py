# -*- coding: utf-8 -*-
"""Calibre-free helpers for background tag-cache warming."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

NAME_KEYS = ('tags', 'fandoms', 'relationships', 'characters')


def _as_name_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict) and 'simplified' in value:
        value = value.get('simplified') or []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def unique_tag_names_from_records(records: list[dict[str, Any]]) -> list[str]:
    """Stable-ordered unique names from reconstructed library records."""
    seen: set[str] = set()
    ordered: list[str] = []
    for record in records:
        for key in NAME_KEYS:
            for name in _as_name_list(record.get(key)):
                if name in seen:
                    continue
                seen.add(name)
                ordered.append(name)
    return ordered


def write_names_file(path: Path, names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(f'{name}\n' for name in names), encoding='utf-8')


GRAPH_RECORD_KEYS = (
    'work_id',
    'title',
    'url',
    'authors',
    'tags',
    'fandoms',
    'relationships',
    'characters',
    'calibre_uuid',
)


def write_graph_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write slim work records for ``tags graph --jsonl``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        for record in records:
            slim: dict[str, Any] = {}
            for key in GRAPH_RECORD_KEYS:
                value = record.get(key)
                if value:
                    slim[key] = value
            if not slim.get('authors'):
                author = record.get('author')
                if author:
                    slim['authors'] = (
                        author if isinstance(author, list) else [str(author).strip()]
                    )
            if not slim.get('title') and not slim.get('work_id'):
                continue
            handle.write(json.dumps(slim, ensure_ascii=False) + '\n')


def parse_warm_status_json(stdout: str) -> dict[str, Any] | None:
    text = (stdout or '').strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find('{')
        end = text.rfind('}')
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def format_warm_started_text(
    status: dict[str, Any],
    *,
    book_count: int,
    name_count: int,
    already: bool = False,
) -> str:
    pid = status.get('pid')
    uncached = status.get('uncached')
    cached = status.get('cached')
    interval = status.get('interval_seconds') or 10
    if not status.get('running') and not already and uncached == 0:
        return (
            f'All {name_count} unique tags from {book_count} book(s) are '
            'already in the cache. Nothing to fetch.\n\n'
            'Start this again after importing new works.'
        )
    if already:
        lines = [
            f'Background tag cache is already running (pid {pid}).',
            f'Updated the tag list from the library ({uncached} still uncached).',
        ]
    else:
        lines = [f'Background tag cache started (pid {pid}).']
    lines.extend(
        [
            '',
            f'Scanned {book_count} book(s); {name_count} unique tags.',
        ]
    )
    if cached is not None and uncached is not None:
        lines.append(
            f'{cached} already cached, {uncached} will be fetched from AO3.'
        )
    lines.extend(
        [
            '',
            f'Pace is about {interval:g}s between tag fetches so Search and '
            'Download can still use AO3.',
            "The library is not modified — only ao3kit's tag cache is filled.",
            'Stop it from Tags and collections → Stop tag cache, or: '
            'python -m ao3kit tags warm stop',
        ]
    )
    message = str(status.get('message') or '').strip()
    if message:
        lines.extend(['', message])
    log_path = str(status.get('log_path') or '').strip()
    if log_path:
        lines.extend(['', f'Log: {log_path}'])
    lines.append(
        'Watch it from Tags and collections → Tag cache log, or: '
        'python -m ao3kit tags warm log --follow'
    )
    return '\n'.join(lines)


LOG_READ_MAX_BYTES = 2_000_000
DEFAULT_LOG_LINES = 400


def warm_cache_dir(project: Path) -> Path:
    return Path(project) / '.cache'


def warm_log_path(project: Path) -> Path:
    return warm_cache_dir(project) / 'tag_warm.log'


def warm_status_path(project: Path) -> Path:
    return warm_cache_dir(project) / 'tag_warm.status.json'


def read_log_tail(
    path: Path,
    *,
    lines: int = DEFAULT_LOG_LINES,
    max_bytes: int = LOG_READ_MAX_BYTES,
) -> str:
    """Last ``lines`` of a log file (``<= 0`` means all, still size-capped)."""
    path = Path(path)
    if not path.is_file():
        return ''
    size = path.stat().st_size
    with path.open('r', encoding='utf-8', errors='replace') as handle:
        if size > max_bytes:
            handle.seek(max(0, size - max_bytes))
            handle.readline()
        text = handle.read()
    if not text or lines <= 0:
        return text
    parts = text.splitlines()
    if len(parts) <= lines:
        return text if text.endswith('\n') or not text else text + '\n'
    return '\n'.join(parts[-lines:]) + '\n'


def read_status_file(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def format_warm_log_header(status: dict[str, Any], log_path: Path) -> str:
    if status.get('running'):
        head = f'Running (pid {status.get("pid")}).'
    else:
        head = 'Not running.'
    bits = [head]
    source = status.get('source_count')
    cached = status.get('cached')
    uncached = status.get('uncached')
    if source:
        bits.append(f'{cached}/{source} cached, {uncached} remaining.')
    message = str(status.get('message') or '').strip()
    if message:
        bits.append(message)
    bits.append(f'Log: {log_path}')
    return ' '.join(str(bit) for bit in bits if bit)


STOP_TAG_PREVIEW = 25


def format_warm_stopped_dialog(status: dict[str, Any]) -> tuple[str, str]:
    """Calibre stop dialog: ``(summary, details)``. Details go in Show details."""
    raw = str(status.get('message') or '').strip()
    stopped = raw.splitlines()[0].strip() if raw else 'Stopped.'
    tags = [
        str(item).strip()
        for item in (status.get('fetched_tags') or [])
        if str(item).strip()
    ]
    try:
        fetched = int(status.get('fetched') or 0)
    except (TypeError, ValueError):
        fetched = 0
    fetched = max(fetched, len(tags))
    try:
        remaining = int(status.get('uncached') or 0)
    except (TypeError, ValueError):
        remaining = 0
    try:
        errors = int(status.get('errors') or 0)
    except (TypeError, ValueError):
        errors = 0

    lines = [stopped, '']
    extras: list[str] = []
    if remaining:
        extras.append(f'{remaining} still remaining')
    if errors:
        extras.append(f'{errors} error{"s" if errors != 1 else ""}')
    suffix = f' ({", ".join(extras)})' if extras else ''
    if fetched:
        noun = 'tag' if fetched == 1 else 'tags'
        lines.append(f'Cached {fetched} {noun} this run{suffix}.')
    else:
        lines.append(f'No new tags were cached this run{suffix}.')

    details = '\n'.join(tags)
    if tags:
        lines.append('')
        lines.extend(tags[:STOP_TAG_PREVIEW])
        overflow = len(tags) - STOP_TAG_PREVIEW
        if overflow > 0:
            lines.append(f'… and {overflow} more (Show details).')
    return '\n'.join(lines).strip(), details
