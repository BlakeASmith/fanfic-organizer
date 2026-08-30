# -*- coding: utf-8 -*-
"""Calibre-free helpers for collection membership rules.

Keep FIELD/OP/MATCH/MODE choices in sync with ``ao3kit.tags.collections``.
"""

from __future__ import annotations

import json
from typing import Any

MATCH_CHOICES = [
    ('mentions', 'tag contains'),
    ('is_ci', 'tag is exactly'),
    ('fandom_mentions', 'fandom contains'),
    ('author_ci', 'author is'),
    ('work_id', 'this AO3 work'),
    ('calibre_uuid', 'this book'),
]
MODE_CHOICES = [
    ('include', 'Put matching books in'),
    ('exclude', 'Never put matching books in'),
]
FIELD_CHOICES = [
    ('tag', 'tag'),
    ('fandom', 'fandom'),
    ('relationship', 'relationship'),
    ('character', 'character'),
    ('author', 'author'),
    ('title', 'title'),
    ('summary', 'summary'),
    ('series', 'series'),
    ('words', 'word count'),
    ('complete', 'complete'),
    ('work_id', 'AO3 work id'),
    ('calibre_uuid', 'Calibre book UUID'),
]
OP_CHOICES = [
    ('contains', 'contains'),
    ('is', 'is exactly'),
    ('wildcard', 'matches wildcard'),
    ('regex', 'matches regex'),
    ('eq', '='),
    ('gt', '>'),
    ('gte', '≥'),
    ('lt', '<'),
    ('lte', '≤'),
]
TEXT_OPS = {'contains', 'is', 'wildcard', 'regex'}
NUMERIC_OPS = {'eq', 'gt', 'gte', 'lt', 'lte'}
TEXT_FIELDS = {
    'tag',
    'fandom',
    'relationship',
    'character',
    'author',
    'title',
    'summary',
    'series',
    'work_id',
    'calibre_uuid',
}
LEGACY_MATCH_TO_CONDITION = {
    'mentions': ('tag', 'contains'),
    'is_ci': ('tag', 'is'),
    'fandom_mentions': ('fandom', 'contains'),
    'author_ci': ('author', 'is'),
    'work_id': ('work_id', 'is'),
    'calibre_uuid': ('calibre_uuid', 'is'),
}

_MATCH_LABELS = dict(MATCH_CHOICES)
_FIELD_LABELS = dict(FIELD_CHOICES)
_OP_LABELS = dict(OP_CHOICES)


def ops_for_field(field_name: str) -> list[tuple[str, str]]:
    if field_name == 'words':
        return [(key, label) for key, label in OP_CHOICES if key in NUMERIC_OPS]
    if field_name == 'complete':
        return [('is', 'is')]
    return [(key, label) for key, label in OP_CHOICES if key in TEXT_OPS]


def condition_from_legacy(match: str, values: Any) -> dict[str, Any]:
    field_name, op = LEGACY_MATCH_TO_CONDITION.get(
        str(match or 'mentions'), ('tag', 'contains')
    )
    if isinstance(values, str):
        parsed = _split_csv(values)
    else:
        parsed = [str(item).strip() for item in (values or []) if str(item).strip()]
    return {
        'field': field_name,
        'op': op,
        'values': parsed,
        'casefold': True,
    }


