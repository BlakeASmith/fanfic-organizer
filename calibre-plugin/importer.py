# -*- coding: utf-8 -*-
"""Import AO3 JSONL records into a Calibre library."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from calibre.ebooks.metadata import MetaInformation

from calibre_plugins.fanfic_organizer.cleaned import (
    calibre_fields_for_record,
    existing_book_id_from_identifiers,
    tags_for_calibre_library,
)
from calibre_plugins.fanfic_organizer.columns import (
    custom_label_is_live,
    layout_columns_present,
)
from calibre_plugins.fanfic_organizer.jsonl_loader import resolve_epub_path


def build_metadata(
    record: dict[str, Any],
    *,
    layout: dict[str, bool] | None = None,
    existing_identifiers: dict[str, Any] | None = None,
    existing_comments: str | None = None,
) -> MetaInformation:
    fields = calibre_fields_for_record(record)
    work_id = fields['work_id']
    title = record.get('title') or f'AO3 work {work_id or "?"}'
    author = record.get('author')
    authors = [author] if author else ['Unknown']

    mi = MetaInformation(title, authors)
    identifiers = dict(existing_identifiers or {})
    identifiers.update(fields['identifiers'])
    mi.set_identifiers(identifiers)

    if fields.get('series'):
        mi.series = fields['series']
        index = fields.get('series_index')
        mi.series_index = float(index) if index is not None else 1.0

    present = layout or {}
    mi.tags = tags_for_calibre_library(
        record,
        has_fandom_column=bool(present.get('fandom')),
        has_relationships_column=bool(present.get('relationships')),
        has_collections_column=bool(present.get('collections')),
    )

    # Leave Comments alone (AO3 summaries live there; scrape records have no summary).
    if existing_comments:
        mi.comments = existing_comments
    return mi


def iter_identifier_maps(db) -> list[tuple[int, dict[str, Any]]]:
    """In-memory identifier dicts for every book (no Calibre search)."""
    api = getattr(db, 'new_api', None)
    if api is not None:
        all_book_ids = getattr(api, 'all_book_ids', None)
        all_field_for = getattr(api, 'all_field_for', None)
        if callable(all_book_ids) and callable(all_field_for):
            try:
                mapping = all_field_for('identifiers', all_book_ids()) or {}
            except Exception:
                mapping = None
            if mapping is not None:
                rows: list[tuple[int, dict[str, Any]]] = []
                for book_id, ids in mapping.items():
                    rows.append((int(book_id), dict(ids or {})))
                return rows
    rows = []
    all_ids = getattr(db, 'all_ids', None)
    if not callable(all_ids):
        return rows
    getter = getattr(db, 'get_identifiers', None)
    for book_id in all_ids():
        ids: dict[str, Any] = {}
        if callable(getter):
            try:
                ids = getter(book_id, index_is_id=True) or {}
            except TypeError:
                ids = getter(book_id) or {}
        rows.append((int(book_id), dict(ids)))
    return rows


def find_existing_book(
    db,
    record: dict[str, Any],
    *,
    catalog: list[tuple[int, dict[str, Any]]] | None = None,
) -> int | None:
    """Find a library book for this AO3 work using in-memory identifiers.

    Calibre ``search_getting_ids('identifiers:…')`` walks every book for each
    query and is too slow on the GUI thread during live import. Identifier
    maps are already in memory; matching them is the same as ``book_matches_work``.
    """
    rows = catalog if catalog is not None else iter_identifier_maps(db)
    found = existing_book_id_from_identifiers(rows, record)
    return int(found) if found is not None else None


def find_book_by_work_id(db, work_id: str) -> int | None:
    return find_existing_book(db, {'work_id': work_id})


def set_cover_from_epub(db, book_id: int, epub_path: Path) -> bool:
    """Copy the EPUB's marked cover image onto the Calibre book."""
    data = None
    try:
        from calibre.ebooks.metadata.meta import get_metadata

        with Path(epub_path).open('rb') as handle:
            mi = get_metadata(handle, stream_type='epub')
        cover = getattr(mi, 'cover_data', None)
        if cover and len(cover) >= 2 and cover[1]:
            data = cover[1]
    except Exception:
        data = None
    if not data:
        return False
    return set_book_cover(db, book_id, data)


def set_book_cover(db, book_id: int, data: bytes) -> bool:
    """Write the Calibre library cover (cover.jpg) from image bytes."""
    if not data:
        return False
    bid = int(book_id)
    try:
        api = getattr(db, 'new_api', None)
        setter = getattr(api, 'set_cover', None) if api is not None else None
        if callable(setter):
            setter({bid: data})
        else:
            db.set_cover(bid, data)
        return True
    except Exception:
        try:
            db.set_cover(bid, data)
            return True
        except Exception:
            return False


