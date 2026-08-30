"""External content sources (AO3, Wikipedia, …)."""

from __future__ import annotations

from ao3kit.sources.base import (
    ID_KEYS,
    KNOWN_SOURCES,
    PUBLISHERS,
    SOURCE_AO3,
    SOURCE_WIKIPEDIA,
    normalize_source,
    publisher_for_source,
    record_source,
    source_id_key,
)

__all__ = [
    "ID_KEYS",
    "KNOWN_SOURCES",
    "PUBLISHERS",
    "SOURCE_AO3",
    "SOURCE_WIKIPEDIA",
    "normalize_source",
    "publisher_for_source",
    "record_source",
    "source_id_key",
]
