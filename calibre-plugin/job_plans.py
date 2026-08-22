# -*- coding: utf-8 -*-
"""Build ao3kit job specs for plugin operations (Calibre-free)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from calibre_plugins.ao3_scraper.jobs import write_json
    from calibre_plugins.ao3_scraper.scrape_run import (
        build_collections_argv,
        build_enrich_argv,
        describe_scrape,
        prepare_download_command,
        prepare_fill_series_command,
        prepare_scrape_command,
        prepare_series_from_command,
        write_records_jsonl,
    )
except ImportError:  # pytest loads this file without the Calibre package
    from jobs import write_json
    from scrape_run import (
        build_collections_argv,
        build_enrich_argv,
        describe_scrape,
        prepare_download_command,
        prepare_fill_series_command,
        prepare_scrape_command,
        prepare_series_from_command,
        write_records_jsonl,
    )


def _write_spec(job_dir: Path, spec: dict[str, Any]) -> dict[str, Any]:
    spec = dict(spec)
    spec['id'] = job_dir.name
    write_json(job_dir / 'spec.json', spec)
    return spec


def plan_scrape(options: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    work = job_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    argv, jsonl = prepare_scrape_command(options, work)
    steps = [argv]
    results = jsonl
    out = jsonl
    if options.get('simplify_tags'):
        cleaned = work / 'cleaned.jsonl'
        steps.append(build_enrich_argv(str(jsonl), str(cleaned), options))
        out = cleaned
    bundle = jsonl.parent if options.get('download_epubs') else work
    return _write_spec(
        job_dir,
        {
            'title': describe_scrape(options).split('\n', 1)[0][:80],
            'kind': 'scrape',
            'steps': steps,
            'plugin': {
                'action': 'import_records',
                'update_existing': bool(options.get('update_existing', True)),
                'jsonl': str(out),
                'results_jsonl': str(results),
                'bundle_root': str(bundle),
            },
        },
    )


def plan_import(
    records: list[dict[str, Any]],
    job_dir: Path,
    *,
    options: dict[str, Any],
    bundle_root: str | Path,
    cleanup_dir: str | None = None,
) -> dict[str, Any]:
    work = job_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    current = work / 'input.jsonl'
    write_records_jsonl(current, records)
    steps: list[list[str]] = []
    dest = Path(bundle_root)
    if options.get('include_series'):
        argv, jsonl, dest = prepare_series_from_command(records, work, options)
        steps.append(argv)
        current = jsonl
    if options.get('simplify_tags'):
        cleaned = work / 'cleaned.jsonl'
        steps.append(build_enrich_argv(str(current), str(cleaned), options))
        current = cleaned
    plugin = {
        'action': 'import_records',
        'update_existing': bool(options.get('update_existing', True)),
        'jsonl': str(current),
        'bundle_root': str(dest),
    }
    if cleanup_dir:
        plugin['cleanup_dir'] = cleanup_dir
    title = 'Import JSONL'
    if options.get('include_series'):
        title = 'Import JSONL (with series)'
    return _write_spec(
        job_dir,
        {'title': title, 'kind': 'import', 'steps': steps, 'plugin': plugin},
    )


def plan_download_selected(
    ready: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    job_dir: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    work = job_dir / 'work'
    records = [item['record'] for item in ready]
    argv, jsonl, dest = prepare_download_command(records, work, options)
    items_path = work / 'items.json'
    write_json(
        items_path,
        {
            'ready': ready,
            'skipped': skipped,
        },
    )
    n = len(ready)
    noun = 'book' if n == 1 else 'books'
    return _write_spec(
        job_dir,
        {
            'title': f'Download EPUBs for {n} {noun}',
            'kind': 'download',
            'steps': [argv],
            'plugin': {
                'action': 'attach_epubs',
                'jsonl': str(jsonl),
                'bundle_root': str(dest),
                'items_json': str(items_path),
                'incremental_epubs': True,
            },
        },
    )


def plan_simplify_selected(
    ready: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    job_dir: Path,
    options: dict[str, Any],
    *,
    collections_only: bool = False,
) -> dict[str, Any]:
    work = job_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    records = [item['record'] for item in ready]
    inp = work / 'in.jsonl'
    out = work / 'out.jsonl'
    write_records_jsonl(inp, records)
    if collections_only:
        argv = build_collections_argv(str(inp), str(out), options)
        title = f'Recompute collections ({len(ready)})'
        kind = 'collections'
        action = 'apply_collections'
    else:
        argv = build_enrich_argv(str(inp), str(out), options)
        title = f'Simplify tags ({len(ready)})'
        kind = 'enrich'
        action = 'apply_cleaned'
    items_path = work / 'items.json'
    write_json(items_path, {'ready': ready, 'skipped': skipped})
    return _write_spec(
        job_dir,
        {
            'title': title,
            'kind': kind,
            'steps': [argv],
            'plugin': {
                'action': action,
                'jsonl': str(out),
                'items_json': str(items_path),
            },
        },
    )


def plan_import_series(
    records: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    job_dir: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    work = job_dir / 'work'
    argv, jsonl, dest = prepare_series_from_command(records, work, options)
    steps = [argv]
    out = jsonl
    if options.get('simplify_tags'):
        cleaned = work / 'cleaned.jsonl'
        steps.append(build_enrich_argv(str(jsonl), str(cleaned), options))
        out = cleaned
    n = len(records)
    noun = 'book' if n == 1 else 'books'
    return _write_spec(
        job_dir,
        {
            'title': f'Import series for {n} {noun}',
            'kind': 'series',
            'steps': steps,
            'plugin': {
                'action': 'import_records',
                'update_existing': bool(options.get('update_existing', True)),
                'skip_existing_epub': True,
                'jsonl': str(out),
                'bundle_root': str(dest) if options.get('download_epubs') else '',
                'skipped': skipped,
            },
        },
    )


def plan_fill_series(
    ready: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    job_dir: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    work = job_dir / 'work'
    records = [item['record'] for item in ready]
    argv, jsonl = prepare_fill_series_command(records, work, options)
    items_path = work / 'items.json'
    write_json(items_path, {'ready': ready, 'skipped': skipped})
    n = len(ready)
    noun = 'book' if n == 1 else 'books'
    return _write_spec(
        job_dir,
        {
            'title': f'Fill series for {n} {noun}',
            'kind': 'fill_series',
            'steps': [argv],
            'plugin': {
                'action': 'apply_series',
                'jsonl': str(jsonl),
                'items_json': str(items_path),
            },
        },
    )


def merge_ready_with_jsonl(
    ready: list[dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    work_id_of,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        work_id = work_id_of(record)
        if work_id:
            by_id[str(work_id)] = record
    merged: list[dict[str, Any]] = []
    for item in ready:
        record = item.get('record') or {}
        work_id = work_id_of(record)
        updated = dict(item)
        if work_id and str(work_id) in by_id:
            updated['record'] = by_id[str(work_id)]
        merged.append(updated)
    return merged