def add_epub_format(
    db,
    book_id: int,
    epub_path: Path,
    *,
    replace: bool = False,
    apply_cover: bool | None = None,
) -> bool:
    """Attach an EPUB. Returns False when the book already has that format."""
    if not replace:
        try:
            if db.has_format(book_id, 'EPUB', index_is_id=True):
                return False
        except Exception:
            pass
    db.add_format(book_id, 'EPUB', str(epub_path), index_is_id=True)
    should_cover = apply_cover
    if should_cover is None:
        try:
            from calibre_plugins.fanfic_organizer.prefs import prefs as plugin_prefs

            should_cover = bool(plugin_prefs.get('set_calibre_cover', True))
        except Exception:
            should_cover = True
    if should_cover:
        set_cover_from_epub(db, book_id, epub_path)
    return True


def attach_downloaded_epubs(
    db,
    items: list[dict[str, Any]],
    *,
    bundle_root: str | Path | None,
) -> list[dict[str, Any]]:
    """Add downloaded EPUB files onto existing books. Does not rewrite metadata."""
    outcomes: list[dict[str, Any]] = []
    for item in items:
        book_id = item['book_id']
        record = item.get('record') or {}
        title = record.get('title') or item.get('title')
        if bundle_root is None:
            outcomes.append(
                {
                    'book_id': book_id,
                    'title': title,
                    'action': 'failed',
                    'reason': 'no download folder',
                    'epub': False,
                }
            )
            continue
        epub_path = resolve_epub_path(record, bundle_root)
        if epub_path is None:
            outcomes.append(
                {
                    'book_id': book_id,
                    'title': title,
                    'action': 'failed',
                    'reason': record.get('epub_error') or 'no EPUB file',
                    'epub': False,
                }
            )
            continue
        attached = add_epub_format(db, book_id, epub_path)
        outcomes.append(
            {
                'book_id': book_id,
                'title': title,
                'action': 'added' if attached else 'skipped',
                'epub': attached,
            }
        )
    return outcomes


def set_book_tags(db, book_id: int, tags: list[str]) -> None:
    """Replace the standard Tags field used by Calibre's tag browser."""
    cleaned = [str(tag).strip() for tag in tags if str(tag).strip()]
    api = getattr(db, 'new_api', None)
    if api is not None and hasattr(api, 'set_field'):
        api.set_field('tags', {int(book_id): cleaned})
        return
    setter = getattr(db, 'set_tags', None)
    if setter is not None:
        setter(book_id, cleaned, append=False)
        commit = getattr(db, 'commit', None)
        if callable(commit):
            commit()
        return
    mi = db.get_metadata(book_id, index_is_id=True)
    mi.tags = cleaned
    try:
        db.set_metadata(book_id, mi, force_changes=True)
    except TypeError:
        db.set_metadata(book_id, mi)


def _set_custom(db, book_id: int, label: str, value: Any, *, commit: bool) -> bool:
    """Write a custom column if it is live. Skip rather than KeyError."""
    label = str(label).lstrip('#')
    if not custom_label_is_live(db, label):
        return False
    lookup = f'#{label}'
    api = getattr(db, 'new_api', None)
    if api is not None and hasattr(api, 'set_field'):
        api.set_field(lookup, {int(book_id): value})
        return True
    db.set_custom(book_id, value, label=label, commit=commit)
    return True


def write_layout_fields(db, book_id: int, record: dict[str, Any]) -> None:
    """Write FanFicFare-style columns when they exist. Skip missing columns."""
    fields = calibre_fields_for_record(record)
    present = layout_columns_present(db)
    cleaned = record.get('cleaned') if isinstance(record.get('cleaned'), dict) else {}

    if present.get('fandom') and (
        fields['fandoms'] or isinstance(cleaned.get('fandoms'), list)
    ):
        _set_custom(db, book_id, 'fandom', fields['fandoms'], commit=False)
    if present.get('relationships') and (
        fields['relationships'] or isinstance(cleaned.get('relationships'), list)
    ):
        _set_custom(
            db, book_id, 'relationships', fields['relationships'], commit=False
        )
    if present.get('collections') and fields['collections']:
        _set_custom(
            db, book_id, 'collections', fields['collections'], commit=False
        )
    if present.get('wordcount') and fields['wordcount'] is not None:
        _set_custom(db, book_id, 'wordcount', fields['wordcount'], commit=False)
    if present.get('originaltags') and fields['original_tags']:
        _set_custom(
            db, book_id, 'originaltags', fields['original_tags'], commit=False
        )

    commit = getattr(db, 'commit', None)
    if callable(commit):
        commit()


