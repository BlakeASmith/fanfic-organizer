# -*- coding: utf-8 -*-
"""Import AO3 JSONL records into a Calibre library."""

from __future__ import annotations

import json
from typing import Any

from calibre.ebooks.metadata.book import Metadata

from calibre_plugins.ao3_scraper.columns import RAW_METADATA_LABEL


def record_to_json(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, indent=2)


def _meta_value(record: dict[str, Any], key: str, default=None):
    metadata = record.get('metadata') or {}
    return metadata.get(key, default)


def build_metadata(record: dict[str, Any]) -> Metadata:
    work_id = str(record.get('work_id') or '')
    title = record.get('title') or f'AO3 work {work_id or "?"}'
    author = record.get('author')
    authors = [author] if author else ['Unknown']

    mi = Metadata(title=title, authors=authors)
    identifiers = {'url': record.get('url', '')}
    if work_id:
        identifiers['ao3'] = work_id
    mi.set_identifiers(identifiers)

    tag_parts: list[str] = []
    for fandom in (record.get('fandoms') or [])[:5]:
        tag_parts.append(f'fandom:{fandom}')

    quality_score = _meta_value(record, 'quality_score')
    if quality_score is not None:
        tag_parts.append(f'ao3-score:{quality_score}')

    chapters = _meta_value(record, 'chapters') or {}
    if chapters.get('is_complete'):
        tag_parts.append('complete')

    tags = record.get('tags') or []
    mi.tags = tag_parts + tags[:50]

    summary_lines = [
        f"URL: {record.get('url', '?')}",
        f"Words: {_meta_value(record, 'words', '?')}",
        f"Kudos: {_meta_value(record, 'kudos', '?')}",
        f"Quality score: {quality_score if quality_score is not None else '?'}",
    ]
    if record.get('date'):
        summary_lines.append(f"Date: {record['date']}")
    mi.comments = '\n'.join(summary_lines)
    return mi


def find_book_by_work_id(db, work_id: str) -> int | None:
    work_id = str(work_id)
    for book_id in db.all_book_ids():
        ids = db.get_identifiers(book_id, index_is_id=True)
        if ids.get('ao3') == work_id:
            return book_id
    return None


def import_record(db, record: dict[str, Any], *, update_existing: bool = True) -> dict[str, Any]:
    work_id = str(record.get('work_id') or '')
    raw_json = record_to_json(record)
    mi = build_metadata(record)
    existing_id = find_book_by_work_id(db, work_id) if work_id else None

    if existing_id is not None and not update_existing:
        return {'book_id': existing_id, 'action': 'skipped', 'title': record.get('title')}

    if existing_id is None:
        book_id = db.create_book_entry(mi, add_duplicates=True)
        action = 'added'
    else:
        book_id = existing_id
        db.set_metadata(book_id, mi, index_is_id=True)
        action = 'updated'

    db.set_custom(book_id, raw_json, label=RAW_METADATA_LABEL, commit=True)
    return {'book_id': book_id, 'action': action, 'title': record.get('title')}


def import_records(
    db, records: list[dict[str, Any]], *, update_existing: bool = True
) -> list[dict[str, Any]]:
    return [import_record(db, record, update_existing=update_existing) for record in records]
