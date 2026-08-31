"""Shared multi-source work-record helpers.

JSONL remains the interchange format across AO3, Wikipedia, generic web, and
future sources. Records always carry ``work_id`` + ``url`` (required by the Calibre
importer). Optional ``source`` names the origin; missing means AO3 for
backward compatibility with existing libraries and JSONL files.
"""

from __future__ import annotations

from typing import Any

SOURCE_AO3 = "ao3"
SOURCE_WIKIPEDIA = "wikipedia"
SOURCE_WEB = "web"

KNOWN_SOURCES = frozenset({SOURCE_AO3, SOURCE_WIKIPEDIA, SOURCE_WEB})

PUBLISHERS = {
    SOURCE_AO3: "Archive of Our Own",
    SOURCE_WIKIPEDIA: "Wikipedia",
    SOURCE_WEB: "Web",
}

# Calibre identifier keys per source (besides shared ``url``).
ID_KEYS = {
    SOURCE_AO3: "ao3",
    SOURCE_WIKIPEDIA: "wikipedia",
    SOURCE_WEB: "web",
}


def normalize_source(value: Any) -> str:
    """Return a known source id; blank / unknown → AO3."""
    text = str(value or "").strip().casefold()
    if text in KNOWN_SOURCES:
        return text
    return SOURCE_AO3


def record_source(record: dict[str, Any] | None) -> str:
    if not isinstance(record, dict):
        return SOURCE_AO3
    return normalize_source(record.get("source"))


def publisher_for_source(source: str) -> str:
    return PUBLISHERS.get(normalize_source(source), PUBLISHERS[SOURCE_AO3])


def source_id_key(source: str) -> str:
    return ID_KEYS.get(normalize_source(source), ID_KEYS[SOURCE_AO3])
