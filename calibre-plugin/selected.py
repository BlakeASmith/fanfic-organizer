# -*- coding: utf-8 -*-
"""Load / write AO3 metadata for selected Calibre books."""

from __future__ import annotations

import html
import json
import re
from typing import Any

from calibre_plugins.ao3_scraper.cleaned import (
    build_cleaned_payload,
    cleaned_collection_names,
    collections_writeback,
    record_from_library_fields,
    series_writeback_from_record,
    tags_for_calibre_library,
)
from calibre_plugins.ao3_scraper.columns import (
    LEGACY_RAW_METADATA_LABEL,
    layout_columns_present,
)
from calibre_plugins.ao3_scraper.epub_plan import (
    formats_include_epub,
    plan_missing_epub_downloads,
)
from calibre_plugins.ao3_scraper.importer import (
    build_metadata,
    set_book_tags,
    write_collections_field,
    write_layout_fields,
)


_TAG_RE = re.compile(r'<[^>]+>')


def parse_custom_json(value: Any) -> dict[str, Any] | None:
    """Parse a comments-column JSON blob (may be HTML-wrapped)."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    plain = html.unescape(_TAG_RE.sub('', text)).strip()
    if not plain:
        return None
    try:
        data = json.loads(plain)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def as_name_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if ',' in text:
        return [part.strip() for part in text.split(',') if part.strip()]
    if '|' in text:
        return [part.strip() for part in text.split('|') if part.strip()]
    return [text]


def get_custom_value(db, book_id: int, label: str) -> Any:
    try:
        return db.get_custom(book_id, label=label, index_is_id=True)
    except Exception:
        return None


def get_raw_record(db, book_id: int) -> dict[str, Any] | None:
    value = get_custom_value(db, book_id, LEGACY_RAW_METADATA_LABEL)
    return parse_custom_json(value)


def record_from_calibre_book(
    db,
    book_id: int,
    *,
    require_work_id: bool = True,
) -> dict[str, Any] | None:
    """Load a work record from Original Tags, identifiers, or a legacy JSON blob."""
    title = None
    authors: list[str] = []
    identifiers: dict[str, Any] = {}
    tags: list[str] = []
    mi = None
    try:
        mi = db.get_metadata(book_id, index_is_id=True)
        title = mi.title
        authors = list(mi.authors or [])
        identifiers = mi.get_identifiers() or {}
        tags = list(mi.tags or [])
    except Exception:
        mi = None

    raw = get_raw_record(db, book_id)
    fandoms = as_name_list(get_custom_value(db, book_id, 'fandom'))
    relationships = as_name_list(get_custom_value(db, book_id, 'relationships'))
    characters = as_name_list(get_custom_value(db, book_id, 'characters'))
    original_tags = as_name_list(get_custom_value(db, book_id, 'originaltags'))
    wordcount = get_custom_value(db, book_id, 'wordcount')
    if isinstance(wordcount, str):
        try:
            wordcount = int(wordcount.replace(',', ''))
        except ValueError:
            wordcount = None
    if wordcount is not None and not isinstance(wordcount, int):
        try:
            wordcount = int(wordcount)
        except (TypeError, ValueError):
            wordcount = None

    is_complete = None
    tag_keys = {str(tag).casefold() for tag in tags}
    if 'completed' in tag_keys or 'complete' in tag_keys:
        is_complete = True

    series_name = None
    series_index = None
    if mi is not None:
        series_name = getattr(mi, 'series', None)
        series_index = getattr(mi, 'series_index', None)

    record = record_from_library_fields(
        title=title,
        authors=authors,
        identifiers=identifiers,
        tags=tags,
        original_tags=original_tags,
        fandoms=fandoms,
        relationships=relationships,
        characters=characters,
        wordcount=wordcount if isinstance(wordcount, int) else None,
        raw_record=raw,
        is_complete=is_complete,
        series_name=series_name,
        series_index=series_index,
        require_work_id=require_work_id,
    )
    if record is None:
        return None
    if mi is not None:
        uuid = getattr(mi, 'uuid', None)
        if uuid:
            record['calibre_uuid'] = str(uuid)
    record['current_collections'] = as_name_list(
        get_custom_value(db, book_id, 'collections')
    )
    return record


def load_selected_records(
    db,
    book_ids: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(ready, skipped)`` for selected book ids.

    ``ready`` items are ``{'book_id': int, 'record': dict}``.
    ``skipped`` items explain why a selection could not be simplified.
    """
    ready: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for book_id in book_ids:
        title = '?'
        try:
            mi = db.get_metadata(book_id, index_is_id=True)
            title = mi.title or title
        except Exception:
            pass
        record = record_from_calibre_book(db, book_id)
        if record is None:
            skipped.append(
                {
                    'book_id': book_id,
                    'title': title,
                    'reason': 'no AO3 URL or work id on this book',
                }
            )
            continue
        ready.append({'book_id': book_id, 'record': record, 'title': title})
    return ready, skipped


