# -*- coding: utf-8 -*-
"""Build cleaned-metadata payloads for Calibre import."""

from __future__ import annotations

from typing import Any


def build_cleaned_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Derive cleaned metadata JSON for the AO3 Cleaned Metadata column.

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
        return [str(name) for name in collections.keys()]
    if isinstance(collections, list):
        return [str(name) for name in collections]
    return []


def _normalized_fandoms(record: dict[str, Any]) -> list[str]:
    fandoms = record.get('fandoms')
    if isinstance(fandoms, dict) and 'simplified' in fandoms:
        return [str(x) for x in (fandoms.get('simplified') or [])]
    if isinstance(fandoms, list):
        return [str(x) for x in fandoms]
    return []
