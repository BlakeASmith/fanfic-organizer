# -*- coding: utf-8 -*-
"""Calibre-free helpers for extra tag mappings (``python -m ao3kit config mappings``).

Keep MATCH_CHOICES / ACTION_CHOICES in sync with ``ao3kit.tags.mappings``.
"""

from __future__ import annotations

import json
from typing import Any

# Labels for the plugin form (keep in sync with ``ao3kit.tags.mappings``).
MATCH_CHOICES = [
    ('mentions', 'contains'),
    ('is_ci', 'is exactly'),
]
ACTION_CHOICES = [
    ('collect', "Don't change it"),
    ('keep_separate', 'Keep this spelling'),
    ('map_to', 'Rename it'),
    ('drop', 'Remove it'),
]

_MATCH_LABELS = {
    **dict(MATCH_CHOICES),
    'tag': 'is exactly',
    'tag_ci': 'is exactly',
    'canonical': 'is exactly',
    'canonical_ci': 'is exactly',
    'contains': 'contains',
    'contains_ci': 'contains',
}
_ACTION_LABELS = dict(ACTION_CHOICES)


def build_mappings_list_argv() -> list[str]:
    return ['config', 'mappings', 'list']


def build_mappings_add_argv(
    *,
    match: str,
    values: str,
    action: str,
    map_to: str = '',
    collections: str = '',
    stop: bool = False,
    enabled: bool = True,
    mapping_id: str = '',
) -> list[str]:
    argv = [
        'config',
        'mappings',
        'add',
        '--match',
        match,
        '--values',
        values,
        '--action',
        action,
    ]
    if map_to.strip():
        argv.extend(['--map-to', map_to.strip()])
    for name in _split_csv(collections):
        argv.extend(['--collection', name])
    if stop:
        argv.append('--stop')
    if mapping_id.strip():
        argv.extend(['--id', mapping_id.strip()])
    if not enabled:
        argv.append('--disabled')
    return argv


def build_mappings_set_argv(
    mapping_id: str,
    *,
    match: str,
    values: str,
    action: str,
    map_to: str = '',
    collections: str = '',
    stop: bool = False,
    enabled: bool = True,
) -> list[str]:
    argv = [
        'config',
        'mappings',
        'set',
        mapping_id,
        '--match',
        match,
        '--values',
        values,
        '--action',
        action,
    ]
    if map_to.strip():
        argv.extend(['--map-to', map_to.strip()])
    for name in _split_csv(collections):
        argv.extend(['--collection', name])
    if stop:
        argv.append('--stop')
    if not enabled:
        argv.append('--disabled')
    return argv


def build_mappings_remove_argv(mapping_id: str) -> list[str]:
    return ['config', 'mappings', 'remove', mapping_id]


def build_mappings_move_argv(mapping_id: str, *, up: bool) -> list[str]:
    argv = ['config', 'mappings', 'move', mapping_id]
    argv.append('--up' if up else '--down')
    return argv


def build_mappings_toggle_argv(mapping_id: str) -> list[str]:
    return ['config', 'mappings', 'toggle', mapping_id]


def build_mappings_preview_argv(tag: str) -> list[str]:
    return ['config', 'mappings', 'preview', tag]


def parse_mappings_list(stdout: str) -> list[dict[str, Any]]:
    data = json.loads(stdout.strip() or '[]')
    if not isinstance(data, list):
        raise ValueError('mappings list is not a JSON array')
    rows = []
    for item in data:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def parse_preview(stdout: str) -> dict[str, Any]:
    data = json.loads(stdout.strip() or '{}')
    if not isinstance(data, dict):
        raise ValueError('preview is not a JSON object')
    return data


def format_when(row: dict[str, Any]) -> str:
    match = _MATCH_LABELS.get(str(row.get('match') or 'mentions'), 'contains')
    values = row.get('values') or []
    if isinstance(values, str):
        joined = values
    else:
        joined = ', '.join(str(item) for item in values)
    return f'{match} “{joined}”'


def format_then(row: dict[str, Any]) -> str:
    action = str(row.get('action') or '')
    if action == 'map_to':
        return f'Rename to {row.get("map_to") or ""}'
    return _ACTION_LABELS.get(action, action)


def format_rule_summary(row: dict[str, Any]) -> str:
    """One line for confirmations, e.g. contains “River Song” → River Song."""
    collections = row.get('collections') or []
    if isinstance(collections, str):
        coll = collections
    else:
        coll = ', '.join(str(item) for item in collections)
    when = format_when(row)
    then = format_then(row)
    if coll:
        return f'{when} · {then} → {coll}'
    return f'{when} · {then}'


def row_has_collection(row: dict[str, Any]) -> bool:
    if not row.get('enabled', True):
        return False
    collections = row.get('collections') or []
    if isinstance(collections, str):
        return bool(collections.strip())
    return any(str(item).strip() for item in collections)


def ui_match_kind(match: str) -> str:
    if match in {'mentions', 'contains', 'contains_ci'}:
        return 'mentions'
    return 'is_ci'


def format_preview(preview: dict[str, Any]) -> str:
    original = preview.get('original') or ''
    canonical = preview.get('canonical') or original
    dropped = bool(preview.get('dropped'))
    mapped = preview.get('mapped')
    lines = [f'This tag: {original}']
    if canonical and canonical != original:
        lines.append(f"AO3's usual name: {canonical}")
    if dropped:
        lines.append('After your rules: removed')
    elif mapped:
        if mapped == original:
            lines.append(f'After your rules: keep “{mapped}”')
        else:
            lines.append(f'After your rules: {mapped}')
    collections = preview.get('collections') or []
    if collections:
        lines.append(
            'Goes in collection: ' + ', '.join(str(item) for item in collections)
        )
    else:
        lines.append('Goes in collection: (none)')
    metatags = preview.get('metatags') or []
    if metatags:
        lines.append(
            'AO3 also adds to Fandom: ' + ', '.join(str(item) for item in metatags)
        )
    return '\n'.join(lines)


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in (value or '').split(',') if part.strip()]
