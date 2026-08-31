# -*- coding: utf-8 -*-
"""Omnibus helpers for the Calibre plugin (mostly Calibre-free)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


def _ensure_ao3kit() -> None:
    """Bootstrap ``ao3kit`` on Calibre's sys.path (checkout or bundled zip)."""
    try:
        import ao3kit  # noqa: F401

        return
    except ImportError:
        pass
    try:
        from calibre_plugins.fanfic_organizer.runtime import ensure_ao3kit_importable
    except ImportError:
        from runtime import ensure_ao3kit_importable  # type: ignore

    if not ensure_ao3kit_importable():
        raise ImportError(
            'ao3kit is not available in Calibre\'s Python. '
            'Re-install Fanfic Organizer from GitHub Releases, or set '
            'Project path in Plugin settings.'
        )


def member_id_from_record(record: dict[str, Any]) -> str:
    _ensure_ao3kit()
    from ao3kit.omnibus import member_id_from_record as _impl

    return _impl(record)


def series_omnibus_title(series_name: str) -> str:
    _ensure_ao3kit()
    from ao3kit.omnibus import series_omnibus_title as _impl

    return _impl(series_name)


def sort_collection_members(records):
    _ensure_ao3kit()
    from ao3kit.omnibus import sort_collection_members as _impl

    return _impl(records)


def merge_omnibus_record(*args, **kwargs):
    _ensure_ao3kit()
    from ao3kit.omnibus import merge_omnibus_record as _impl

    return _impl(*args, **kwargs)


def read_omnibus_meta(epub):
    _ensure_ao3kit()
    from ao3kit.epub_merge import read_omnibus_meta as _impl

    return _impl(epub)


def read_omnibus_members(epub):
    _ensure_ao3kit()
    from ao3kit.epub_merge import read_omnibus_members as _impl

    return _impl(epub)


def reorder_members(omnibus_path, member_ids_in_order, dest=None):
    _ensure_ao3kit()
    from ao3kit.epub_merge import reorder_members as _impl

    return _impl(omnibus_path, member_ids_in_order, dest)


def update_omnibus_sidecar_file(omnibus_path, *, meta_updates=None, members=None, dest=None):
    _ensure_ao3kit()
    from ao3kit.epub_merge import update_omnibus_sidecar_file as _impl

    return _impl(
        omnibus_path, meta_updates=meta_updates, members=members, dest=dest
    )


def is_omnibus_identifiers(identifiers: dict[str, Any] | None) -> bool:
    ids = identifiers or {}
    return bool(str(ids.get('omnibus') or '').strip())


def is_omnibus_book(db, book_id: int) -> bool:
    try:
        mi = db.get_metadata(book_id, index_is_id=True)
        return is_omnibus_identifiers(mi.get_identifiers() if mi else None)
    except Exception:
        return False


def omnibus_id_of(identifiers: dict[str, Any] | None) -> str:
    return str((identifiers or {}).get('omnibus') or '').strip()


def collection_key(name: str) -> str:
    return str(name or '').strip()


def find_omnibus_book_id(
    db,
    *,
    omnibus_id: str = '',
    series_id: str = '',
    collection: str = '',
) -> int | None:
    """Find an existing omnibus row by id, series, or collection name."""
    api = getattr(db, 'new_api', None)
    all_ids = list(getattr(db, 'all_ids', lambda: [])() or [])
    if api is not None:
        getter = getattr(api, 'all_field_for', None)
        if callable(getter):
            try:
                id_map = getter('identifiers', all_ids) or {}
            except Exception:
                id_map = {}
            for book_id, ids in id_map.items():
                if not isinstance(ids, dict):
                    continue
                if omnibus_id and str(ids.get('omnibus') or '') == omnibus_id:
                    return int(book_id)
                if series_id and str(ids.get('omnibus') or '') and str(
                    ids.get('ao3series') or ''
                ) == str(series_id):
                    return int(book_id)
                if collection and str(ids.get('omnibus') or '') and str(
                    ids.get('omnibuscollection') or ''
                ) == collection:
                    return int(book_id)
    for book_id in all_ids:
        try:
            mi = db.get_metadata(book_id, index_is_id=True)
            ids = mi.get_identifiers() or {}
        except Exception:
            continue
        if not ids.get('omnibus'):
            continue
        if omnibus_id and str(ids.get('omnibus')) == omnibus_id:
            return int(book_id)
        if series_id and str(ids.get('ao3series') or '') == str(series_id):
            return int(book_id)
        if collection and str(ids.get('omnibuscollection') or '') == collection:
            return int(book_id)
    return None


def list_collection_names(db) -> list[str]:
    try:
        from calibre_plugins.fanfic_organizer.selected import library_collection_names
    except ImportError:
        from selected import library_collection_names

    names = list(library_collection_names(db) or [])
    return sorted(set(names), key=lambda s: s.casefold())


