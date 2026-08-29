# -*- coding: utf-8 -*-
"""Build cleaned-metadata payloads and Calibre field mapping.

The JSON ``cleaned`` object is the shared contract with ao3kit. Mapping that
object onto Calibre columns follows the fanfic library layout already used
with FanFicFare:

* ``#fandom`` ← cleaned fandoms
* ``#relationships`` ← cleaned AO3 Relationship tags
* ``#collections`` ← rule collection names
* ``#originaltags`` ← pre-clean AO3 tags (warnings, ships, characters, freeforms)
* ``#summary`` ← AO3 work summary (when the column exists; Comments is also read)
* ``#wordcount`` ← AO3 word count (when present)
* Tags ← remaining cleaned tags + ``Completed``
* Series / series index ← first AO3 series membership (Calibre's built-in Series)

When those custom columns are absent, fandoms / ships / collections stay on
the standard Tags field instead of being dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

AO3_WORK_ID_RE = re.compile(
    r'(?:https?://)?(?:www\.)?archiveofourown\.org/works/(\d+)',
    re.IGNORECASE,
)

FFF_INJECTED_TAGS = frozenset({'fanfiction', 'completed', 'complete'})
RELATIONSHIP_CATEGORIES = frozenset({'relationship'})
FANDOM_CATEGORIES = frozenset({'fandom'})
COMPLETED_TAG = 'Completed'


def work_id_from_url(url: Any) -> str | None:
    if not url:
        return None
    match = AO3_WORK_ID_RE.search(str(url))
    return match.group(1) if match else None


def work_url(work_id: Any) -> str | None:
    text = str(work_id or '').strip()
    if not text:
        return None
    return f'https://archiveofourown.org/works/{text}'


def canonical_work_id(record: dict[str, Any]) -> str:
    work_id = str(record.get('work_id') or '').strip()
    if work_id:
        return work_id
    return work_id_from_url(record.get('url')) or ''


def canonical_work_url(record: dict[str, Any]) -> str:
    url = str(record.get('url') or '').strip()
    if url:
        return url
    return work_url(canonical_work_id(record)) or ''


def book_matches_work(
    identifiers: dict[str, Any] | None,
    *,
    work_id: str = '',
    url: str = '',
) -> bool:
    """True if Calibre identifiers refer to the same AO3 work."""
    ids = identifiers or {}
    work_id = str(work_id or '').strip()
    url = str(url or '').strip()
    existing_ao3 = str(ids.get('ao3') or '').strip()
    existing_url = str(ids.get('url') or '').strip()
    existing_from_url = work_id_from_url(existing_url) or ''
    wanted_from_url = work_id_from_url(url) or ''
    if work_id and existing_ao3 == work_id:
        return True
    if work_id and existing_from_url == work_id:
        return True
    if wanted_from_url and existing_from_url == wanted_from_url:
        return True
    if wanted_from_url and existing_ao3 == wanted_from_url:
        return True
    if url and existing_url.rstrip('/') == url.rstrip('/'):
        return True
    return False


def existing_book_id_from_identifiers(
    books: Iterable[tuple[Any, dict[str, Any] | None]],
    record: dict[str, Any],
) -> Any | None:
    """Return the library book id that already stores this AO3 work, if any.

    ``books`` is ``(book_id, identifiers)`` from the in-memory Calibre
    identifier maps. Same match rules as ``book_matches_work`` (ao3 id or URL).
    """
    work_id = canonical_work_id(record)
    url = canonical_work_url(record)
    if not work_id and not url:
        return None
    for book_id, ids in books:
        if book_matches_work(ids, work_id=work_id, url=url):
            return book_id
    return None


def build_cleaned_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Derive the shared ``cleaned`` object used to fill Calibre fields.

    Precedence:
    1. Explicit ``cleaned`` object on the record (from an apply step).
    2. ``tags`` already shaped like a RuledTagsResult (``simplified``, …).
    3. Fallback: treat raw tag/fandom lists as the current simplified set.
    """
    cleaned = record.get('cleaned')
    if isinstance(cleaned, dict):
        payload = dict(cleaned)
        payload.setdefault('work_id', record.get('work_id'))
        payload.setdefault('title', record.get('title'))
        return payload

    tags = record.get('tags')
    if isinstance(tags, dict) and 'simplified' in tags:
        return {
            'work_id': record.get('work_id'),
            'title': record.get('title'),
            'simplified': list(tags.get('simplified') or []),
            'collections': dict(tags.get('collections') or {}),
            'dropped': list(tags.get('dropped') or []),
            'original': list(tags.get('original') or []),
            'fandoms': _normalized_fandoms(record),
            'source': 'rules',
        }

    return {
        'work_id': record.get('work_id'),
        'title': record.get('title'),
        'simplified': [str(t) for t in (tags or [])],
        'collections': dict(record.get('collections') or {}),
        'dropped': [],
        'original': [str(t) for t in (tags or [])],
        'fandoms': _normalized_fandoms(record),
        'source': 'raw',
    }


