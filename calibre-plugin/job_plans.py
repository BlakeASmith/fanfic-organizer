# -*- coding: utf-8 -*-
"""Build ao3kit job specs for plugin operations (Calibre-free)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from calibre_plugins.fanfic_organizer.jobs import write_json
    from calibre_plugins.fanfic_organizer.library_job import (
        LibraryJobOptions,
        library_job_title,
    )
    from calibre_plugins.fanfic_organizer.scrape_run import (
        build_collections_argv,
        build_cover_argv,
        build_enrich_argv,
        describe_scrape,
        prepare_download_command,
        prepare_fill_series_command,
        prepare_identify_command,
        prepare_scrape_command,
        prepare_series_from_command,
        prepare_works_from_command,
        write_records_jsonl,
    )
except ImportError:  # pytest loads this file without the Calibre package
    from jobs import write_json
    from library_job import LibraryJobOptions, library_job_title
    from scrape_run import (
        build_collections_argv,
        build_cover_argv,
        build_enrich_argv,
        describe_scrape,
        prepare_download_command,
        prepare_fill_series_command,
        prepare_identify_command,
        prepare_scrape_command,
        prepare_series_from_command,
        prepare_works_from_command,
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


def _record_has_ao3(record: dict[str, Any] | None) -> bool:
    rec = record or {}
    return bool(
        str(rec.get('work_id') or '').strip()
        or str(rec.get('url') or '').strip()
    )


def _dedupe_actions(actions: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for action in actions:
        text = str(action or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def plan_library_job(
    ready: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    job_dir: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Compose selected-book operations for the whole open library."""
    simplify = bool(options.get('simplify_tags'))
    fill_series = bool(options.get('fill_series'))
    import_series = bool(options.get('import_series'))
    download = bool(options.get('download_epubs'))
    covers = bool(options.get('generate_covers'))
    collections = bool(options.get('recompute_collections'))
    cover_flag = options.get('cover_on_download')
    if cover_flag is None:
        cover_flag = options.get('cover')

    work = job_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    records = [item['record'] for item in ready]
    current = work / 'input.jsonl'
    write_records_jsonl(current, records)
    dest = work / 'bundle'
    dest.mkdir(parents=True, exist_ok=True)
    steps: list[list[str]] = []
    actions: list[str] = []
    plugin: dict[str, Any] = {
        'jsonl': str(current),
        'items_json': str(work / 'items.json'),
        'update_existing': bool(options.get('update_existing', True)),
        'skip_existing_epub': True,
        'incremental_import': True,
        'set_calibre_cover': bool(options.get('set_calibre_cover', True)),
    }
    write_json(work / 'items.json', {'ready': ready, 'skipped': skipped})

    series_opts = dict(options)
    series_opts['download_epubs'] = bool(download)
    if cover_flag is True:
        series_opts['cover'] = True
    elif cover_flag is False:
        series_opts['cover'] = False

    ao3_records = [record for record in records if _record_has_ao3(record)]

    if import_series and ao3_records:
        argv, jsonl, dest = prepare_series_from_command(records, work, series_opts)
        steps.append(argv)
        current = jsonl
        plugin['bundle_root'] = str(dest)
        plugin['results_jsonl'] = str(jsonl)
        actions.append('import_records')
    elif fill_series and ao3_records:
        argv, jsonl = prepare_fill_series_command(records, work, options)
        steps.append(argv)
        current = jsonl
        actions.append('apply_series')

    if download and not import_series:
        download_ready = [
            item
            for item in ready
            if not item.get('has_epub')
            and (
                str((item.get('record') or {}).get('work_id') or '').strip()
                or str((item.get('record') or {}).get('url') or '').strip()
            )
        ]
        if download_ready:
            argv, jsonl, dest = prepare_download_command(
                [item['record'] for item in download_ready], work, series_opts
            )
            steps.append(argv)
            plugin['bundle_root'] = str(dest)
            plugin['results_jsonl'] = str(jsonl)
            actions.append('attach_epubs')

    if simplify:
        cleaned = work / 'cleaned.jsonl'
        steps.append(build_enrich_argv(str(current), str(cleaned), options))
        current = cleaned
        if 'import_records' not in actions:
            actions.append('apply_cleaned')
            actions = [item for item in actions if item != 'apply_series']
    elif collections:
        out = work / 'collections.jsonl'
        steps.append(build_collections_argv(str(current), str(out), options))
        current = out
        if 'import_records' not in actions:
            actions.append('apply_collections')

    if covers:
        png_dir = dest / 'covers'
        png_dir.mkdir(parents=True, exist_ok=True)
        (dest / 'epubs').mkdir(parents=True, exist_ok=True)
        cover_jsonl = dest / 'cover-input.jsonl'
        write_records_jsonl(cover_jsonl, records)
        steps.append(build_cover_argv(str(cover_jsonl), str(dest), str(png_dir), options))
        plugin['png_dir'] = str(png_dir)
        plugin['bundle_root'] = str(dest)
        actions.append('apply_covers')

    plugin['jsonl'] = str(current)
    actions = _dedupe_actions(actions)
    if not actions:
        actions = ['none']
    plugin['action'] = actions[0]
    if len(actions) > 1:
        plugin['actions'] = actions

    n = len(ready)
    title = library_job_title(LibraryJobOptions.from_dict(options), n)
    return _write_spec(
        job_dir,
        {
            'title': title,
            'kind': 'library',
            'steps': steps,
            'plugin': plugin,
            'result': _jsonl_result(current, label='book'),
        },
    )