def book_has_epub(db, book_id: int) -> bool:
    try:
        return bool(db.has_format(book_id, 'EPUB', index_is_id=True))
    except TypeError:
        try:
            return bool(db.has_format(book_id, 'EPUB'))
        except Exception:
            return False
    except Exception:
        return False


def load_book_as_member_record(db, book_id: int) -> dict[str, Any]:
    try:
        from calibre_plugins.fanfic_organizer.selected import record_from_calibre_book
    except ImportError:
        from selected import record_from_calibre_book

    record = record_from_calibre_book(db, book_id, require_work_id=False)
    if record is None:
        mi = db.get_metadata(book_id, index_is_id=True)
        ids = mi.get_identifiers() or {}
        record = {
            'work_id': str(ids.get('ao3') or ids.get('wikipedia') or book_id),
            'title': mi.title or str(book_id),
            'author': (mi.authors or ['Unknown'])[0],
            'authors': list(mi.authors or []),
            'identifiers': dict(ids),
        }
    record['calibre_book_id'] = book_id
    cols = list(record.get('current_collections') or [])
    if cols:
        cleaned = dict(record.get('cleaned') or {})
        cleaned.setdefault('collections', cols)
        record['cleaned'] = cleaned
    return record


def members_for_book_ids(db, book_ids: list[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (ready, skipped) for combine. Skips omnibus rows."""
    ready: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for book_id in book_ids:
        if is_omnibus_book(db, book_id):
            skipped.append({'book_id': book_id, 'reason': 'omnibus'})
            continue
        record = load_book_as_member_record(db, book_id)
        if not book_has_epub(db, book_id):
            skipped.append({'book_id': book_id, 'record': record, 'reason': 'no_epub'})
            continue
        ready.append({'book_id': book_id, 'record': record})
    return ready, skipped


def members_for_series(db, series_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ready: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    all_ids = list(getattr(db, 'all_ids', lambda: [])() or [])
    for book_id in all_ids:
        if is_omnibus_book(db, book_id):
            continue
        try:
            mi = db.get_metadata(book_id, index_is_id=True)
            ids = mi.get_identifiers() or {}
        except Exception:
            continue
        if str(ids.get('ao3series') or '') != str(series_id):
            continue
        record = load_book_as_member_record(db, book_id)
        record['calibre_book_id'] = book_id
        # series index
        try:
            record.setdefault('series', [])
            if not record['series']:
                record['series'] = [
                    {
                        'series_id': series_id,
                        'name': mi.series or '',
                        'position': float(mi.series_index or 0),
                    }
                ]
        except Exception:
            pass
        if not book_has_epub(db, book_id):
            skipped.append({'book_id': book_id, 'record': record, 'reason': 'no_epub'})
            continue
        ready.append({'book_id': book_id, 'record': record})
    records = [item['record'] for item in ready]
    ordered = sort_collection_members(records)
    by_id = {member_id_from_record(r): r for r in ordered}
    # rebuild ready in order
    ready_ordered = []
    seen = set()
    for r in ordered:
        mid = member_id_from_record(r)
        if mid in seen:
            continue
        seen.add(mid)
        for item in ready:
            if member_id_from_record(item['record']) == mid:
                ready_ordered.append(item)
                break
    return ready_ordered, skipped


def members_for_collection(
    db, collection: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ready: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    wanted = collection.strip().casefold()
    all_ids = list(getattr(db, 'all_ids', lambda: [])() or [])
    for book_id in all_ids:
        if is_omnibus_book(db, book_id):
            continue
        record = load_book_as_member_record(db, book_id)
        cols = [str(c).strip() for c in (record.get('current_collections') or [])]
        if not any(c.casefold() == wanted for c in cols):
            continue
        if not book_has_epub(db, book_id):
            skipped.append({'book_id': book_id, 'record': record, 'reason': 'no_epub'})
            continue
        ready.append({'book_id': book_id, 'record': record})
    records = sort_collection_members([i['record'] for i in ready])
    by_mid = {member_id_from_record(i['record']): i for i in ready}
    ordered = []
    for r in records:
        item = by_mid.get(member_id_from_record(r))
        if item:
            ordered.append(item)
    return ordered, skipped


def export_member_epubs(
    db, ready: list[dict[str, Any]], dest_dir: Path
) -> list[dict[str, Any]]:
    try:
        from calibre_plugins.fanfic_organizer.selected import copy_book_epub
    except ImportError:
        from selected import copy_book_epub

    dest_dir.mkdir(parents=True, exist_ok=True)
    exported = []
    for item in ready:
        book_id = int(item['book_id'])
        record = dict(item['record'])
        mid = member_id_from_record(record)
        dest = dest_dir / f'{mid}.epub'
        if copy_book_epub(db, book_id, dest):
            record['epub_file'] = str(dest)
            exported.append({'book_id': book_id, 'record': record, 'epub_path': dest})
    return exported


def write_combine_inputs(
    work: Path,
    exported: list[dict[str, Any]],
    *,
    kind: str,
    title: str,
    series_id: str = '',
    series_name: str = '',
    collection: str = '',
    auto_update: bool = False,
    include_prefaces: bool = False,
    omnibus_id: str = '',
    append_epub: Path | None = None,
    remove_book_ids: list[int] | None = None,
    existing_omnibus_book_id: int | None = None,
) -> dict[str, Any]:
    """Write manifest for the combine job worker."""
    work.mkdir(parents=True, exist_ok=True)
    records_path = work / 'members.jsonl'
    with records_path.open('w', encoding='utf-8') as fh:
        for item in exported:
            fh.write(json.dumps(item['record'], ensure_ascii=False) + '\n')
    manifest = {
        'kind': kind,
        'title': title,
        'series_id': series_id,
        'series_name': series_name,
        'collection': collection,
        'auto_update': auto_update,
        'include_prefaces': include_prefaces,
        'omnibus_id': omnibus_id or str(uuid.uuid4()),
        'append_epub': str(append_epub) if append_epub else '',
        'remove_book_ids': list(remove_book_ids or []),
        'existing_omnibus_book_id': existing_omnibus_book_id,
        'members': [
            {
                'book_id': item['book_id'],
                'member_id': member_id_from_record(item['record']),
                'epub_path': str(item['epub_path']),
                'title': item['record'].get('title') or '',
            }
            for item in exported
        ],
        'records_jsonl': str(records_path),
    }
    (work / 'combine.json').write_text(
        json.dumps(manifest, indent=2), encoding='utf-8'
    )
    return manifest


def schedule_collection_omnibus_updates(plugin, db, collection_names=None):
    """Enqueue sync jobs for managed collection omnibuses after membership writes.

    ``plugin`` is the InterfaceAction (needs ``jobs()``). Returns number of jobs started.
    """
    try:
        from calibre_plugins.fanfic_organizer.prefs import prefs as plugin_prefs
    except ImportError:
        return 0
    if not bool(plugin_prefs.get('omnibus_auto_update_collections', True)):
        return 0

    from calibre_plugins.fanfic_organizer.job_plans import plan_omnibus_sync
    from calibre_plugins.fanfic_organizer.selected import copy_book_epub
    import tempfile

    started = 0
    all_ids = list(getattr(db, 'all_ids', lambda: [])() or [])
    wanted = None
    if collection_names:
        wanted = {str(n).strip().casefold() for n in collection_names if str(n).strip()}

    for book_id in all_ids:
        if not is_omnibus_book(db, book_id):
            continue
        try:
            mi = db.get_metadata(book_id, index_is_id=True)
            ids = mi.get_identifiers() or {}
        except Exception:
            continue
        collection = str(ids.get('omnibuscollection') or '').strip()
        if not collection:
            continue
        if wanted is not None and collection.casefold() not in wanted:
            continue
        tmp = Path(tempfile.mkdtemp(prefix='omnibus-sync-'))
        epub = tmp / 'omnibus.epub'
        if not copy_book_epub(db, int(book_id), epub):
            continue
        try:
            meta = read_omnibus_meta(epub) or {}
        except ImportError:
            return started
        if not bool(meta.get('auto_update', True)):
            continue
        have = set(str(m) for m in (meta.get('member_ids') or []))
        ready, _skipped = members_for_collection(db, collection)
        add_items = []
        for item in ready:
            mid = member_id_from_record(item['record'])
            if mid not in have:
                add_items.append(item)
        remove_ids = []
        active_now = {member_id_from_record(i['record']) for i in ready}
        for mid in list(have):
            if mid not in active_now:
                remove_ids.append(mid)
        if not add_items and not remove_ids:
            continue
        job_dir = plugin.jobs().prepare_job_dir('omnibus')
        if job_dir is None:
            continue
        work = Path(job_dir) / 'work'
        work.mkdir(parents=True, exist_ok=True)
        existing = work / 'existing.epub'
        existing.write_bytes(epub.read_bytes())
        add_paths = []
        add_ids = []
        records_path = None
        if add_items:
            exported = export_member_epubs(db, add_items, work / 'members')
            add_paths = [str(e['epub_path']) for e in exported]
            add_ids = [member_id_from_record(e['record']) for e in exported]
            records_path = work / 'members.jsonl'
            with records_path.open('w', encoding='utf-8') as fh:
                for e in exported:
                    fh.write(json.dumps(e['record'], ensure_ascii=False) + '\n')
        plan_omnibus_sync(
            omnibus_epub=existing,
            job_dir=Path(job_dir),
            add_paths=add_paths or None,
            add_ids=add_ids or None,
            remove_ids=remove_ids or None,
            records_jsonl=records_path,
            omnibus_book_id=int(book_id),
            title=f'Update collection omnibus · {collection}',
        )
        if plugin.jobs().start_prepared(job_dir):
            started += 1
    return started