def original_tag_names(record: dict[str, Any]) -> list[str]:
    """Pre-clean AO3 tags (ships, characters, freeforms — not fandoms)."""
    cleaned = record.get('cleaned')
    if isinstance(cleaned, dict):
        original = cleaned.get('original')
        if isinstance(original, list) and original:
            return _unique_names(original)
        detail = cleaned.get('tags')
        if isinstance(detail, list):
            from_detail = []
            for item in detail:
                if not isinstance(item, dict):
                    continue
                name = item.get('original') or item.get('mapped')
                if name:
                    from_detail.append(name)
            if from_detail:
                return _unique_names(from_detail)
    tags = record.get('tags')
    if isinstance(tags, dict):
        original = tags.get('original')
        if isinstance(original, list) and original:
            return _unique_names(original)
        return _unique_names(tags.get('simplified') or [])
    if isinstance(tags, list):
        return _unique_names(tags)
    return []


def cleaned_tag_names(record: dict[str, Any]) -> list[str]:
    """Tags to attach on the Calibre book — prefer cleaned simplified list."""
    payload = build_cleaned_payload(record)
    simplified = payload.get('simplified')
    if isinstance(simplified, list) and simplified:
        return [str(t) for t in simplified]
    tags = record.get('tags') or []
    if isinstance(tags, list):
        return [str(t) for t in tags]
    return []


def cleaned_collection_names(record: dict[str, Any]) -> list[str]:
    payload = build_cleaned_payload(record)
    collections = payload.get('collections') or {}
    if isinstance(collections, dict):
        return [str(name) for name in collections.keys() if str(name).strip()]
    if isinstance(collections, list):
        return [str(name) for name in collections if str(name).strip()]
    return []


def collections_writeback(
    existing: list[str] | None,
    incoming: list[str] | None,
) -> list[str] | None:
    """Replace Calibre collections with the computed set.

    Returns None when the column already matches. An empty computed set clears
    the column.
    """
    incoming_names = _unique_names(incoming)
    existing_names = _unique_names(existing)
    if {name.casefold() for name in incoming_names} == {
        name.casefold() for name in existing_names
    }:
        return None
    return incoming_names


def collect_collection_lines(records: list[dict[str, Any]]) -> list[str]:
    """Unique tag → collection assignments across a batch, with work counts."""
    counts: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    for record in records:
        cleaned = record.get('cleaned')
        if not isinstance(cleaned, dict):
            continue
        collections = cleaned.get('collections') or {}
        if not isinstance(collections, dict):
            continue
        seen: set[tuple[str, str]] = set()
        for name, sources in collections.items():
            collection = str(name).strip()
            if not collection:
                continue
            src_list = sources if isinstance(sources, list) else [sources]
            if not src_list:
                src_list = [collection]
            for src in src_list:
                source = str(src).strip() or collection
                row = (source, collection)
                if row in seen:
                    continue
                seen.add(row)
                if row not in counts:
                    order.append(row)
                    counts[row] = 0
                counts[row] += 1
    lines: list[str] = []
    for row in order:
        source, collection = row
        extra = f'  ({counts[row]} works)' if counts[row] > 1 else ''
        lines.append(f'{source} → {collection}{extra}')
    return lines


def format_collection_summary(records: list[dict[str, Any]]) -> str:
    lines = collect_collection_lines(records)
    if not lines:
        return 'Collection assignments: none'
    return 'Collection assignments ({} unique):\n{}'.format(
        len(lines),
        '\n'.join(f'  {line}' for line in lines),
    )


def _remap_rows_from_tag_item(item: Any) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    if not isinstance(item, dict):
        return rows
    original = str(item.get('original') or '').strip()
    mapped = item.get('mapped')
    mapped = str(mapped).strip() if mapped is not None else None
    action = str(item.get('mapping_action') or 'default')
    dropped = bool(item.get('dropped'))
    status = str(item.get('status') or '')
    rule = item.get('mapping_rule')
    metatags = [
        str(name) for name in (item.get('metatags') or []) if str(name).strip()
    ]
    if not original:
        return rows
    if dropped or action == 'drop':
        rows.append((original, '(dropped)', str(rule or status or 'drop')))
    elif mapped and mapped != original:
        if action == 'map_to':
            source = str(rule or 'rules')
        else:
            source = f'AO3 {status}' if status else 'AO3'
        rows.append((original, mapped, source))
    for meta in metatags:
        if meta and meta != original:
            rows.append((original, f'+{meta}', 'metatag'))
    return rows


