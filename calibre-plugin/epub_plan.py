# -*- coding: utf-8 -*-
"""Plan native EPUB downloads for library books that are missing a file.

Calibre-free so pytest can import this module without Calibre.
"""

from __future__ import annotations

from typing import Any

REASON_NO_AO3 = 'no AO3 URL or work id on this book'
REASON_HAS_EPUB = 'already has an EPUB'


def formats_include_epub(formats: Any) -> bool:
    """True if a Calibre ``formats()`` value already lists EPUB."""
    if formats is None or formats is False:
        return False
    if formats is True:
        return True
    if isinstance(formats, (list, tuple, set)):
        names = [str(item).strip().upper() for item in formats if str(item).strip()]
    else:
        names = [
            part.strip().upper()
            for part in str(formats).replace(';', ',').split(',')
            if part.strip()
        ]
    return 'EPUB' in names


def plan_missing_epub_downloads(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split snapshots into downloadable books vs skips.

    Each item is ``{book_id, title, record, has_epub}``. ``record`` is an
    ao3kit work dict or ``None`` when the book has no AO3 id/URL.
    """
    ready: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        book_id = item.get('book_id')
        title = item.get('title') or str(book_id)
        record = item.get('record')
        if not isinstance(record, dict) or not (
            str(record.get('work_id') or '').strip()
            or str(record.get('url') or '').strip()
        ):
            skipped.append(
                {'book_id': book_id, 'title': title, 'reason': REASON_NO_AO3}
            )
            continue
        if item.get('has_epub'):
            skipped.append(
                {'book_id': book_id, 'title': title, 'reason': REASON_HAS_EPUB}
            )
            continue
        ready.append({'book_id': book_id, 'record': record, 'title': title})
    return ready, skipped


def merge_download_manifest(
    items: list[dict[str, Any]],
    downloaded_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy ``epub_file`` / errors from the download JSONL back onto items."""
    by_id: dict[str, dict[str, Any]] = {}
    for record in downloaded_records:
        work_id = str(record.get('work_id') or '').strip()
        if work_id:
            by_id[work_id] = record
    merged: list[dict[str, Any]] = []
    for item in items:
        record = dict(item.get('record') or {})
        work_id = str(record.get('work_id') or '').strip()
        updated = by_id.get(work_id, record)
        merged.append({**item, 'record': updated})
    return merged


def pending_epub_attachments(
    items: list[dict[str, Any]],
    downloaded_records: list[dict[str, Any]],
    already_seen: set[Any],
) -> list[dict[str, Any]]:
    """Items whose download JSONL now has ``epub_file`` and are not yet seen."""
    pending: list[dict[str, Any]] = []
    for item in merge_download_manifest(items, downloaded_records):
        book_id = item.get('book_id')
        if book_id in already_seen:
            continue
        record = item.get('record') or {}
        if str(record.get('epub_file') or '').strip():
            pending.append(item)
    return pending


def import_fingerprint(record: dict[str, Any]) -> tuple:
    """Stable metadata snapshot so stub → scraped upgrades re-enter Calibre."""
    meta = record.get('metadata') if isinstance(record.get('metadata'), dict) else {}
    return (
        str(record.get('title') or ''),
        tuple(str(x) for x in (record.get('fandoms') or [])),
        tuple(str(x) for x in (record.get('tags') or [])),
        tuple(str(x) for x in (record.get('relationships') or [])),
        str(meta.get('words') or record.get('words') or ''),
        str(record.get('summary') or ''),
    )


def pending_incremental_imports(
    records: list[dict[str, Any]],
    imported: dict[str, dict[str, Any]],
    *,
    work_id_of,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split JSONL rows into new works vs works whose EPUB is now ready.

    ``imported`` maps work id → ``{book_id, has_epub, fingerprint?}`` for rows
    already written to Calibre this job. New rows may already list ``epub_file``;
    the caller attaches that file on the first import. When a later JSONL row
    has a different metadata fingerprint (Fill from AO3 seed → scraped page),
    the row is returned again so Calibre gets the real metadata.
    """
    new_records: list[dict[str, Any]] = []
    epub_records: list[dict[str, Any]] = []
    for record in records:
        work_id = str(work_id_of(record) or '').strip()
        if not work_id:
            continue
        state = imported.get(work_id)
        fp = import_fingerprint(record)
        if state is None:
            new_records.append(record)
            continue
        prev_fp = state.get('fingerprint')
        if prev_fp is not None and prev_fp != fp:
            new_records.append(record)
            continue
        if str(record.get('epub_file') or '').strip() and not state.get('has_epub'):
            epub_records.append(record)
    return new_records, epub_records


def summarize_epub_download(
    outcomes: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    *,
    cancelled: bool = False,
) -> str:
    added = sum(1 for item in outcomes if item.get('action') == 'added')
    failed = sum(1 for item in outcomes if item.get('action') == 'failed')
    already = sum(1 for item in skipped if item.get('reason') == REASON_HAS_EPUB)
    no_id = len(skipped) - already
    noun = 'book' if added == 1 else 'books'
    parts = [f'Added EPUB to {added} {noun}']
    if already:
        parts.append(f'skipped {already} that already had one')
    if no_id:
        parts.append(f'skipped {no_id} without an AO3 URL / work id')
    if failed:
        parts.append(f'{failed} failed')
    text = '; '.join(parts) + '.'
    if cancelled:
        if added:
            return text.rstrip('.') + ' Cancelled before the rest finished.'
        return 'Cancelled before any EPUB was added.'
    return text

