# -*- coding: utf-8 -*-
"""Calibre custom column setup for AO3 metadata."""

from __future__ import annotations

RAW_METADATA_LABEL = 'ao3_raw_metadata'
RAW_METADATA_LOOKUP = '#ao3_raw_metadata'
RAW_METADATA_NAME = 'AO3 Raw Metadata'

CLEANED_METADATA_LABEL = 'ao3_cleaned_metadata'
CLEANED_METADATA_LOOKUP = '#ao3_cleaned_metadata'
CLEANED_METADATA_NAME = 'AO3 Cleaned Metadata'

COMMENTS_DATATYPE = 'comments'


def column_exists(db, lookup: str, label: str | None = None) -> bool:
    """True if a custom column is already present.

    Checks field_metadata by lookup key and by label. Label matters when the
    column exists in the DB but the in-memory metadata map is stale.
    """
    try:
        db.field_metadata[lookup]
        return True
    except (KeyError, AttributeError, TypeError):
        pass

    if label is None and lookup.startswith('#'):
        label = lookup[1:]

    try:
        custom = db.field_metadata.custom_field_metadata()
    except Exception:
        custom = {}

    for key, meta in (custom or {}).items():
        if key == lookup:
            return True
        if label and meta.get('label') == label:
            return True

    # Last resort: backend table (library may have the row already).
    try:
        backend = getattr(db, 'backend', None) or getattr(
            getattr(db, 'new_api', None), 'backend', None
        )
        if backend is not None:
            rows = backend.execute(
                'SELECT label FROM custom_columns WHERE label = ?',
                (label or lookup.lstrip('#'),),
            ).fetchall()
            if rows:
                return True
    except Exception:
        pass

    return False


def ensure_custom_column(
    db,
    *,
    label: str,
    name: str,
    lookup: str,
    datatype: str = COMMENTS_DATATYPE,
) -> str:
    """Create a comments custom column if missing. Returns the column label."""
    if column_exists(db, lookup, label=label):
        return label

    try:
        db.create_custom_column(
            label,
            name,
            datatype,
            is_multiple=False,
            editable=True,
            display={},
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


def ensure_raw_metadata_column(db) -> str:
    return ensure_custom_column(
        db,
        label=RAW_METADATA_LABEL,
        name=RAW_METADATA_NAME,
        lookup=RAW_METADATA_LOOKUP,
    )


def ensure_cleaned_metadata_column(db) -> str:
    return ensure_custom_column(
        db,
        label=CLEANED_METADATA_LABEL,
        name=CLEANED_METADATA_NAME,
        lookup=CLEANED_METADATA_LOOKUP,
    )


def ensure_plugin_columns(db) -> dict[str, str]:
    """Ensure raw + cleaned metadata columns exist. Returns label map."""
    return {
        'raw': ensure_raw_metadata_column(db),
        'cleaned': ensure_cleaned_metadata_column(db),
    }
