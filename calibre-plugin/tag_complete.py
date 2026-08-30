# -*- coding: utf-8 -*-
"""Tag-name autocomplete for plugin text fields (Calibre-free helpers).

Suggestions come from the local AO3 tag-cache SQLite file (and optional extra
names from the open library). No AO3 ``/autocomplete/`` requests — robots.txt
disallows that path.

Keep ranking / SQL in sync with ``ao3kit.tags.suggest``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

DEFAULT_LIMIT = 25
FETCH_CAP = 400
_ATTR = '_fanfic_tag_completer'
HINT = 'Start typing for tag-name suggestions from the local tag cache.'


def escape_like(text: str) -> str:
    return (
        str(text)
        .replace('\\', '\\\\')
        .replace('%', '\\%')
        .replace('_', '\\_')
    )


def category_values(category: str | None) -> tuple[str, ...] | None:
    if category is None:
        return None
    key = str(category).strip().casefold()
    if not key:
        return None
    if key in {'freeform', 'additional tags'}:
        return ('freeform', 'additional tags')
    return (key,)


def _boundary_contains(folded: str, query: str) -> bool:
    start = 0
    while True:
        pos = folded.find(query, start)
        if pos < 0:
            return False
        if pos == 0 or not folded[pos - 1].isalnum():
            return True
        start = pos + 1


def rank_tuple(name: str, query: str, *, canonical: bool = False) -> tuple:
    folded = name.casefold()
    q = query.casefold().strip()
    if folded == q:
        bucket = 0
    elif folded.startswith(q):
        bucket = 1
    elif _boundary_contains(folded, q):
        bucket = 2 if (
            f' {q}' in f' {folded}' or f'/{q}' in folded or f'({q}' in folded
        ) else 3
    else:
        bucket = 4
    return (bucket, 0 if canonical else 1, len(name), folded)


def name_matches_query(name: str, query: str) -> bool:
    q = str(query or '').strip()
    if not q:
        return False
    return rank_tuple(name, q)[0] < 4


def current_csv_token(text: str, cursor: int | None = None) -> tuple[int, int, str]:
    raw = text or ''
    if cursor is None:
        cursor = len(raw)
    cursor = max(0, min(int(cursor), len(raw)))
    start = raw.rfind(',', 0, cursor) + 1
    end = raw.find(',', cursor)
    if end < 0:
        end = len(raw)
    token = raw[start:end]
    lead = len(token) - len(token.lstrip())
    trail = len(token) - len(token.rstrip())
    token_start = start + lead
    token_end = end - trail
    if token_end < token_start:
        token_end = token_start
    return token_start, token_end, raw[token_start:token_end]


def replace_csv_token(
    text: str, replacement: str, cursor: int | None = None
) -> tuple[str, int]:
    start, end, _token = current_csv_token(text, cursor)
    raw = text or ''
    inserted = str(replacement or '')
    new = raw[:start] + inserted + raw[end:]
    return new, start + len(inserted)


def merge_and_rank(
    cache_rows: Sequence[tuple[str, str]],
    extra: Iterable[str] | None,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    q = str(query or '').strip()
    if not q:
        return []
    cap = max(1, int(limit))
    items: list[tuple[tuple, str]] = []
    seen: set[str] = set()
    for name, status in cache_rows:
        text = str(name or '').strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        rank = rank_tuple(text, q, canonical=str(status) == 'canonical')
        if rank[0] >= 4:
            continue
        seen.add(key)
        items.append((rank, text))
    for name in extra or []:
        text = str(name or '').strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        rank = rank_tuple(text, q, canonical=False)
        if rank[0] >= 4:
            continue
        seen.add(key)
        items.append((rank, text))
    items.sort(key=lambda row: row[0])
    return [name for _rank, name in items[:cap]]


def suggest_from_connection(
    conn: sqlite3.Connection,
    query: str,
    *,
    category: str | None = None,
    extra: Iterable[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    fetch: int = FETCH_CAP,
) -> list[str]:
    q = str(query or '').strip()
    if not q:
        return []
    pattern = f'%{escape_like(q)}%'
    wanted = category_values(category)
    fetch_n = max(int(limit), int(fetch))
    sql = """
        SELECT name, status
        FROM entries
        WHERE name LIKE ? ESCAPE '\\'
    """
    params: list[Any] = [pattern]
    if wanted:
        placeholders = ','.join('?' * len(wanted))
        sql += f" AND LOWER(IFNULL(category, '')) IN ({placeholders})"
        params.extend(wanted)
    sql += ' LIMIT ?'
    params.append(fetch_n)
    try:
        rows = [
            (str(row[0]), str(row[1]))
            for row in conn.execute(sql, params).fetchall()
        ]
    except sqlite3.Error:
        rows = []
    return merge_and_rank(rows, extra, q, limit=limit)


def suggest_from_sqlite_path(
    path: Path | str | None,
    query: str,
    *,
    category: str | None = None,
    extra: Iterable[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    fetch: int = FETCH_CAP,
) -> list[str]:
    if path is None:
        return merge_and_rank([], extra, query, limit=limit)
    cache_path = Path(path)
    if not cache_path.is_file():
        return merge_and_rank([], extra, query, limit=limit)
    try:
        conn = sqlite3.connect(
            f'file:{cache_path.resolve()}?mode=ro', uri=True, timeout=5
        )
    except sqlite3.Error:
        return merge_and_rank([], extra, query, limit=limit)
    try:
        return suggest_from_connection(
            conn,
            query,
            category=category,
            extra=extra,
            limit=limit,
            fetch=fetch,
        )
    finally:
        conn.close()


def tag_cache_path() -> Path:
    env = os.environ.get('AO3KIT_TAG_CACHE', '').strip()
    if env:
        return Path(env).expanduser().resolve()
    try:
        from ao3kit.paths import tag_cache_file

        return tag_cache_file()
    except ImportError:
        pass
    try:
        from calibre_plugins.fanfic_organizer.user_dirs import resolve_tag_cache_file

        return resolve_tag_cache_file()
    except ImportError:
        pass
    cache_home = os.environ.get('XDG_CACHE_HOME', '').strip()
    if cache_home:
        root = Path(cache_home)
    else:
        root = Path.home() / '.cache'
    return root / 'fanfic-organizer' / 'ao3_tag_cache.sqlite'


def suggest_tag_names(
    query: str,
    *,
    cache_path: Path | str | None = None,
    category: str | None = None,
    extra: Iterable[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    """Plugin-side suggest (same ranking as ``ao3kit.tags.suggest``)."""
    return suggest_from_sqlite_path(
        cache_path if cache_path is not None else tag_cache_path(),
        query,
        category=category,
        extra=extra,
        limit=limit,
    )


def category_for_collection_match(match: str):
    """Cache category for a collection-rule match kind, or False to disable."""
    kind = str(match or '')
    if kind in {'work_id', 'calibre_uuid'}:
        return False
    if kind == 'fandom_mentions':
        return 'Fandom'
    if kind == 'author_ci':
        return 'Author'
    return None


def category_for_collection_field(field_name: str):
    """Cache category for a collection condition field, or False to disable."""
    kind = str(field_name or '')
    if kind in {
        'work_id',
        'calibre_uuid',
        'words',
        'complete',
        'title',
        'summary',
        'series',
    }:
        return False
    if kind == 'fandom':
        return 'Fandom'
    if kind == 'author':
        return 'Author'
    if kind == 'relationship':
        return 'Relationship'
    if kind == 'character':
        return 'Character'
    return None


def extras_for_collection_field(
    field_name: str, vocab: dict[str, list[str]] | None = None
) -> list[str]:
    kind = str(field_name or '')
    vocab = vocab or {}
    if kind == 'author':
        return list(vocab.get('authors') or [])
    if kind == 'fandom':
        return list(vocab.get('fandoms') or [])
    if kind == 'relationship':
        return list(vocab.get('relationships') or [])
    if kind in {'tag', 'character'}:
        return extras_for_collection_match('mentions', vocab)
    return []


def attach_collection_field_completer(values_edit, field_combo, parent=None):
    """Wire autocomplete from a collection condition field combo."""
    vocab = library_vocab(find_calibre_db(parent or values_edit))

    def extras_for(field_name: str) -> list[str]:
        return extras_for_collection_field(field_name, vocab)

    field_name = str(field_combo.currentData() or 'tag')
    attach_tag_completer(
        values_edit,
        category=category_for_collection_field(field_name),
        extra=extras_for(field_name),
        csv=True,
    )

    def _sync(*_args) -> None:
        kind = str(field_combo.currentData() or 'tag')
        set_completer_category(values_edit, category_for_collection_field(kind))
        set_completer_extra(values_edit, extras_for(kind))

    field_combo.currentIndexChanged.connect(_sync)
    return getattr(values_edit, _ATTR, None)


def _id_map_names(db, lookup: str) -> list[str]:
    api = getattr(db, 'new_api', db)
    getter = getattr(api, 'get_id_map', None)
    if not callable(getter):
        return []
    try:
        mapping = getter(lookup)
    except Exception:
        return []
    if not isinstance(mapping, dict):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for value in mapping.values():
        text = str(value or '').strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        names.append(text)
    return names


def library_vocab(db) -> dict[str, list[str]]:
    """Unique Tags / Fandom / Relationships / authors from the open library."""
    if db is None:
        return {}
    return {
        'tags': _id_map_names(db, 'tags'),
        'fandoms': _id_map_names(db, '#fandom'),
        'relationships': _id_map_names(db, '#relationships'),
        'authors': _id_map_names(db, 'authors'),
    }


def extra_from_warm_names() -> list[str]:
    try:
        from ao3kit.paths import warm_names_file

        path = warm_names_file()
    except ImportError:
        try:
            from calibre_plugins.fanfic_organizer.user_dirs import (
                resolve_warm_names_file,
            )

            path = resolve_warm_names_file()
        except ImportError:
            return []
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = line.strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        names.append(text)
    return names


def find_calibre_db(widget):
    obj = widget
    seen: set[int] = set()
    while obj is not None and id(obj) not in seen:
        seen.add(id(obj))
        db = getattr(obj, 'current_db', None)
        if db is not None:
            return db
        gui = getattr(obj, 'gui', None)
        if gui is not None:
            db = getattr(gui, 'current_db', None)
            if db is not None:
                return db
        parent = getattr(obj, 'parent', None)
        obj = parent() if callable(parent) else parent
    return None


def extras_for_collection_match(
    match: str, vocab: dict[str, list[str]] | None = None
) -> list[str]:
    kind = str(match or '')
    vocab = vocab or {}
    if kind == 'author_ci':
        return list(vocab.get('authors') or [])
    names: list[str] = []
    seen: set[str] = set()

    def _add(items: Iterable[str]) -> None:
        for item in items:
            text = str(item or '').strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            names.append(text)

    if kind == 'fandom_mentions':
        _add(vocab.get('fandoms') or [])
        return names
    _add(extra_from_warm_names())
    _add(vocab.get('tags') or [])
    _add(vocab.get('fandoms') or [])
    _add(vocab.get('relationships') or [])
    return names


def combined_tag_extras(widget=None, db=None) -> list[str]:
    vocab = library_vocab(db if db is not None else find_calibre_db(widget))
    return extras_for_collection_match('mentions', vocab)


def _qt():
    try:
        from PyQt5.Qt import QCompleter, QStringListModel, Qt

        return QCompleter, QStringListModel, Qt
    except ImportError:
        from PyQt5.QtCore import Qt
        from PyQt5.QtCore import QStringListModel
        from PyQt5.QtWidgets import QCompleter

        return QCompleter, QStringListModel, Qt


class TagNameCompleter:
    """Popup suggestions for a ``QLineEdit``, optional CSV token replacement."""

    def __init__(
        self,
        widget,
        *,
        category=None,
        extra: Iterable[str] | None = None,
        csv: bool = False,
        cache_path: Path | str | None = None,
        limit: int = DEFAULT_LIMIT,
    ):
        QCompleter, QStringListModel, Qt = _qt()
        self.widget = widget
        self.category = category
        self.extra = [str(item) for item in extra or [] if str(item).strip()]
        self.csv = bool(csv)
        self.cache_path = cache_path
        self.limit = max(1, int(limit))
        self.model = QStringListModel()
        self.completer = QCompleter(self.model, widget)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        try:
            self.completer.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        except AttributeError:
            self.completer.setCompletionMode(
                QCompleter.CompletionMode.UnfilteredPopupCompletion
            )
        self.completer.setMaxVisibleItems(12)
        self.completer.setWidget(widget)
        try:
            self.completer.activated[str].connect(self._activated)
        except Exception:
            self.completer.activated.connect(self._activated)
        widget.textEdited.connect(self._text_edited)
        setattr(widget, _ATTR, self)
        tip = widget.toolTip() or ''
        if HINT not in tip:
            widget.setToolTip((tip + '\n' if tip else '') + HINT)

    @property
    def enabled(self) -> bool:
        return self.category is not False

    def _text_edited(self, text: str) -> None:
        if not self.enabled:
            popup = self.completer.popup()
            if popup is not None:
                popup.hide()
            return
        cursor = self.widget.cursorPosition()
        if self.csv:
            _start, _end, token = current_csv_token(text, cursor)
        else:
            token = text
        token = token.strip()
        if not token:
            popup = self.completer.popup()
            if popup is not None:
                popup.hide()
            return
        category = self.category if isinstance(self.category, str) else None
        names = suggest_tag_names(
            token,
            cache_path=self.cache_path,
            category=category,
            extra=self.extra,
            limit=self.limit,
        )
        self.model.setStringList(names)
        if names:
            self.completer.complete()
        else:
            popup = self.completer.popup()
            if popup is not None:
                popup.hide()

    def _activated(self, completion) -> None:
        name = str(completion or '').strip()
        if not name:
            return
        if self.csv:
            text = self.widget.text()
            cursor = self.widget.cursorPosition()
            new, pos = replace_csv_token(text, name, cursor)
            self.widget.setText(new)
            self.widget.setCursorPosition(pos)
        else:
            self.widget.setText(name)
            self.widget.setCursorPosition(len(name))
        popup = self.completer.popup()
        if popup is not None:
            popup.hide()


def attach_tag_completer(
    widget,
    *,
    category=None,
    extra: Iterable[str] | None = None,
    csv: bool = False,
    cache_path: Path | str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> TagNameCompleter | None:
    """Attach (or replace) tag-name autocomplete on a ``QLineEdit``."""
    if widget is None:
        return None
    existing = getattr(widget, _ATTR, None)
    if existing is not None:
        existing.category = category
        existing.extra = [str(item) for item in extra or [] if str(item).strip()]
        existing.csv = bool(csv)
        if cache_path is not None:
            existing.cache_path = cache_path
        existing.limit = max(1, int(limit))
        return existing
    try:
        return TagNameCompleter(
            widget,
            category=category,
            extra=extra,
            csv=csv,
            cache_path=cache_path,
            limit=limit,
        )
    except Exception:
        return None


def set_completer_category(widget, category) -> None:
    ctl = getattr(widget, _ATTR, None)
    if ctl is None:
        return
    ctl.category = category
    if category is False:
        popup = ctl.completer.popup()
        if popup is not None:
            popup.hide()


def set_completer_extra(widget, extra: Iterable[str] | None) -> None:
    ctl = getattr(widget, _ATTR, None)
    if ctl is None:
        return
    ctl.extra = [str(item) for item in extra or [] if str(item).strip()]


def attach_collection_match_completer(values_edit, match_combo, parent=None):
    """Wire a collection-rule match field: tags / fandoms / authors by kind."""
    vocab = library_vocab(find_calibre_db(parent or values_edit))

    def extras_for(match: str) -> list[str]:
        return extras_for_collection_match(match, vocab)

    match = str(match_combo.currentData() or 'mentions')
    attach_tag_completer(
        values_edit,
        category=category_for_collection_match(match),
        extra=extras_for(match),
        csv=True,
    )

    def _sync(*_args) -> None:
        kind = str(match_combo.currentData() or 'mentions')
        set_completer_category(values_edit, category_for_collection_match(kind))
        set_completer_extra(values_edit, extras_for(kind))

    match_combo.currentIndexChanged.connect(_sync)
    return getattr(values_edit, _ATTR, None)