def conditions_from_row(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    row = row or {}
    raw = row.get('all')
    if isinstance(raw, list) and raw:
        out: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            out.append(normalize_condition(item))
        if out:
            return out
    return [condition_from_legacy(str(row.get('match') or 'mentions'), row.get('values'))]


def normalize_condition(item: dict[str, Any]) -> dict[str, Any]:
    field_name = str(item.get('field') or 'tag').strip() or 'tag'
    op = str(item.get('op') or 'contains').strip() or 'contains'
    casefold = item.get('casefold', True)
    if isinstance(casefold, str):
        casefold = casefold.strip().casefold() not in {'0', 'false', 'no'}
    values: list[str] = []
    if 'values' in item and item.get('values') is not None:
        raw_values = item.get('values')
        if isinstance(raw_values, str):
            values = _split_csv(raw_values)
        else:
            values = [str(v).strip() for v in raw_values if str(v).strip()]
    elif item.get('value') is not None:
        raw = item.get('value')
        if isinstance(raw, bool):
            values = ['true' if raw else 'false']
        else:
            text = str(raw).strip()
            if text:
                values = [text]
    return {
        'field': field_name,
        'op': op,
        'values': values,
        'value': item.get('value'),
        'casefold': bool(casefold),
    }


def format_condition(item: dict[str, Any]) -> str:
    cond = normalize_condition(item)
    field_label = _FIELD_LABELS.get(cond['field'], cond['field'])
    op_label = _OP_LABELS.get(cond['op'], cond['op'])
    if cond['field'] == 'words':
        number = cond.get('value')
        if number is None and cond['values']:
            number = cond['values'][0]
        return f'{field_label} {op_label} {number}'
    if cond['field'] == 'complete':
        flag = None
        raw = cond.get('value')
        if isinstance(raw, bool):
            flag = raw
        elif cond['values']:
            flag = cond['values'][0].casefold() in {'1', 'true', 'yes', 'complete', 't'}
        return 'complete' if flag else 'incomplete'
    joined = ', '.join(cond['values'])
    suffix = '' if cond.get('casefold', True) else ' (case-sensitive)'
    return f'{field_label} {op_label} “{joined}”{suffix}'


def when_spec(item: dict[str, Any]) -> str:
    """Encode one condition as ``field:op:value`` for CLI ``--when``."""
    cond = normalize_condition(item)
    field_name = cond['field']
    op = cond['op']
    if field_name == 'words':
        number = cond.get('value')
        if number is None and cond['values']:
            number = cond['values'][0]
        return f'{field_name}:{op}:{number}'
    if field_name == 'complete':
        raw = cond.get('value')
        if isinstance(raw, bool):
            flag = raw
        elif cond['values']:
            flag = cond['values'][0].casefold() in {'1', 'true', 'yes', 'complete', 't'}
        else:
            flag = True
        return f'{field_name}:{op}:{"true" if flag else "false"}'
    value = ', '.join(cond['values'])
    return f'{field_name}:{op}:{value}'


def build_collections_list_argv() -> list[str]:
    return ['config', 'collections', 'list']


def build_collections_add_argv(
    *,
    match: str = 'mentions',
    values: str = '',
    collections: str,
    mode: str = 'include',
    pin: bool = False,
    enabled: bool = True,
    rule_id: str = '',
    description: str = '',
    conditions: list[dict[str, Any]] | None = None,
    case_sensitive: bool = False,
) -> list[str]:
    argv = ['config', 'collections', 'add', '--mode', mode]
    for name in _split_csv(collections):
        argv.extend(['--collection', name])
    argv.extend(_condition_argv(match, values, conditions, case_sensitive))
    if pin:
        argv.append('--pin')
    if description.strip():
        argv.extend(['--description', description.strip()])
    if rule_id.strip():
        argv.extend(['--id', rule_id.strip()])
    if not enabled:
        argv.append('--disabled')
    return argv


def build_collections_set_argv(
    rule_id: str,
    *,
    match: str = 'mentions',
    values: str = '',
    collections: str,
    mode: str = 'include',
    pin: bool = False,
    enabled: bool = True,
    description: str = '',
    conditions: list[dict[str, Any]] | None = None,
    case_sensitive: bool = False,
) -> list[str]:
    argv = ['config', 'collections', 'set', rule_id, '--mode', mode]
    for name in _split_csv(collections):
        argv.extend(['--collection', name])
    argv.extend(_condition_argv(match, values, conditions, case_sensitive))
    if pin:
        argv.append('--pin')
    if description.strip():
        argv.extend(['--description', description.strip()])
    if not enabled:
        argv.append('--disabled')
    return argv


def _condition_argv(
    match: str,
    values: str,
    conditions: list[dict[str, Any]] | None,
    case_sensitive: bool,
) -> list[str]:
    argv: list[str] = []
    if conditions:
        payload = [normalize_condition(item) for item in conditions]
        argv.extend(['--conditions-json', json.dumps(payload, ensure_ascii=False)])
        return argv
    argv.extend(['--match', match, '--values', values])
    if case_sensitive:
        argv.append('--case-sensitive')
    return argv


def build_collections_remove_argv(rule_id: str) -> list[str]:
    return ['config', 'collections', 'remove', rule_id]


def build_collections_move_argv(rule_id: str, *, up: bool) -> list[str]:
    argv = ['config', 'collections', 'move', rule_id]
    argv.append('--up' if up else '--down')
    return argv


def build_collections_toggle_argv(rule_id: str) -> list[str]:
    return ['config', 'collections', 'toggle', rule_id]


def build_collections_pin_argv(
    *,
    collection: str,
    work_id: str = '',
    uuid: str = '',
    description: str = '',
    exclude: bool = False,
) -> list[str]:
    argv = ['config', 'collections', 'pin', '--collection', collection]
    if work_id.strip():
        argv.extend(['--work-id', work_id.strip()])
    if uuid.strip():
        argv.extend(['--uuid', uuid.strip()])
    if description.strip():
        argv.extend(['--description', description.strip()])
    if exclude:
        argv.append('--exclude')
    return argv


def build_collections_unpin_argv(
    *,
    collection: str,
    work_id: str = '',
    uuid: str = '',
    exclude: bool = False,
    all_modes: bool = False,
) -> list[str]:
    argv = ['config', 'collections', 'unpin', '--collection', collection]
    if work_id.strip():
        argv.extend(['--work-id', work_id.strip()])
    if uuid.strip():
        argv.extend(['--uuid', uuid.strip()])
    if all_modes:
        argv.append('--all-modes')
    elif exclude:
        argv.append('--exclude')
    return argv


def parse_rules_list(stdout: str) -> list[dict[str, Any]]:
    data = json.loads(stdout.strip() or '[]')
    if not isinstance(data, list):
        raise ValueError('collection rules list is not a JSON array')
    return [item for item in data if isinstance(item, dict)]


def parse_explain(text: str) -> list[dict[str, Any]]:
    data = json.loads((text or '').strip() or '[]')
    if not isinstance(data, list):
        raise ValueError('collection explain is not a JSON array')
    return [item for item in data if isinstance(item, dict)]


def merge_collection_names(*groups: Any) -> list[str]:
    """Unique collection names, case-insensitive, sorted."""
    names: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if group is None:
            continue
        if isinstance(group, (str, bytes)):
            values = [group]
        else:
            values = group
        for value in values:
            name = str(value or '').strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    return sorted(names, key=lambda item: item.casefold())


def collection_names_from_rules(rows: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for row in rows:
        collections = row.get('collections') or []
        if isinstance(collections, str):
            names.append(collections)
        else:
            names.extend(str(item) for item in collections)
    return merge_collection_names(names)


def collection_names_from_explain(books: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for book in books:
        for item in book.get('memberships') or []:
            names.append(item.get('name'))
        names.extend(book.get('current') or [])
        names.extend(book.get('computed') or [])
    return merge_collection_names(names)


def empty_membership(name: str) -> dict[str, Any]:
    return {
        'name': name,
        'status': 'out',
        'computed': False,
        'current': False,
        'includes': [],
        'excludes': [],
        'include_pins': [],
        'exclude_pins': [],
        'shared_includes': [],
        'shared_excludes': [],
        'engine_sources': [],
    }


def flatten_explain_rows(
    books: list[dict[str, Any]],
    collection_filter: str = '',
) -> list[dict[str, Any]]:
    wanted = collection_filter.strip()
    wanted_key = wanted.casefold()
    rows: list[dict[str, Any]] = []
    for book in books:
        memberships = list(book.get('memberships') or [])
        if wanted:
            match = next(
                (
                    item
                    for item in memberships
                    if str(item.get('name') or '').strip().casefold() == wanted_key
                ),
                None,
            )
            memberships = [match if match is not None else empty_membership(wanted)]
        elif not memberships:
            memberships = [empty_membership('')]
        for item in memberships:
            row = dict(item)
            row['title'] = book.get('title') or ''
            row['work_id'] = book.get('work_id') or ''
            row['calibre_uuid'] = book.get('calibre_uuid') or ''
            row['book_id'] = book.get('book_id')
            rows.append(row)
    return rows


def format_membership_status(status: str) -> str:
    return {
        'in': 'In',
        'pending': 'Will be added',
        'excluded': 'Kept out',
        'unexplained': 'On the book, no rule',
        'out': 'Not in',
    }.get(str(status or ''), str(status or ''))


def format_membership_why(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for rule in item.get('includes') or []:
        if rule.get('pin'):
            parts.append('always this work')
        else:
            parts.append(format_when(rule))
    for rule in item.get('excludes') or []:
        label = format_when(rule)
        parts.append(f'never ({label})')
    for source in item.get('engine_sources') or []:
        text = str(source).strip()
        if text.startswith('rule:'):
            parts.append('collection rule')
        elif text:
            parts.append(f'tag mapping ({text})')
        else:
            parts.append('tag mapping')
    if item.get('status') == 'unexplained' and not parts:
        parts.append('on the book with no matching rule')
    if item.get('status') == 'out' and not parts:
        if str(item.get('name') or '').strip():
            parts.append('no matching rule')
        else:
            parts.append('not in any collection yet')
    return '; '.join(parts) or '—'


def format_when(row: dict[str, Any]) -> str:
    if row.get('when'):
        return str(row.get('when'))
    if row.get('pin'):
        values = row.get('values') or []
        joined = values if isinstance(values, str) else ', '.join(str(item) for item in values)
        desc = str(row.get('description') or joined)
        return f'always this work ({desc})'
    conditions = conditions_from_row(row)
    if conditions:
        return ' AND '.join(format_condition(item) for item in conditions)
    match = _MATCH_LABELS.get(str(row.get('match') or 'mentions'), 'tag contains')
    values = row.get('values') or []
    joined = values if isinstance(values, str) else ', '.join(str(item) for item in values)
    return f'{match} “{joined}”'


def format_collection(row: dict[str, Any]) -> str:
    collections = row.get('collections') or []
    if isinstance(collections, str):
        return collections
    return ', '.join(str(item) for item in collections)


def format_kind(row: dict[str, Any]) -> str:
    if row.get('mode') == 'exclude':
        return 'Never'
    if row.get('pin'):
        return 'Always this work'
    return 'Rule'


def format_rule_summary(row: dict[str, Any]) -> str:
    return f'{format_when(row)} → {format_collection(row)}'


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in (value or '').split(',') if part.strip()]
