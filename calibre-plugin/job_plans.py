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
        build_cover_argv,
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
        build_cover_argv,
        build_enrich_argv,
        describe_scrape,
        prepare_download_command,
        prepare_fill_series_command,
        prepare_scrape_command,
        prepare_series_from_command,
        write_records_jsonl,
    )


def _jsonl_result(
    path: str | Path, *, label: str = 'work', field: str = ''
) -> dict[str, str]:
    spec = {'source': 'jsonl_count', 'path': str(path), 'label': label}
    if field:
        spec['field'] = field
    return spec


def _import_plugin(
    *,
    jsonl: str | Path,
    bundle_root: str | Path | None,
    update_existing: bool,
    skip_existing_epub: bool = True,
    results_jsonl: str | Path | None = None,
    cleanup_dir: str | None = None,
    skipped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    plugin: dict[str, Any] = {
        'action': 'import_records',
        'update_existing': bool(update_existing),
        'skip_existing_epub': bool(skip_existing_epub),
        'jsonl': str(jsonl),
        'incremental_import': True,
    }
    if bundle_root:
        plugin['bundle_root'] = str(bundle_root)
    if results_jsonl:
        plugin['results_jsonl'] = str(results_jsonl)
    if cleanup_dir:
        plugin['cleanup_dir'] = cleanup_dir
    if skipped:
        plugin['skipped'] = skipped
    return plugin


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
            'plugin': _import_plugin(
                jsonl=out,
                bundle_root=bundle,
                update_existing=bool(options.get('update_existing', True)),
                results_jsonl=results,
            ),
            'result': _jsonl_result(out),
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
    results = current
    if options.get('include_series'):
        argv, jsonl, dest = prepare_series_from_command(records, work, options)
        steps.append(argv)
        current = jsonl
        results = jsonl
    if options.get('simplify_tags'):
        cleaned = work / 'cleaned.jsonl'
        steps.append(build_enrich_argv(str(current), str(cleaned), options))
        current = cleaned
    plugin = _import_plugin(
        jsonl=current,
        bundle_root=dest,
        update_existing=bool(options.get('update_existing', True)),
        results_jsonl=results,
        cleanup_dir=cleanup_dir,
    )
    title = 'Import JSONL'
    if options.get('include_series'):
        title = 'Import JSONL (with series)'
    return _write_spec(
        job_dir,
        {'title': title, 'kind': 'import', 'steps': steps, 'plugin': plugin, 'result': _jsonl_result(current)},
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
            'result': _jsonl_result(jsonl, label='EPUB', field='epub_file'),
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
            'result': _jsonl_result(out, label='book'),
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
            'plugin': _import_plugin(
                jsonl=out,
                bundle_root=dest if options.get('download_epubs') else None,
                update_existing=bool(options.get('update_existing', True)),
                results_jsonl=jsonl,
                skipped=skipped,
            ),
            'result': _jsonl_result(out),
        },
    )


def plan_complete_selected(
    records: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    job_dir: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Series fill + rest of series + missing EPUBs + tag simplify for a selection."""
    forced = dict(options)
    forced['download_epubs'] = True
    forced['simplify_tags'] = True
    forced['update_existing'] = True
    spec = plan_import_series(records, skipped, job_dir, forced)
    n = len(records)
    noun = 'book' if n == 1 else 'books'
    spec['title'] = f'Complete selected ({n} {noun})'
    spec['kind'] = 'complete'
    return _write_spec(job_dir, spec)


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
            'result': _jsonl_result(jsonl, label='book'),
        },
    )


def plan_cover_selected(
    ready: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    job_dir: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    work = job_dir / 'work'
    dest = work / 'bundle'
    epubs = dest / 'epubs'
    png_dir = dest / 'covers'
    dest.mkdir(parents=True, exist_ok=True)
    epubs.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)
    records = [item['record'] for item in ready]
    jsonl = dest / 'results.jsonl'
    write_records_jsonl(jsonl, records)
    argv = build_cover_argv(str(jsonl), str(dest), str(png_dir), options)
    items_path = work / 'items.json'
    write_json(items_path, {'ready': ready, 'skipped': skipped})
    n = len(ready)
    noun = 'book' if n == 1 else 'books'
    return _write_spec(
        job_dir,
        {
            'title': f'Generate covers for {n} {noun}',
            'kind': 'cover',
            'steps': [argv],
            'plugin': {
                'action': 'apply_covers',
                'jsonl': str(jsonl),
                'bundle_root': str(dest),
                'png_dir': str(png_dir),
                'items_json': str(items_path),
                'set_calibre_cover': bool(options.get('set_calibre_cover', True)),
            },
            'result': _jsonl_result(jsonl, label='cover'),
        },
    )


def plan_graph_serve(job_dir: Path, *, port: int | None = None) -> dict[str, Any]:
    """Singleton live viewer (``jobs/graph``). Does not write Calibre."""
    work = job_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    try:
        from calibre_plugins.ao3_scraper.scrape_run import build_graph_serve_argv
    except ImportError:
        from scrape_run import build_graph_serve_argv

    argv = build_graph_serve_argv(port=port)
    return _write_spec(
        job_dir,
        {
            'title': 'Tag graph viewer',
            'kind': 'graph',
            'steps': [argv],
            'plugin': {'action': 'none'},
            'result': {'source': 'last_log'},
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