def load_selected_for_collections(
    db,
    book_ids: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(ready, skipped)`` for recomputing collection membership.

    Work id is optional — pins can use the Calibre UUID. Tags and other
    metadata are not rewritten by the caller.
    """
    ready: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for book_id in book_ids:
        title = '?'
        try:
            mi = db.get_metadata(book_id, index_is_id=True)
            title = mi.title or title
        except Exception:
            pass
        record = record_from_calibre_book(db, book_id, require_work_id=False)
        if record is None:
            skipped.append(
                {
                    'book_id': book_id,
                    'title': title,
                    'reason': 'could not load this book',
                }
            )
            continue
        if not record.get('title'):
            record['title'] = title
        ready.append({'book_id': book_id, 'record': record, 'title': title})
    return ready, skipped


def library_collection_names(db) -> list[str]:
    """Unique #collections values already in the open Calibre library."""
    api = getattr(db, 'new_api', None)
    getter = getattr(api, 'get_item_name_map', None) if api is not None else None
    mapping = None
    if callable(getter):
        for lookup in ('#collections', 'collections'):
            try:
                mapping = getter(lookup)
            except Exception:
                mapping = None
            if isinstance(mapping, dict) and mapping:
                break
            mapping = None
    if isinstance(mapping, dict):
        from calibre_plugins.ao3_scraper.collection_rules import merge_collection_names

        return merge_collection_names(mapping.values())
    return []


def pin_targets_from_selected(
    db,
    book_ids: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(ready, skipped)`` pin targets (AO3 work id or Calibre UUID)."""
    ready: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for book_id in book_ids:
        title = '?'
        try:
            mi = db.get_metadata(book_id, index_is_id=True)
            title = mi.title or title
        except Exception:
            pass
        record = record_from_calibre_book(db, book_id, require_work_id=False)
        work_id = str((record or {}).get('work_id') or '').strip()
        uuid = str((record or {}).get('calibre_uuid') or '').strip()
        if not work_id and not uuid:
            skipped.append(
                {
                    'book_id': book_id,
                    'title': title,
                    'reason': 'no AO3 work id or Calibre UUID',
                }
            )
            continue
        ready.append(
            {
                'book_id': book_id,
                'title': title,
                'work_id': work_id,
                'uuid': uuid,
            }
        )
    return ready, skipped


def load_records_for_tag_warm(db, book_ids: list[int]) -> list[dict[str, Any]]:
    """Reconstructed work records for background tag-cache warming.

    Work id is optional — any book with tags/fandoms can contribute names.
    """
    records: list[dict[str, Any]] = []
    for book_id in book_ids:
        record = record_from_calibre_book(db, book_id, require_work_id=False)
        if record is not None:
            records.append(record)
    return records


def book_has_epub(db, book_id: int) -> bool:
    """True when the Calibre book already has an EPUB format."""
    try:
        has_format = getattr(db, 'has_format', None)
        if callable(has_format):
            try:
                return bool(has_format(book_id, 'EPUB', index_is_id=True))
            except TypeError:
                return bool(has_format(book_id, 'EPUB'))
    except Exception:
        pass
    try:
        formats = db.formats(book_id, index_is_id=True)
    except TypeError:
        try:
            formats = db.formats(book_id)
        except Exception:
            return False
    except Exception:
        return False
    return formats_include_epub(formats)


def snapshot_selected_for_epub(db, book_ids: list[int]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for book_id in book_ids:
        title = '?'
        try:
            mi = db.get_metadata(book_id, index_is_id=True)
            title = mi.title or title
        except Exception:
            pass
        snapshots.append(
            {
                'book_id': book_id,
                'title': title,
                'record': record_from_calibre_book(db, book_id),
                'has_epub': book_has_epub(db, book_id),
            }
        )
    return snapshots


def load_selected_for_epub_download(
    db,
    book_ids: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(ready, skipped)`` for selected books missing a native EPUB."""
    return plan_missing_epub_downloads(snapshot_selected_for_epub(db, book_ids))


def load_selected_similar_records(
    db,
    book_ids: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(ready, skipped)`` for Search similar.

    Work id is optional; a book is usable if it has fandoms, tags, ships,
    characters, or an author.
    """
    from calibre_plugins.ao3_scraper.similar import facets_from_record

    ready: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for book_id in book_ids:
        title = '?'
        try:
            mi = db.get_metadata(book_id, index_is_id=True)
            title = mi.title or title
        except Exception:
            pass
        record = record_from_calibre_book(db, book_id, require_work_id=False)
        if record is None:
            skipped.append(
                {
                    'book_id': book_id,
                    'title': title,
                    'reason': 'no metadata on this book',
                }
            )
            continue
        if not record.get('title'):
            record['title'] = title
        if not facets_from_record(record).has_any():
            skipped.append(
                {
                    'book_id': book_id,
                    'title': title,
                    'reason': 'no fandom, tags, characters, or author to search from',
                }
            )
            continue
        ready.append({'book_id': book_id, 'record': record, 'title': title})
    return ready, skipped


def apply_cleaned_record(db, book_id: int, record: dict[str, Any]) -> dict[str, Any]:
    """Write layout columns and replace Calibre Tags from the simplified set."""
    layout = layout_columns_present(db)
    existing_identifiers = None
    existing_comments = None
    try:
        existing = db.get_metadata(book_id, index_is_id=True)
        existing_identifiers = existing.get_identifiers()
        existing_comments = existing.comments
    except Exception:
        existing = None

    mi = build_metadata(
        record,
        layout=layout,
        existing_identifiers=existing_identifiers,
        existing_comments=existing_comments,
    )
    tags = tags_for_calibre_library(
        record,
        has_fandom_column=bool(layout.get('fandom')),
        has_relationships_column=bool(layout.get('relationships')),
        has_collections_column=bool(layout.get('collections')),
    )
    mi.tags = tags
    try:
        db.set_metadata(book_id, mi, force_changes=True)
    except TypeError:
        db.set_metadata(book_id, mi)

    set_book_tags(db, book_id, tags)
    write_layout_fields(db, book_id, record)
    payload = build_cleaned_payload(record)
    return {
        'book_id': book_id,
        'action': 'updated',
        'title': record.get('title'),
        'simplified': list(payload.get('simplified') or []),
        'tags': tags,
    }


def apply_cleaned_records(
    db,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """``items`` are ``{'book_id', 'record'}`` after enrichment."""
    outcomes: list[dict[str, Any]] = []
    for item in items:
        outcomes.append(apply_cleaned_record(db, item['book_id'], item['record']))
    return outcomes


def apply_collections_record(db, book_id: int, record: dict[str, Any]) -> dict[str, Any]:
    """Replace Collections with the computed set. Tags are left unchanged."""
    incoming = cleaned_collection_names(record)
    existing = as_name_list(get_custom_value(db, book_id, 'collections'))
    combined = collections_writeback(existing, incoming)
    title = record.get('title')
    if combined is None:
        return {
            'book_id': book_id,
            'action': 'unchanged',
            'title': title,
            'collections': existing,
            'added': [],
        }
    wrote = write_collections_field(db, book_id, combined)
    existing_keys = {name.casefold() for name in existing}
    added = [name for name in combined if name.casefold() not in existing_keys]
    removed = [name for name in existing if name.casefold() not in {n.casefold() for n in combined}]
    return {
        'book_id': book_id,
        'action': 'updated' if wrote else 'unchanged',
        'title': title,
        'collections': combined if wrote else existing,
        'added': added if wrote else [],
        'removed': removed if wrote else [],
    }


def apply_collections_records(
    db,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for item in items:
        outcomes.append(apply_collections_record(db, item['book_id'], item['record']))
    return outcomes


def apply_series_record(db, book_id: int, record: dict[str, Any]) -> dict[str, Any]:
    """Write Series / series index / ``ao3series`` without changing tags or formats."""
    patch = series_writeback_from_record(record)
    mi = db.get_metadata(book_id, index_is_id=True)
    identifiers = dict(mi.get_identifiers() or {})
    identifiers.update(patch.get('identifiers') or {})
    mi.set_identifiers(identifiers)
    wrote_series = False
    if patch.get('in_series') and patch.get('series'):
        mi.series = patch['series']
        index = patch.get('series_index')
        mi.series_index = float(index) if index is not None else 1.0
        wrote_series = True
    try:
        db.set_metadata(book_id, mi, force_changes=True)
    except TypeError:
        db.set_metadata(book_id, mi)
    return {
        'book_id': book_id,
        'action': 'updated' if wrote_series else 'unchanged',
        'title': record.get('title') or getattr(mi, 'title', None),
        'series': patch.get('series') if wrote_series else getattr(mi, 'series', None),
        'series_index': patch.get('series_index') if wrote_series else getattr(mi, 'series_index', None),
        'in_series': wrote_series,
    }


def apply_series_records(
    db,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """``items`` are ``{'book_id', 'record'}`` after series lookup."""
    outcomes: list[dict[str, Any]] = []
    for item in items:
        outcomes.append(apply_series_record(db, item['book_id'], item['record']))
    return outcomes
