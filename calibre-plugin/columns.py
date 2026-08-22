# -*- coding: utf-8 -*-
"""Calibre custom column setup for a new fanfic library.

Column labels/names match the FanFicFare layout used in the existing fanfic
library so a *new* Calibre library can look the same. This module never
opens or writes a library on its own — callers decide when to create columns
(import into the current library, or an explicit settings checkbox).
"""

from __future__ import annotations

from typing import Any

# Older plugin versions stored full JSON blobs here. Never created now;
# still read as a fallback when simplifying books imported before this change.
LEGACY_RAW_METADATA_LABEL = 'ao3_raw_metadata'

COMMENTS_DATATYPE = 'comments'

# Replica of the fanfic library's FanFicFare / browsing columns, plus Original
# Tags (pre-clean AO3 tags). Cleaned tags go in Calibre's standard Tags field.
# Count Pages readability columns are not created here.
LAYOUT_COLUMN_SPECS: tuple[dict[str, Any], ...] = (
    {
        'role': 'fandom',
        'label': 'fandom',
        'lookup': '#fandom',
        'name': 'Fandom',
        'datatype': 'text',
        'is_multiple': True,
        'display': {'is_names': False, 'description': ''},
    },
    {
        'role': 'relationships',
        'label': 'relationships',
        'lookup': '#relationships',
        'name': 'Relationships',
        'datatype': 'text',
        'is_multiple': True,
        'display': {'is_names': False, 'description': ''},
    },
    {
        'role': 'collections',
        'label': 'collections',
        'lookup': '#collections',
        'name': 'Collections',
        'datatype': 'text',
        'is_multiple': True,
        'display': {'is_names': False, 'description': ''},
    },
    {
        'role': 'originaltags',
        'label': 'originaltags',
        'lookup': '#originaltags',
        'name': 'Original Tags',
        'datatype': 'text',
        'is_multiple': True,
        'display': {'is_names': False, 'description': ''},
    },
    {
        'role': 'wordcount',
        'label': 'wordcount',
        'lookup': '#wordcount',
        'name': 'word count',
        'datatype': 'int',
        'is_multiple': False,
        'display': {'number_format': None, 'description': ''},
    },
)

LAYOUT_COLUMNS = tuple(
    (spec['role'], spec['label'], spec['lookup']) for spec in LAYOUT_COLUMN_SPECS
)


def custom_label_is_live(db, label: str) -> bool:
    """True if Calibre's open library can actually read/write this custom column.

    ``create_custom_column`` inserts the SQL row immediately, but
    ``backend.custom_column_label_map`` (used by ``set_custom``) is only
    rebuilt when the library is reopened. Do not treat a SQL row as writable.
    """
    label = str(label or '').lstrip('#')
    if not label:
        return False
    backend = getattr(db, 'backend', None)
    if backend is None:
        backend = getattr(getattr(db, 'new_api', None), 'backend', None)
    cmap = getattr(backend, 'custom_column_label_map', None)
    if isinstance(cmap, dict):
        return label in cmap
    lookup = f'#{label}'
    try:
        db.field_metadata[lookup]
        return True
    except Exception:
        return False


def column_exists(db, lookup: str, label: str | None = None) -> bool:
    """True if the column is live on the currently open library."""
    if label is None and lookup.startswith('#'):
        label = lookup[1:]
    if label and custom_label_is_live(db, label):
        return True
    try:
        db.field_metadata[lookup]
        return True
    except (KeyError, AttributeError, TypeError):
        pass
    return False


def ensure_custom_column(
    db,
    *,
    label: str,
    name: str,
    lookup: str,
    datatype: str = COMMENTS_DATATYPE,
    is_multiple: bool = False,
    display: dict[str, Any] | None = None,
) -> str:
    """Create a custom column if it is not live on the open library."""
    if custom_label_is_live(db, label):
        return label

    try:
        db.create_custom_column(
            label,
            name,
            datatype,
            is_multiple=is_multiple,
            editable=True,
            display=dict(display or {}),
        )
    except Exception as exc:
        # Concurrent create / stale metadata: column already there.
        message = str(exc).lower()
        if (
            'unique constraint' in message
            or 'already exists' in message
            or type(exc).__name__ == 'ConstraintError'
        ):
            return label
        raise
    return label


def ensure_layout_columns(db) -> list[str]:
    """Create Fandom / Relationships / Collections / word count if missing.

    Returns labels that are not live in the open DB. Calibre only registers
    newly created columns after the library is reopened.
    """
    pending: list[str] = []
    for spec in LAYOUT_COLUMN_SPECS:
        label = spec['label']
        if custom_label_is_live(db, label):
            continue
        pending.append(label)
        ensure_custom_column(
            db,
            label=label,
            name=spec['name'],
            lookup=spec['lookup'],
            datatype=spec['datatype'],
            is_multiple=spec['is_multiple'],
            display=spec['display'],
        )
    return pending


def reopen_current_library(gui):
    """Reopen the current library so newly created custom columns become live."""
    db = gui.current_db
    path = getattr(db, 'library_path', None)
    moved = getattr(gui, 'library_moved', None)
    if path and callable(moved):
        moved(path)
    return gui.current_db


def apply_layout_columns(gui):
    """Create missing layout columns and reopen the library if needed."""
    db = gui.current_db
    pending = ensure_layout_columns(db)
    if not pending:
        return db
    return reopen_current_library(gui)


def layout_columns_present(db) -> dict[str, bool]:
    """Which fanfic-layout columns are writable on this open library."""
    present: dict[str, bool] = {}
    for role, label, _lookup in LAYOUT_COLUMNS:
        present[role] = custom_label_is_live(db, label)
    return present
