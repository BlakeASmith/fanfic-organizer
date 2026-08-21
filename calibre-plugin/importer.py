# -*- coding: utf-8 -*-
"""Import AO3 JSONL records into a Calibre library."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from calibre.ebooks.metadata import MetaInformation

from calibre_plugins.ao3_scraper.cleaned import (
    build_cleaned_payload,
    cleaned_collection_names,
    cleaned_tag_names,
)
from calibre_plugins.ao3_scraper.columns import (
    CLEANED_METADATA_LABEL,
    RAW_METADATA_LABEL,
)
from calibre_plugins.ao3_scraper.jsonl_loader import resolve_epub_path


def record_to_json(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, indent=2)


def cleaned_record_to_json(record: dict[str, Any]) -> str:
    return json.dumps(build_cleaned_payload(record), ensure_ascii=False, indent=2)


def _meta_value(record: dict[str, Any], key: str, default=None):
    metadata = record.get('metadata') or {}
    return metadata.get(key, default)


def build_metadata(record: dict[str, Any]) -> MetaInformation:
    work_id = str(record.get('work_id') or '')
    title = record.get('title') or f'AO3 work {work_id or "?"}'
    author = record.get('author')
    authors = [author] if author else ['Unknown']

    mi = MetaInformation(title, authors)
    identifiers = {'url': record.get('url', '')}
    if work_id:
        identifiers['ao3'] = work_id
    mi.set_identifiers(identifiers)

    tag_parts: list[str] = []
    fandoms = record.get('fandoms')
    if isinstance(fandoms, dict):
        fandom_names = list(fandoms.get('simplified') or [])
    else:
        fandom_names = list(fandoms or [])
    for fandom in fandom_names[:5]:
        tag_parts.append(f'fandom:{fandom}')

    for collection in cleaned_collection_names(record)[:20]:
        tag_parts.append(f'collection:{collection}')

    quality_score = _meta_value(record, 'quality_score')
    if quality_score is not None:
        tag_parts.append(f'ao3-score:{quality_score}')

    chapters = _meta_value(record, 'chapters') or {}
    if chapters.get('is_complete'):
        tag_parts.append('complete')

    tags = cleaned_tag_names(record)
    mi.tags = tag_parts + tags[:50]

    summary_lines = [
        f"URL: {record.get('url', '?')}",
        f"Words: {_meta_value(record, 'words', '?')}",
        f"Kudos: {_meta_value(record, 'kudos', '?')}",
        f"Quality score: {quality_score if quality_score is not None else '?'}",
    ]
    if record.get('date'):
        summary_lines.append(f"Date: {record['date']}")
    collections = cleaned_collection_names(record)
    if collections:
        summary_lines.append('Collections: ' + ', '.join(collections))
    mi.comments = '\n'.join(summary_lines)
    return mi


def find_book_by_work_id(db, work_id: str) -> int | None:
    work_id = str(work_id)
    search = getattr(db, 'search_getting_ids', None)
    if search is not None:
        try:
            ids = search(
                f'identifiers:ao3:{work_id}',
                None,
                use_virtual_library=False,
            )
            if ids:
                return ids[0]
        except Exception:
            pass
    for book_id in db.all_ids():
        ids = db.get_identifiers(book_id, index_is_id=True)
        if ids.get('ao3') == work_id:
            return book_id
    return None


def add_epub_format(db, book_id: int, epub_path: Path) -> bool:
    db.add_format(book_id, 'EPUB', str(epub_path), index_is_id=True)
    return True


def import_record(
    db,
    record: dict[str, Any],
    *,
    update_existing: bool = True,
    bundle_root: str | Path | None = None,
) -> dict[str, Any]:
    work_id = str(record.get('work_id') or '')
    raw_json = record_to_json(record)
    cleaned_json = cleaned_record_to_json(record)
    mi = build_metadata(record)
    existing_id = find_book_by_work_id(db, work_id) if work_id else None

    if existing_id is not None and not update_existing:
        return {
            'book_id': existing_id,
            'action': 'skipped',
            'title': record.get('title'),
            'epub': False,
        }

    if existing_id is None:
        book_id = db.create_book_entry(mi, add_duplicates=True)
        action = 'added'
    else:
        book_id = existing_id
        db.set_metadata(book_id, mi)
        action = 'updated'

    db.set_custom(book_id, raw_json, label=RAW_METADATA_LABEL, commit=False)
    db.set_custom(book_id, cleaned_json, label=CLEANED_METADATA_LABEL, commit=True)

    attached = False
    if bundle_root is not None:
        epub_path = resolve_epub_path(record, bundle_root)
        if epub_path is not None:
            attached = add_epub_format(db, book_id, epub_path)

    return {
        'book_id': book_id,
        'action': action,
        'title': record.get('title'),
        'epub': attached,
    }


def import_records(
    db,
    records: list[dict[str, Any]],
    *,
    update_existing: bool = True,
    bundle_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    return [
        import_record(
            db,
            record,
            update_existing=update_existing,
            bundle_root=bundle_root,
        )
        for record in records
    ]
