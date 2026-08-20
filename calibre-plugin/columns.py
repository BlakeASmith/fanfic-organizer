# -*- coding: utf-8 -*-
"""Calibre custom column setup for AO3 raw metadata."""

from __future__ import annotations

RAW_METADATA_LABEL = 'ao3_raw_metadata'
RAW_METADATA_LOOKUP = '#ao3_raw_metadata'
RAW_METADATA_NAME = 'AO3 Raw Metadata'
RAW_METADATA_DATATYPE = 'comments'


def column_exists(db) -> bool:
    try:
        db.field_metadata[RAW_METADATA_LOOKUP]
        return True
    except (KeyError, AttributeError):
        return False


def ensure_raw_metadata_column(db) -> str:
    """Create the raw metadata column if missing. Returns the column label."""
    if column_exists(db):
        return RAW_METADATA_LABEL

    db.create_custom_column(
        RAW_METADATA_LABEL,
        RAW_METADATA_NAME,
        RAW_METADATA_DATATYPE,
        is_multiple=False,
        editable=True,
        display={},
    )
    return RAW_METADATA_LABEL