def write_collections_field(db, book_id: int, names: list[str]) -> bool:
    """Replace the Collections column when it exists. Tags are left unchanged."""
    present = layout_columns_present(db)
    if not present.get('collections'):
        return False
    wrote = _set_custom(db, book_id, 'collections', list(names), commit=False)
    commit = getattr(db, 'commit', None)
    if callable(commit):
        commit()
    return wrote


def refresh_library_ui(
    gui,
    book_ids: list[int] | None = None,
    *,
    added_count: int = 0,
) -> None:
    """Refresh the book list, tag browser, and book details after library writes.

    ``added_count`` is how many new rows were just prepended on the library
    view. Calibre's ``BooksModel.books_added`` notifies Qt of those inserts and
    emits ``count_changed_signal``, which already rebuilds the tag browser.
    Callers that only updated existing books should leave it at 0 so we still
    ``recount()`` after tag/field changes.
    """
    model = gui.library_view.model()
    current_row = -1
    try:
        index = gui.library_view.currentIndex()
        if index.isValid():
            current_row = index.row()
    except Exception:
        current_row = -1

    inserted = False
    added = int(added_count or 0)
    if added > 0:
        inserter = getattr(model, 'books_added', None)
        if callable(inserter):
            try:
                inserter(added)
                inserted = True
            except Exception:
                inserted = False

    if book_ids and hasattr(model, 'refresh_ids'):
        try:
            model.refresh_ids(list(book_ids), current_row=current_row)
        except TypeError:
            try:
                model.refresh_ids(list(book_ids))
            except Exception:
                if not inserted:
                    model.refresh()
        except Exception:
            if not inserted:
                model.refresh()
    elif not inserted:
        model.refresh()

    if inserted:
        return
    tags_view = getattr(gui, 'tags_view', None)
    if tags_view is not None and hasattr(tags_view, 'recount'):
        tags_view.recount()


def import_record(
    db,
    record: dict[str, Any],
    *,
    update_existing: bool = True,
    bundle_root: str | Path | None = None,
    skip_existing_epub: bool = False,
    catalog: list[tuple[int, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    layout = layout_columns_present(db)
    existing_id = find_existing_book(db, record, catalog=catalog)

    if existing_id is not None and not update_existing:
        attached = False
        if bundle_root is not None:
            already_has_epub = False
            if skip_existing_epub:
                try:
                    already_has_epub = bool(
                        db.has_format(existing_id, 'EPUB', index_is_id=True)
                    )
                except Exception:
                    already_has_epub = False
            if not already_has_epub:
                epub_path = resolve_epub_path(record, bundle_root)
                if epub_path is not None:
                    try:
                        attached = add_epub_format(db, existing_id, epub_path)
                    except Exception:
                        attached = False
        return {
            'book_id': existing_id,
            'action': 'skipped',
            'title': record.get('title'),
            'epub': attached,
        }

    existing_identifiers = None
    existing_comments = None
    if existing_id is not None:
        try:
            existing = db.get_metadata(existing_id, index_is_id=True)
            existing_identifiers = existing.get_identifiers()
            existing_comments = existing.comments
        except Exception:
            pass

    mi = build_metadata(
        record,
        layout=layout,
        existing_identifiers=existing_identifiers,
        existing_comments=existing_comments,
    )

    if existing_id is None:
        book_id = db.create_book_entry(mi, add_duplicates=True)
        action = 'added'
        if catalog is not None:
            identifiers = {}
            getter = getattr(mi, 'get_identifiers', None)
            if callable(getter):
                identifiers = getter() or {}
            catalog.append((int(book_id), dict(identifiers)))
    else:
        book_id = existing_id
        try:
            db.set_metadata(book_id, mi, force_changes=True)
        except TypeError:
            db.set_metadata(book_id, mi)
        action = 'updated'

    write_layout_fields(db, book_id, record)
    set_book_tags(db, book_id, mi.tags)

    attached = False
    if bundle_root is not None:
        already_has_epub = False
        if skip_existing_epub:
            try:
                already_has_epub = bool(
                    db.has_format(book_id, 'EPUB', index_is_id=True)
                )
            except Exception:
                already_has_epub = False
        if not already_has_epub:
            epub_path = resolve_epub_path(record, bundle_root)
            if epub_path is not None:
                try:
                    attached = add_epub_format(db, book_id, epub_path)
                except Exception:
                    attached = False

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
    skip_existing_epub: bool = False,
) -> list[dict[str, Any]]:
    catalog = iter_identifier_maps(db)
    return [
        import_record(
            db,
            record,
            update_existing=update_existing,
            bundle_root=bundle_root,
            skip_existing_epub=skip_existing_epub,
            catalog=catalog,
        )
        for record in records
    ]