def collect_remapping_lines(records: list[dict[str, Any]]) -> list[str]:
    """Unique before → after remaps across a batch, with work counts."""
    counts: dict[tuple[str, str, str], int] = {}
    order: list[tuple[str, str, str]] = []
    for record in records:
        cleaned = record.get('cleaned')
        if not isinstance(cleaned, dict):
            continue
        items = list(cleaned.get('tags') or [])
        fandom_detail = cleaned.get('fandoms_detail')
        if isinstance(fandom_detail, dict):
            items.extend(fandom_detail.get('tags') or [])
        rel_detail = cleaned.get('relationships_detail')
        if isinstance(rel_detail, dict):
            items.extend(rel_detail.get('tags') or [])
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            for row in _remap_rows_from_tag_item(item):
                if row in seen:
                    continue
                seen.add(row)
                if row not in counts:
                    order.append(row)
                    counts[row] = 0
                counts[row] += 1
    lines: list[str] = []
    for row in order:
        original, mapped, source = row
        extra = f'  ({counts[row]} works)' if counts[row] > 1 else ''
        lines.append(f'{original} → {mapped}  [{source}]{extra}')
    return lines


def format_remapping_summary(records: list[dict[str, Any]]) -> str:
    lines = collect_remapping_lines(records)
    if not lines:
        return 'Tag remappings: none (all tags already canonical)'
    return 'Tag remappings ({} unique):\n{}'.format(
        len(lines),
        '\n'.join(f'  {line}' for line in lines),
    )


def _normalized_fandoms(record: dict[str, Any]) -> list[str]:
    fandoms = record.get('fandoms')
    if isinstance(fandoms, dict) and 'simplified' in fandoms:
        return [str(x) for x in (fandoms.get('simplified') or [])]
    if isinstance(fandoms, list):
        return [str(x) for x in fandoms]
    payload = record.get('cleaned')
    if isinstance(payload, dict):
        nested = payload.get('fandoms')
        if isinstance(nested, list):
            return [str(x) for x in nested]
    return []


def _normalized_relationships(record: dict[str, Any]) -> list[str]:
    relationships = record.get('relationships')
    if isinstance(relationships, dict) and 'simplified' in relationships:
        return [str(x) for x in (relationships.get('simplified') or [])]
    if isinstance(relationships, list):
        return [str(x) for x in relationships]
    payload = record.get('cleaned')
    if isinstance(payload, dict):
        nested = payload.get('relationships')
        if isinstance(nested, list):
            return [str(x) for x in nested]
    return []


