# -*- coding: utf-8 -*-
"""Copy AO3 synopsis into Calibre Comments for Kobo / device sync."""

from __future__ import annotations

from typing import Any

from calibre_plugins.fanfic_organizer.cover_summary import (
    classify_synopsis,
    comments_for_import_synopsis,
    resolve_record_summary,
)
from calibre_plugins.fanfic_organizer.columns import layout_columns_present
from calibre_plugins.fanfic_organizer.importer import _set_custom


def repair_synopsis_on_book(
    db,
    book_id: int,
    *,
    comments: str = '',
    summary: str = '',
    work_id: str = '',
    layout: dict[str, bool] | None = None,
) -> str:
    """Repair one book. Returns ``updated``, ``ok``, ``need_fetch``, or ``skipped``."""
    present = layout if layout is not None else layout_columns_present(db)
    state = classify_synopsis(comments, summary, work_id=work_id)
    if state == 'ok':
        return 'ok'
    if state == 'need_fetch':
        return 'need_fetch'
    if state != 'needs_local':
        return 'skipped'
    text = resolve_record_summary({}, summary_column=summary, comments=comments)
    if not text:
        return 'skipped'
    new_comments = comments_for_import_synopsis(text, comments)
    changed = False
    try:
        mi = db.get_metadata(int(book_id), index_is_id=True)
    except Exception:
        return 'skipped'
    if new_comments is not None and new_comments != str(comments or ''):
        mi.comments = new_comments
        changed = True
    if changed:
        try:
            db.set_metadata(int(book_id), mi, force_changes=True)
        except TypeError:
            db.set_metadata(int(book_id), mi)
    if present.get('summary') and not str(summary or '').strip():
        if _set_custom(db, int(book_id), 'summary', text, commit=False):
            changed = True
    commit = getattr(db, 'commit', None)
    if changed and callable(commit):
        commit()
    return 'updated' if changed else 'ok'


def repair_synopsis_for_items(
    db,
    items: list[dict[str, Any]],
    *,
    books_by_id: dict[int, Any] | None = None,
) -> dict[str, Any]:
    """Repair synopsis on ready library job items."""
    layout = layout_columns_present(db)
    counts = {
        'updated': 0,
        'ok': 0,
        'need_fetch': 0,
        'skipped': 0,
    }
    need_fetch: list[dict[str, Any]] = []
    updated_ids: list[int] = []
    for item in items:
        book_id = int(item.get('book_id') or 0)
        if not book_id:
            counts['skipped'] += 1
            continue
        book = (books_by_id or {}).get(book_id)
        comments = str(getattr(book, 'comments', None) or item.get('comments') or '')
        summary = str(getattr(book, 'summary', None) or item.get('summary') or '')
        work_id = str(getattr(book, 'work_id', None) or '')
        if not work_id:
            record = item.get('record') if isinstance(item.get('record'), dict) else {}
            work_id = str(record.get('work_id') or '')
        outcome = repair_synopsis_on_book(
            db,
            book_id,
            comments=comments,
            summary=summary,
            work_id=work_id,
            layout=layout,
        )
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == 'updated':
            updated_ids.append(book_id)
        if outcome == 'need_fetch':
            need_fetch.append(
                {
                    'book_id': book_id,
                    'title': item.get('title') or f'book {book_id}',
                }
            )
    return {
        'counts': counts,
        'need_fetch': need_fetch,
        'updated_ids': updated_ids,
    }


def summarize_synopsis_repair(result: dict[str, Any]) -> tuple[str, str]:
    counts = result.get('counts') or {}
    updated = int(counts.get('updated') or 0)
    need_fetch = result.get('need_fetch') or []
    parts = [f'Synced synopsis on {updated} book(s)']
    ok = int(counts.get('ok') or 0)
    if ok:
        parts.append(f'{ok} already had Comments set')
    summary = ', '.join(parts) + '.'
    detail = ''
    if need_fetch:
        summary += f' {len(need_fetch)} still need Fill from AO3 (no summary stored locally).'
        detail = '\n'.join(
            f"{row.get('title')}: no summary in library"
            for row in need_fetch[:30]
        )
    return summary, detail