def plan_identify_selected(
    ready: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    job_dir: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Identify selected books, then Calibre ingest may prompt and fill."""
    work = job_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    bundle = work / 'bundle'
    bundle.mkdir(parents=True, exist_ok=True)
    records = [item['record'] for item in ready]
    argv, jsonl = prepare_identify_command(
        records, work, options, bundle=bundle, search=True
    )
    items_path = work / 'items.json'
    write_json(items_path, {'ready': ready, 'skipped': skipped})
    n = len(ready)
    noun = 'book' if n == 1 else 'books'
    fill_options = {
        'download_epubs': bool(options.get('download_epubs', True)),
        'simplify_tags': bool(options.get('simplify_tags', False)),
        'include_series': bool(options.get('include_series', False)),
        'update_existing': True,
        'username': options.get('username') or '',
        'password': options.get('password') or '',
    }
    return _write_spec(
        job_dir,
        {
            'title': f'Identify AO3 works ({n} {noun})',
            'kind': 'identify',
            'steps': [argv],
            'plugin': {
                'action': 'resolve_identify',
                'jsonl': str(jsonl),
                'items_json': str(items_path),
                'bundle_root': str(bundle),
                'fill_options': fill_options,
            },
            'result': _jsonl_result(jsonl, label='book'),
        },
    )


def plan_fill_from_ao3(
    ready: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    job_dir: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Fetch work pages (and optional EPUBs/tags) for already-identified books."""
    work = job_dir / 'work'
    records = [item['record'] for item in ready]
    if options.get('include_series'):
        argv, jsonl, dest = prepare_series_from_command(records, work, options)
    else:
        argv, jsonl, dest = prepare_works_from_command(records, work, options)
    steps = [argv]
    out = jsonl
    if options.get('simplify_tags'):
        cleaned = work / 'cleaned.jsonl'
        steps.append(build_enrich_argv(str(jsonl), str(cleaned), options))
        out = cleaned
    items_path = work / 'items.json'
    write_json(items_path, {'ready': ready, 'skipped': skipped})
    n = len(ready)
    noun = 'book' if n == 1 else 'books'
    plugin = _import_plugin(
        jsonl=out,
        bundle_root=dest if options.get('download_epubs') else None,
        update_existing=True,
        results_jsonl=jsonl,
        skipped=skipped,
    )
    plugin['items_json'] = str(items_path)
    return _write_spec(
        job_dir,
        {
            'title': f'Fill from AO3 ({n} {noun})',
            'kind': 'fill',
            'steps': steps,
            'plugin': plugin,
            'result': _jsonl_result(out),
        },
    )


def plan_graph_serve(job_dir: Path, *, port: int | None = None) -> dict[str, Any]:
    """Singleton live viewer (``jobs/graph``). Does not write Calibre."""
    work = job_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    try:
        from calibre_plugins.fanfic_organizer.scrape_run import build_graph_serve_argv
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
    by_uuid: dict[str, dict[str, Any]] = {}
    by_book_id: dict[int, dict[str, Any]] = {}
    for record in records:
        work_id = work_id_of(record)
        if work_id:
            by_id[str(work_id)] = record
        uuid = str(record.get('calibre_uuid') or '').strip()
        if uuid:
            by_uuid[uuid] = record
        raw_book_id = record.get('calibre_book_id')
        if raw_book_id is not None:
            try:
                by_book_id[int(raw_book_id)] = record
            except (TypeError, ValueError):
                pass
    merged: list[dict[str, Any]] = []
    for item in ready:
        record = item.get('record') or {}
        work_id = work_id_of(record)
        uuid = str(record.get('calibre_uuid') or '').strip()
        updated = dict(item)
        book_id = item.get('book_id')
        if work_id and str(work_id) in by_id:
            updated['record'] = by_id[str(work_id)]
        elif uuid and uuid in by_uuid:
            updated['record'] = by_uuid[uuid]
        else:
            try:
                key = int(book_id)
            except (TypeError, ValueError):
                key = None
            if key is not None and key in by_book_id:
                updated['record'] = by_book_id[key]
        merged.append(updated)
    return merged


def load_identify_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Load identify JSONL (work id is optional on ambiguous/failed rows)."""
    records: list[dict[str, Any]] = []
    dest = Path(path)
    if not dest.is_file():
        return records
    for line in dest.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def split_identify_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    identified: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for record in records:
        status = str(record.get('status') or '').strip()
        work_id = str(record.get('work_id') or '').strip()
        if status == 'identified' and work_id:
            identified.append(record)
        elif status == 'ambiguous':
            ambiguous.append(record)
        else:
            failed.append(record)
    return identified, ambiguous, failed


def apply_identify_choices(
    records: list[dict[str, Any]],
    choices: dict[Any, Any],
) -> list[dict[str, Any]]:
    """Apply ``{book_id: work_id}`` picks; skip empty choices."""
    mapped = {
        str(key).strip(): str(value).strip()
        for key, value in (choices or {}).items()
        if str(key).strip()
    }
    out: list[dict[str, Any]] = []
    for record in records:
        status = str(record.get('status') or '').strip()
        if status == 'identified' and str(record.get('work_id') or '').strip():
            out.append(dict(record))
            continue
        if status != 'ambiguous':
            continue
        book_id = record.get('book_id')
        if book_id is None:
            book_id = record.get('calibre_book_id')
        picked = mapped.get(str(book_id or '').strip(), '')
        if not picked:
            continue
        updated = dict(record)
        for candidate in record.get('candidates') or []:
            if str((candidate or {}).get('work_id') or '').strip() != picked:
                continue
            for key, value in candidate.items():
                if key in {'status', 'source', 'reason', 'candidates', 'score'}:
                    continue
                updated[key] = value
            break
        updated['work_id'] = picked
        url = str(updated.get('url') or '').strip()
        if not url:
            updated['url'] = f'https://archiveofourown.org/works/{picked}'
        updated['status'] = 'identified'
        updated['source'] = str(updated.get('source') or 'search')
        updated.pop('candidates', None)
        updated.pop('reason', None)
        out.append(updated)
    return out


def merge_identify_ready(
    ready: list[dict[str, Any]],
    identified: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach identified work records onto the original ``{book_id, record}`` rows."""
    by_book: dict[str, dict[str, Any]] = {}
    for record in identified:
        book_id = record.get('book_id')
        if book_id is None:
            book_id = record.get('calibre_book_id')
        if book_id is None:
            continue
        by_book[str(book_id)] = record
    merged: list[dict[str, Any]] = []
    for item in ready:
        book_id = item.get('book_id')
        record = by_book.get(str(book_id))
        if record is None:
            continue
        updated = dict(item)
        updated['record'] = record
        updated['title'] = record.get('title') or item.get('title')
        merged.append(updated)
    return merged
