# -*- coding: utf-8 -*-
"""Find tags that appear on few books and remove them from Calibre Tags.

Counts and removals use the standard Tags field only (not Fandom,
Relationships, Collections, or Original Tags).
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

# Whole-string / token similarity floor. Short tokens skip this and use
# equality, substring, or prefix instead so "an" does not match everything.
FUZZY_THRESHOLD = 0.78
_SPLIT = re.compile(r"[\s/,&|._\-–—()\[\]'\":;]+")


def parse_name_list(text: str | None) -> tuple[str, ...]:
    """Split a comma-separated filter field into stripped names."""
    if not text:
        return ()
    return tuple(part.strip() for part in str(text).split(',') if part.strip())


def field_values(value: Any) -> tuple[str, ...]:
    """Normalize a Calibre tags / names field into a tuple of strings."""
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


def _fold(text: str) -> str:
    return str(text).casefold().strip()


def _tokens(text: str) -> list[str]:
    return [part for part in _SPLIT.split(_fold(text)) if part]


def _token_matches(query: str, value: str, *, threshold: float) -> bool:
    if query == value:
        return True
    if query in value or value in query:
        return True
    if len(query) >= 2 and value.startswith(query):
        return True
    if len(query) < 3:
        return False
    return difflib.SequenceMatcher(None, query, value).ratio() >= threshold


def fuzzy_name_match(
    value: str,
    query: str,
    *,
    threshold: float = FUZZY_THRESHOLD,
) -> bool:
    """True if ``query`` is a fuzzy / substring / token match for ``value``."""
    q = _fold(query)
    v = _fold(value)
    if not q:
        return True
    if not v:
        return False
    if q == v or q in v or v in q:
        return True
    q_tokens = _tokens(query)
    v_tokens = _tokens(value)
    if q_tokens and v_tokens and all(
        any(_token_matches(qt, vt, threshold=threshold) for vt in v_tokens)
        for qt in q_tokens
    ):
        return True
    if len(q) < 3:
        return False
    return difflib.SequenceMatcher(None, q, v).ratio() >= threshold


def _any_fuzzy(haystack: Iterable[str], needles: Iterable[str]) -> bool:
    values = [str(item) for item in haystack if str(item).strip()]
    for needle in needles:
        text = str(needle).strip()
        if not text:
            continue
        if any(fuzzy_name_match(value, text) for value in values):
            return True
    return False


@dataclass(frozen=True)
class TagPurgeFilters:
    """Book-set filters. Comma-separated values in one field are OR (fuzzy).

    Filled fields are AND'd together. Empty fields are ignored. Names match
    by case-insensitive substring, token overlap, or close spelling.
    """

    fandoms: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (
            self.fandoms
            or self.tags
            or self.collections
            or self.authors
            or self.relationships
        )


@dataclass(frozen=True)
class BookTagSnapshot:
    book_id: int
    tags: tuple[str, ...]
    fandoms: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()
    title: str = ''


def snapshot_matches(book: BookTagSnapshot, filters: TagPurgeFilters) -> bool:
    if filters.fandoms and not _any_fuzzy(book.fandoms, filters.fandoms):
        return False
    if filters.tags and not _any_fuzzy(book.tags, filters.tags):
        return False
    if filters.collections and not _any_fuzzy(book.collections, filters.collections):
        return False
    if filters.authors and not _any_fuzzy(book.authors, filters.authors):
        return False
    if filters.relationships and not _any_fuzzy(
        book.relationships, filters.relationships
    ):
        return False
    return True


def matching_books(
    books: Iterable[BookTagSnapshot],
    filters: TagPurgeFilters | None = None,
) -> list[BookTagSnapshot]:
    items = list(books)
    if filters is None or filters.is_empty():
        return items
    return [book for book in items if snapshot_matches(book, filters)]


def count_tags(books: Iterable[BookTagSnapshot]) -> dict[str, int]:
    """Count distinct books that have each Tags-column value."""
    counts: Counter[str] = Counter()
    for book in books:
        seen: set[str] = set()
        for tag in book.tags:
            name = str(tag).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            counts[name] += 1
    return dict(counts)


def tags_on_books(books: Iterable[BookTagSnapshot]) -> set[str]:
    names: set[str] = set()
    for book in books:
        for tag in book.tags:
            name = str(tag).strip()
            if name:
                names.add(name)
    return names


def rare_tags(counts: dict[str, int], max_works: int) -> list[tuple[str, int]]:
    """Tags whose book count is between 1 and ``max_works``, A–Z."""
    limit = int(max_works)
    if limit < 1:
        return []
    items = [
        (name, n) for name, n in counts.items() if 1 <= int(n) <= limit
    ]
    items.sort(key=lambda item: (item[0].casefold(), item[0], item[1]))
    return items


def plan_tag_purge(
    library: Iterable[BookTagSnapshot],
    *,
    max_works: int,
    source: Iterable[BookTagSnapshot] | None = None,
    filters: TagPurgeFilters | None = None,
) -> list[tuple[str, int]]:
    """List rare tags using **library-wide** counts.

    ``source`` (after ``filters``) only chooses which tag *names* appear.
    Frequency is always counted across ``library``.
    """
    library_books = list(library)
    counts = count_tags(library_books)
    if source is not None or (filters is not None and not filters.is_empty()):
        seed = matching_books(
            library_books if source is None else source, filters
        )
        allowed = tags_on_books(seed)
        counts = {name: n for name, n in counts.items() if name in allowed}
    return rare_tags(counts, max_works)


def filter_tags_by_name(
    planned: Iterable[tuple[str, int]],
    query: str | None,
) -> list[tuple[str, int]]:
    """Keep tags whose names fuzzy-match any comma-separated query part."""
    needles = parse_name_list(query)
    items = list(planned)
    if not needles:
        return items
    return [
        (name, count)
        for name, count in items
        if any(fuzzy_name_match(name, needle) for needle in needles)
    ]


def remaining_tags(tags: Iterable[str], purge_names: Iterable[str]) -> list[str]:
    drop = {str(name) for name in purge_names if str(name)}
    return [str(tag) for tag in tags if str(tag) and str(tag) not in drop]


def purge_updates(
    books: Iterable[BookTagSnapshot],
    purge_names: Iterable[str],
) -> list[tuple[int, list[str]]]:
    """``(book_id, remaining_tags)`` for every library book that would change."""
    drop = {str(name) for name in purge_names if str(name)}
    if not drop:
        return []
    updates: list[tuple[int, list[str]]] = []
    for book in books:
        kept = remaining_tags(book.tags, drop)
        if kept != list(book.tags):
            updates.append((book.book_id, kept))
    return updates


def _try_all_field_for(api, lookup: str, ids: Iterable[int]) -> dict[int, Any]:
    getter = getattr(api, 'all_field_for', None)
    if getter is None:
        return {}
    try:
        return getter(lookup, ids) or {}
    except Exception:
        return {}


def resolve_scope_ids(
    db,
    search: str = '',
    *,
    use_virtual_library: bool = True,
) -> list[int]:
    """Book ids in the current library, optionally narrowed by Calibre search."""
    query = (search or '').strip()
    search_fn = getattr(db, 'search_getting_ids', None)
    if callable(search_fn):
        try:
            ids = search_fn(
                query, None, use_virtual_library=use_virtual_library
            )
        except TypeError:
            ids = search_fn(query, None)
        return [int(book_id) for book_id in (ids or [])]

    api = getattr(db, 'new_api', None)
    if api is not None:
        if query and hasattr(api, 'search'):
            return [int(book_id) for book_id in (api.search(query) or [])]
        all_ids = getattr(api, 'all_book_ids', None)
        if callable(all_ids):
            return [int(book_id) for book_id in (all_ids() or [])]

    all_ids = getattr(db, 'all_ids', None)
    if callable(all_ids):
        return [int(book_id) for book_id in (all_ids() or [])]
    return []


def scope_book_ids(
    db,
    search: str = '',
    *,
    selected_ids: Iterable[int] | None = None,
    selected_only: bool = False,
    use_virtual_library: bool = True,
) -> list[int]:
    """Library (or virtual library) ids, optionally limited to a selection."""
    selected = [int(book_id) for book_id in (selected_ids or [])]
    if selected_only and selected:
        query = (search or '').strip()
        if not query:
            return selected
        found = set(
            resolve_scope_ids(
                db, query, use_virtual_library=use_virtual_library
            )
        )
        return [book_id for book_id in selected if book_id in found]
    return resolve_scope_ids(
        db, search, use_virtual_library=use_virtual_library
    )


def _as_int_ids(values: Iterable[Any] | None) -> list[int]:
    ids: list[int] = []
    for value in values or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def selected_ids_from_view(view) -> list[int]:
    """Highlighted book ids from a Calibre book view."""
    if view is None:
        return []
    getter = getattr(view, 'get_selected_ids', None)
    if callable(getter):
        try:
            ids = _as_int_ids(getter())
            if ids:
                return ids
        except Exception:
            pass
    model = None
    model_fn = getattr(view, 'model', None)
    if callable(model_fn):
        try:
            model = model_fn()
        except Exception:
            model = None
    id_fn = getattr(model, 'id', None) if model is not None else None
    sm_fn = getattr(view, 'selectionModel', None)
    sm = sm_fn() if callable(sm_fn) else None
    if sm is not None and callable(id_fn):
        try:
            rows = sm.selectedRows()
        except Exception:
            rows = []
        ids = []
        for index in rows or []:
            try:
                row = index.row() if hasattr(index, 'row') else int(index)
                ids.append(int(id_fn(row)))
            except Exception:
                continue
        if ids:
            return ids
    current_fn = getattr(view, 'currentIndex', None)
    if callable(current_fn) and callable(id_fn):
        try:
            index = current_fn()
            row = index.row()
            if row >= 0:
                return [int(id_fn(row))]
        except Exception:
            pass
    return []


def selected_ids_from_gui(gui) -> list[int]:
    """Highlighted ids from the current view, then the library view."""
    views = []
    current = getattr(gui, 'current_view', None)
    if callable(current):
        try:
            views.append(current())
        except Exception:
            pass
    library_view = getattr(gui, 'library_view', None)
    if library_view is not None and library_view not in views:
        views.append(library_view)
    for view in views:
        ids = selected_ids_from_view(view)
        if ids:
            return ids
    return []


def shown_ids_from_gui(gui) -> list[int]:
    """Book ids currently displayed in the library view (search / tag browser)."""
    view = getattr(gui, 'library_view', None)
    model_fn = getattr(view, 'model', None) if view is not None else None
    model = None
    if callable(model_fn):
        try:
            model = model_fn()
        except Exception:
            model = None
    if model is None:
        return []
    id_fn = getattr(model, 'id', None)
    if not callable(id_fn):
        return []
    n = 0
    count = getattr(model, 'count', None)
    if callable(count):
        try:
            n = int(count())
        except TypeError:
            n = 0
    if n <= 0:
        row_count = getattr(model, 'rowCount', None)
        if callable(row_count):
            try:
                n = int(row_count())
            except TypeError:
                try:
                    n = int(row_count(None))
                except Exception:
                    n = 0
    ids: list[int] = []
    for row in range(max(0, n)):
        try:
            ids.append(int(id_fn(row)))
        except Exception:
            continue
    return ids


def library_book_count(gui) -> int:
    db = getattr(gui, 'current_db', None)
    if db is None:
        return 0
    api = getattr(db, 'new_api', None)
    all_ids = getattr(api, 'all_book_ids', None) if api is not None else None
    if callable(all_ids):
        try:
            return len(all_ids())
        except Exception:
            pass
    all_ids = getattr(db, 'all_ids', None)
    if callable(all_ids):
        try:
            return len(all_ids())
        except Exception:
            pass
    return 0


def initial_scope_ids(
    *,
    selected: Iterable[int] | None,
    shown: Iterable[int] | None,
    library_count: int,
) -> tuple[list[int], str]:
    """Pick the opening book set: multi-select, else filtered view, else highlight.

    Calibre usually has exactly one highlighted row even when a search or the
    tag browser is showing a subset. In that case use the shown subset.
    """
    selected_ids = _as_int_ids(selected)
    shown_ids = _as_int_ids(shown)
    if len(selected_ids) > 1:
        return selected_ids, 'selected'
    shown_is_subset = (
        bool(shown_ids)
        and int(library_count or 0) > 0
        and len(shown_ids) < int(library_count)
    )
    if shown_is_subset:
        return shown_ids, 'shown'
    if selected_ids:
        return selected_ids, 'selected'
    if shown_ids:
        return shown_ids, 'shown'
    return [], 'library'


def graph_scope_ids(
    *,
    selected: Iterable[int] | None,
    library_ids: Iterable[int],
) -> tuple[list[int], str]:
    """Use a multi-selection; otherwise the whole library.

    Calibre almost always has one highlighted row, so a single selection
    still means "graph the library." Two or more selected books means
    graph only those.
    """
    selected_ids = _as_int_ids(selected)
    if len(selected_ids) > 1:
        return selected_ids, 'selected'
    return _as_int_ids(library_ids), 'library'


def _snapshot_from_maps(
    book_id: int,
    *,
    tags_map: dict[int, Any],
    fandom_map: dict[int, Any],
    collections_map: dict[int, Any],
    authors_map: dict[int, Any],
    relationships_map: dict[int, Any],
    title_map: dict[int, Any],
) -> BookTagSnapshot:
    title = title_map.get(book_id)
    if title is None:
        title = ''
    else:
        title = str(title)
    return BookTagSnapshot(
        book_id=int(book_id),
        tags=field_values(tags_map.get(book_id)),
        fandoms=field_values(fandom_map.get(book_id)),
        collections=field_values(collections_map.get(book_id)),
        authors=field_values(authors_map.get(book_id)),
        relationships=field_values(relationships_map.get(book_id)),
        title=title,
    )


def _load_via_new_api(api, book_ids: list[int]) -> list[BookTagSnapshot]:
    tags_map = _try_all_field_for(api, 'tags', book_ids)
    fandom_map = _try_all_field_for(api, '#fandom', book_ids)
    collections_map = _try_all_field_for(api, '#collections', book_ids)
    authors_map = _try_all_field_for(api, 'authors', book_ids)
    relationships_map = _try_all_field_for(api, '#relationships', book_ids)
    title_map = _try_all_field_for(api, 'title', book_ids)
    return [
        _snapshot_from_maps(
            book_id,
            tags_map=tags_map,
            fandom_map=fandom_map,
            collections_map=collections_map,
            authors_map=authors_map,
            relationships_map=relationships_map,
            title_map=title_map,
        )
        for book_id in book_ids
    ]


def _custom_value(db, book_id: int, label: str) -> Any:
    getter = getattr(db, 'get_custom', None)
    if not callable(getter):
        return None
    try:
        return getter(book_id, label=label, index_is_id=True)
    except Exception:
        return None


def _load_via_old_api(db, book_ids: list[int]) -> list[BookTagSnapshot]:
    snapshots: list[BookTagSnapshot] = []
    for book_id in book_ids:
        tags: tuple[str, ...] = ()
        authors: tuple[str, ...] = ()
        title = ''
        try:
            mi = db.get_metadata(book_id, index_is_id=True)
            tags = field_values(getattr(mi, 'tags', None))
            authors = field_values(getattr(mi, 'authors', None))
            title = str(getattr(mi, 'title', '') or '')
        except Exception:
            pass
        snapshots.append(
            BookTagSnapshot(
                book_id=int(book_id),
                tags=tags,
                fandoms=field_values(_custom_value(db, book_id, 'fandom')),
                collections=field_values(
                    _custom_value(db, book_id, 'collections')
                ),
                authors=authors,
                relationships=field_values(
                    _custom_value(db, book_id, 'relationships')
                ),
                title=title,
            )
        )
    return snapshots


def load_snapshots(
    db,
    book_ids: Iterable[int] | None = None,
) -> list[BookTagSnapshot]:
    """Read Tags plus layout columns for the given (or all) books."""
    if book_ids is None:
        ids = resolve_scope_ids(db, '', use_virtual_library=True)
    else:
        ids = [int(book_id) for book_id in book_ids]
    api = getattr(db, 'new_api', None)
    if api is not None and hasattr(api, 'all_field_for'):
        return _load_via_new_api(api, ids)
    return _load_via_old_api(db, ids)