def _unique_names(values: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    if values is None:
        return names
    if isinstance(values, str):
        values = [values]
    for value in values:
        name = str(value).strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _category_of(item: dict[str, Any]) -> str:
    return str(item.get('category') or '').strip().casefold()


AO3_SERIES_ID_RE = re.compile(
    r'(?:https?://)?(?:www\.)?archiveofourown\.org/series/(\d+)',
    re.IGNORECASE,
)


def series_id_from_value(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    if text.isdigit():
        return text
    match = AO3_SERIES_ID_RE.search(text)
    return match.group(1) if match else ''


def series_memberships_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the ``series`` list on a work record."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw = record.get('series')
    if not isinstance(raw, list):
        return items
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        series_id = series_id_from_value(
            entry.get('series_id') or entry.get('url')
        )
        name = str(entry.get('name') or '').strip()
        url = str(entry.get('url') or '').strip()
        if series_id and not url:
            url = f'https://archiveofourown.org/series/{series_id}'
        if not series_id and not name:
            continue
        key = series_id or name.casefold()
        if key in seen:
            continue
        seen.add(key)
        position = entry.get('position')
        if position is not None and position != '':
            try:
                position = int(position)
            except (TypeError, ValueError):
                position = None
        else:
            position = None
        items.append(
            {
                'series_id': series_id,
                'name': name or (f'AO3 series {series_id}' if series_id else ''),
                'url': url,
                'position': position,
            }
        )
    return items


def primary_series(record: dict[str, Any]) -> dict[str, Any] | None:
    items = series_memberships_from_record(record)
    return items[0] if items else None


def calibre_fields_for_record(record: dict[str, Any]) -> dict[str, Any]:
    """Split a work record into the fanfic-library Calibre fields."""
    payload = build_cleaned_payload(record)
    fandoms = _unique_names(payload.get('fandoms') or _normalized_fandoms(record))
    collections = _unique_names(cleaned_collection_names(record))
    cleaned_rels = payload.get('relationships')
    has_cleaned_rels = isinstance(cleaned_rels, list)
    relationships = (
        _unique_names(cleaned_rels)
        if has_cleaned_rels
        else _unique_names(_normalized_relationships(record))
    )
    other_tags: list[str] = []

    fandom_keys = {name.casefold() for name in fandoms}
    rel_keys = {name.casefold() for name in relationships}

    detail = payload.get('tags')
    used_detail = False
    if isinstance(detail, list) and any(isinstance(item, dict) for item in detail):
        used_detail = True
        for item in detail:
            if not isinstance(item, dict) or item.get('dropped'):
                continue
            name = str(item.get('mapped') or '').strip()
            if not name:
                continue
            category = _category_of(item)
            if category in FANDOM_CATEGORIES:
                if name.casefold() not in fandom_keys:
                    fandoms.append(name)
                    fandom_keys.add(name.casefold())
                continue
            if name.casefold() in fandom_keys:
                continue
            if category in RELATIONSHIP_CATEGORIES:
                if name.casefold() not in rel_keys:
                    relationships.append(name)
                    rel_keys.add(name.casefold())
                continue
            if name.casefold() in rel_keys:
                continue
            other_tags.append(name)

    if not used_detail:
        for tag in cleaned_tag_names(record):
            key = tag.casefold()
            if key in fandom_keys or key in rel_keys:
                continue
            other_tags.append(tag)

    chapters = (record.get('metadata') or {}).get('chapters') or {}
    tags = _unique_names(other_tags)
    if chapters.get('is_complete'):
        if COMPLETED_TAG.casefold() not in {name.casefold() for name in tags}:
            tags.append(COMPLETED_TAG)

    words = (record.get('metadata') or {}).get('words')
    wordcount = words if isinstance(words, int) else None

    from calibre_plugins.fanfic_organizer.cover_summary import resolve_record_summary

    summary = resolve_record_summary(record)

    work_id = canonical_work_id(record)
    url = canonical_work_url(record)
    identifiers: dict[str, str] = {}
    if url:
        identifiers['url'] = url
    if work_id:
        identifiers['ao3'] = work_id

    primary = primary_series(record)
    series_name = None
    series_index = None
    series_id = ''
    if primary:
        series_name = primary.get('name') or None
        series_id = str(primary.get('series_id') or '')
        position = primary.get('position')
        if position is not None:
            series_index = float(position)
        elif series_name:
            series_index = 1.0
        if series_id:
            identifiers['ao3series'] = series_id

    return {
        'fandoms': _unique_names(fandoms),
        'relationships': _unique_names(relationships),
        'collections': collections,
        'tags': tags,
        'original_tags': original_tag_names(record),
        'wordcount': wordcount,
        'summary': summary or None,
        'work_id': work_id,
        'url': url,
        'identifiers': identifiers,
        'series': series_name,
        'series_index': series_index,
        'series_id': series_id,
        'series_list': series_memberships_from_record(record),
        'source': payload.get('source'),
    }


def series_writeback_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Series fields to copy onto an existing Calibre book.

    Does not include Tags or custom columns. ``in_series`` is False when the
    work is not part of an AO3 series (leave the existing Series field alone).
    """
    fields = calibre_fields_for_record(record)
    return {
        'series': fields.get('series'),
        'series_index': fields.get('series_index'),
        'series_id': fields.get('series_id') or '',
        'identifiers': dict(fields.get('identifiers') or {}),
        'in_series': bool(fields.get('series')),
    }


def tags_for_calibre_library(
    record: dict[str, Any],
    *,
    has_fandom_column: bool = False,
    has_relationships_column: bool = False,
    has_collections_column: bool = False,
) -> list[str]:
    """Values for Calibre's standard Tags column.

    Custom-column values are omitted from Tags when those columns exist so the
    tag browser is not a second copy of Fandom / Relationships / Collections.
    """
    fields = calibre_fields_for_record(record)
    names: list[str] = []
    seen: set[str] = set()

    def add(values: list[str]) -> None:
        for value in values:
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(value)

    if not has_fandom_column:
        add(fields['fandoms'])
    if not has_collections_column:
        add(fields['collections'])
    if not has_relationships_column:
        add(fields['relationships'])
    add(fields['tags'])
    return names


def calibre_tags_for_record(record: dict[str, Any]) -> list[str]:
    """Tags when no fanfic custom columns are available (everything in Tags)."""
    return tags_for_calibre_library(record)


def record_from_library_fields(
    *,
    title: str | None = None,
    authors: list[str] | None = None,
    identifiers: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    original_tags: list[str] | None = None,
    fandoms: list[str] | None = None,
    relationships: list[str] | None = None,
    characters: list[str] | None = None,
    wordcount: int | None = None,
    comments: str | None = None,
    summary: str | None = None,
    raw_record: dict[str, Any] | None = None,
    is_complete: bool | None = None,
    series_name: str | None = None,
    series_index: Any = None,
    require_work_id: bool = True,
) -> dict[str, Any] | None:
    """Build an ao3kit work record from Calibre fields (FanFicFare books).

    Prefers a legacy raw JSON blob when present. Otherwise reconstructs from
    URL / ``ao3`` identifiers, Original Tags (if stored), and custom columns.
    """
    from calibre_plugins.fanfic_organizer.cover_summary import resolve_record_summary

    def _attach_summary(record: dict[str, Any]) -> dict[str, Any]:
        text = resolve_record_summary(
            record,
            summary_column=summary,
            comments=comments,
        )
        if text:
            record['summary'] = text
        return record

    ids = dict(identifiers or {})
    author_list = _unique_names(authors)
    restored_series = _series_from_calibre(
        ids,
        series_name=series_name,
        series_index=series_index,
    )
    if isinstance(raw_record, dict):
        record = dict(raw_record)
        record.pop('cleaned', None)
        work_id = canonical_work_id(record) or str(ids.get('ao3') or '').strip()
        url = canonical_work_url(record) or str(ids.get('url') or '').strip()
        if not work_id:
            work_id = work_id_from_url(url) or ''
        if not url:
            url = work_url(work_id) or ''
        if not work_id and require_work_id:
            return None
        if work_id:
            record['work_id'] = work_id
        if url:
            record['url'] = url
        if title and not record.get('title'):
            record['title'] = title
        if author_list and not record.get('authors'):
            record['authors'] = author_list
            if not record.get('author'):
                record['author'] = author_list[0]
        if characters and not record.get('characters'):
            record['characters'] = _unique_names(characters)
        if restored_series and not series_memberships_from_record(record):
            record['series'] = restored_series
        return _attach_summary(record)

    work_id = str(ids.get('ao3') or '').strip() or (work_id_from_url(ids.get('url')) or '')
    url = str(ids.get('url') or '').strip() or (work_url(work_id) or '')
    if not work_id and require_work_id:
        return None

    fandom_list = _unique_names(fandoms)
    fandom_keys = {name.casefold() for name in fandom_list}
    raw_tags: list[str] = []
    seen: set[str] = set()

    def add_tag(value: Any) -> None:
        name = str(value).strip()
        if not name:
            return
        key = name.casefold()
        if key in FFF_INJECTED_TAGS or key in fandom_keys or key in seen:
            return
        seen.add(key)
        raw_tags.append(name)

    source_tags = original_tags if original_tags else tags
    for tag in source_tags or []:
        add_tag(tag)
    if not original_tags:
        for tag in relationships or []:
            add_tag(tag)

    author = author_list[0] if author_list else None

    metadata: dict[str, Any] = {}
    if isinstance(wordcount, int):
        metadata['words'] = wordcount
    if is_complete is not None:
        metadata['chapters'] = {'is_complete': bool(is_complete)}

    record = {
        'work_id': work_id,
        'url': url,
        'title': title or (f'AO3 work {work_id}' if work_id else title or 'Untitled'),
        'author': author,
        'authors': author_list,
        'fandoms': fandom_list,
        'tags': raw_tags,
        'metadata': metadata,
    }
    if characters:
        record['characters'] = _unique_names(characters)
    if relationships:
        record['relationships'] = _unique_names(relationships)
    if restored_series:
        record['series'] = restored_series
    return _attach_summary(record)


def _series_from_calibre(
    identifiers: dict[str, Any],
    *,
    series_name: str | None,
    series_index: Any,
) -> list[dict[str, Any]]:
    series_id = series_id_from_value(
        identifiers.get('ao3series') or identifiers.get('series')
    )
    name = str(series_name or '').strip()
    position = None
    if series_index not in (None, ''):
        try:
            position = int(float(series_index))
        except (TypeError, ValueError):
            position = None
    if not series_id and not name:
        return []
    url = f'https://archiveofourown.org/series/{series_id}' if series_id else ''
    return [
        {
            'series_id': series_id,
            'name': name or (f'AO3 series {series_id}' if series_id else ''),
            'url': url,
            'position': position,
        }
    ]
