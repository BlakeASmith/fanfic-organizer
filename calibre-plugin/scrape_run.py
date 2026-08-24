# -*- coding: utf-8 -*-
"""Build ao3kit scrape argv for the Calibre plugin.

Search and optional EPUB download are one ``scrape --download`` process.
Calibre-free so pytest can import this module without Calibre.
Keep SORT_OPTIONS in sync with ao3kit.scrape.SORT_OPTIONS.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Keep in sync with ao3kit.scrape.SORT_OPTIONS.
SORT_OPTIONS = [
    ('kudos_count', 'Kudos'),
    ('hits', 'Hits'),
    ('comments_count', 'Comments'),
    ('bookmarks_count', 'Bookmarks'),
    ('word_count', 'Word count'),
    ('date_to_sort_on', 'Date updated'),
    ('created_at', 'Date posted'),
    ('title_to_sort_on', 'Title'),
]


def parse_id_list(value: str | None) -> list[int]:
    if not value or not str(value).strip():
        return []
    ids: list[int] = []
    for part in str(value).replace(' ', '').split(','):
        if part.isdigit():
            ids.append(int(part))
    return ids


def optional_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(',', '')
    if not text:
        return None
    return int(text)


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def _blank_none(value: Any) -> str | None:
    text = str(value or '').strip()
    return text or None


def complete_to_bool(value: Any) -> bool | None:
    text = str(value or '').strip().lower()
    if text in ('true', 't', '1', 'yes'):
        return True
    if text in ('false', 'f', '0', 'no'):
        return False
    return None


def ids_to_csv(values: Any) -> str:
    if not values:
        return ''
    if isinstance(values, str):
        return values
    return ','.join(str(item) for item in values)


def criteria_from_options(options: dict[str, Any]) -> dict[str, Any]:
    """JSON object accepted by ``SearchCriteria.from_dict`` / ``--criteria-file``."""
    language = _blank_none(options.get('language_id'))
    return {
        'tag_id': _blank_none(options.get('tag_id')),
        'sort_column': _blank_none(options.get('sort_column')) or 'kudos_count',
        'complete': complete_to_bool(options.get('complete')),
        'words_from': optional_int(options.get('words_from')),
        'words_to': optional_int(options.get('words_to')),
        'date_from': _blank_none(options.get('date_from')),
        'date_to': _blank_none(options.get('date_to')),
        'query': _blank_none(options.get('query')),
        'language_id': language if language is not None else 'en',
        'creators': str(options.get('creators') or '').strip(),
        'other_tag_names': str(options.get('other_tag_names') or '').strip(),
        'excluded_tag_names': str(options.get('excluded_tag_names') or '').strip(),
        'crossover': str(options.get('crossover') or '').strip(),
        'relationship_ids': parse_id_list(options.get('relationship_ids')),
        'freeform_ids': parse_id_list(options.get('freeform_ids')),
        'character_ids': parse_id_list(options.get('character_ids')),
    }


def scrape_search_is_usable(options: dict[str, Any]) -> bool:
    if str(options.get('url') or '').strip():
        return True
    criteria = criteria_from_options(options)
    return bool(
        criteria.get('tag_id')
        or criteria.get('query')
        or criteria.get('creators')
        or criteria.get('other_tag_names')
        or criteria.get('relationship_ids')
        or criteria.get('freeform_ids')
        or criteria.get('character_ids')
    )


def uses_url_search(options: dict[str, Any]) -> bool:
    url = str(options.get('url') or '').strip()
    return bool(url) and not bool(options.get('use_form_criteria'))


def write_criteria_file(path: str | Path, options: dict[str, Any]) -> Path:
    dest = Path(path)
    dest.write_text(
        json.dumps(criteria_from_options(options), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return dest


def _append_optional(argv: list[str], flag: str, value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    argv.extend([flag, text])


def merge_plugin_settings(
    options: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fill login from plugin settings when the search form omitted it.

    ``settings`` keys: ``ao3_username``, ``ao3_password``.
    Explicit values on ``options`` win. Request delay is left to the
    host-wide rate limiter (do not pass ``--delay``). Pacing is config
    ``request_delay`` (1.5s); tag profiles stay on the adaptive ~1.0s lane.
    """
    settings = settings or {}
    merged = dict(options)
    if not str(merged.get('username') or '').strip():
        merged['username'] = str(settings.get('ao3_username') or '').strip()
    if not str(merged.get('password') or ''):
        merged['password'] = str(settings.get('ao3_password') or '')
    if merged.get('include_series') is None:
        merged['include_series'] = bool(settings.get('include_series'))
    return merged


def _append_credentials(argv: list[str], options: dict[str, Any]) -> None:
    username = str(options.get('username') or '').strip()
    password = str(options.get('password') or '')
    if username and password:
        argv.extend(['--username', username, '--password', password])


def build_parse_url_argv(url: str) -> list[str]:
    return ['scrape', '--parse-only', '--url', url]


def build_login_test_argv(username: str, password: str) -> list[str]:
    return ['login', '--username', username, '--password', password]


def build_scrape_argv(
    options: dict[str, Any],
    *,
    output: str,
    criteria_file: str | None = None,
    epub_dir: str | None = None,
) -> list[str]:
    argv = ['scrape', '-o', output, '--verbose']
    if uses_url_search(options):
        argv.extend(['--url', str(options['url']).strip()])
    elif criteria_file:
        argv.extend(['--criteria-file', criteria_file])
    else:
        criteria = criteria_from_options(options)
        _append_optional(argv, '--tag-id', criteria.get('tag_id'))
        _append_optional(argv, '--query', criteria.get('query'))
        sort = criteria.get('sort_column')
        if sort and sort != 'kudos_count':
            argv.extend(['--sort-column', str(sort)])
        complete = criteria.get('complete')
        if complete is True:
            argv.append('--complete')
        elif complete is False:
            argv.append('--no-complete')
        _append_optional(argv, '--language-id', criteria.get('language_id'))
        _append_optional(argv, '--words-from', criteria.get('words_from'))
        _append_optional(argv, '--words-to', criteria.get('words_to'))
        _append_optional(argv, '--date-from', criteria.get('date_from'))
        _append_optional(argv, '--date-to', criteria.get('date_to'))
        rel = criteria.get('relationship_ids') or []
        if rel:
            argv.append('--relationship-ids')
            argv.extend(str(item) for item in rel)
        free = criteria.get('freeform_ids') or []
        if free:
            argv.append('--freeform-ids')
            argv.extend(str(item) for item in free)
        chars = criteria.get('character_ids') or []
        if chars:
            argv.append('--character-ids')
            argv.extend(str(item) for item in chars)

    start_page = optional_int(options.get('start_page'))
    if start_page and start_page != 1 and not uses_url_search(options):
        argv.extend(['--start-page', str(start_page)])

    _append_optional(argv, '--max-results', optional_int(options.get('max_results')))
    _append_optional(argv, '--min-score', optional_float(options.get('min_score')))
    _append_optional(argv, '--min-kudos', optional_int(options.get('min_kudos')))
    _append_optional(argv, '--min-words', optional_int(options.get('min_words')))
    if options.get('complete_only'):
        argv.append('--complete-only')
    if options.get('include_series'):
        argv.append('--include-series')
    _append_credentials(argv, options)
    if options.get('download_epubs'):
        argv.append('--download')
        argv.extend(['--epub-dir', epub_dir or str(Path(output).parent)])
        argv.append('--no-zip')
        argv.append('--no-simplify')
    return argv


def write_records_jsonl(path: str | Path, records: list[dict[str, Any]]) -> Path:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('w', encoding='utf-8') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    return dest


def prepare_series_from_command(
    records: list[dict[str, Any]],
    tmp: str | Path,
    options: dict[str, Any] | None = None,
) -> tuple[list[str], Path, Path]:
    """Write seed JSONL and return ``(argv, output_jsonl, dest)`` for series expand."""
    options = options or {}
    dest = Path(tmp) / 'bundle'
    dest.mkdir(parents=True, exist_ok=True)
    seeds = dest / 'seeds.jsonl'
    output = dest / 'results.jsonl'
    write_records_jsonl(seeds, records)
    argv = ['scrape', '--series-from', str(seeds), '-o', str(output), '--verbose']
    if options.get('download_epubs'):
        argv.append('--download')
        argv.extend(['--epub-dir', str(dest)])
        argv.append('--no-zip')
        argv.append('--no-simplify')
    _append_credentials(argv, options)
    return argv, output, dest


def prepare_fill_series_command(
    records: list[dict[str, Any]],
    tmp: str | Path,
    options: dict[str, Any] | None = None,
) -> tuple[list[str], Path]:
    """Write seed JSONL and return ``(argv, output_jsonl)`` for series fill."""
    options = options or {}
    dest = Path(tmp)
    dest.mkdir(parents=True, exist_ok=True)
    seeds = dest / 'seeds.jsonl'
    output = dest / 'results.jsonl'
    write_records_jsonl(seeds, records)
    argv = [
        'scrape',
        '--fill-series-from',
        str(seeds),
        '-o',
        str(output),
        '--verbose',
    ]
    _append_credentials(argv, options)
    return argv, output


def prepare_download_command(
    records: list[dict[str, Any]],
    tmp: str | Path,
    options: dict[str, Any] | None = None,
) -> tuple[list[str], Path, Path]:
    """Write JSONL under ``tmp/bundle`` and return ``(argv, jsonl, dest)``."""
    dest = Path(tmp) / 'bundle'
    dest.mkdir(parents=True, exist_ok=True)
    jsonl = dest / 'results.jsonl'
    write_records_jsonl(jsonl, records)
    argv = build_download_argv(str(jsonl), str(dest), options or {})
    return argv, jsonl, dest


def build_download_argv(
    jsonl: str,
    dest_dir: str,
    options: dict[str, Any],
) -> list[str]:
    argv = [
        'download',
        '-i',
        jsonl,
        '-d',
        dest_dir,
        '--verbose',
        '--no-zip',
        '--no-simplify',
    ]
    _append_credentials(argv, options)
    return argv


def build_cover_argv(
    jsonl: str,
    dest_dir: str,
    png_dir: str,
    options: dict[str, Any] | None = None,
) -> list[str]:
    argv = [
        'cover',
        '--jsonl',
        jsonl,
        '--dir',
        dest_dir,
        '--png-dir',
        png_dir,
        '--verbose',
        '--replace',
    ]
    # Cover generation is local; the cover CLI does not accept login flags.
    return argv


def build_enrich_argv(
    jsonl_in: str,
    jsonl_out: str,
    options: dict[str, Any],
) -> list[str]:
    argv = [
        'tags',
        'enrich',
        '--jsonl',
        jsonl_in,
        '-o',
        jsonl_out,
        '--verbose',
    ]
    _append_credentials(argv, options)
    return argv


def build_collections_argv(
    jsonl_in: str,
    jsonl_out: str,
    options: dict[str, Any],
) -> list[str]:
    return [
        'tags',
        'collections',
        '--jsonl',
        jsonl_in,
        '-o',
        jsonl_out,
        '--verbose',
    ]


def build_collections_explain_argv(jsonl_in: str, jsonl_out: str) -> list[str]:
    return [
        'tags',
        'collections',
        '--jsonl',
        jsonl_in,
        '-o',
        jsonl_out,
        '--explain',
    ]


def build_warm_start_argv(
    names_file: str,
    options: dict[str, Any],
) -> list[str]:
    argv = ['tags', 'warm', 'start', '--names-file', names_file]
    _append_credentials(argv, options)
    return argv


def build_warm_status_argv() -> list[str]:
    return ['tags', 'warm', 'status']


def build_warm_stop_argv() -> list[str]:
    return ['tags', 'warm', 'stop']


def build_warm_log_argv(*, lines: int | None = None, follow: bool = False) -> list[str]:
    argv = ['tags', 'warm', 'log']
    if lines is not None:
        argv.extend(['--lines', str(lines)])
    if follow:
        argv.append('--follow')
    return argv


def build_job_start_argv(job_dir: str, jobs_dir: str | None = None) -> list[str]:
    argv = ['job', 'start']
    if jobs_dir:
        argv.extend(['--jobs-dir', jobs_dir])
    argv.extend(['--dir', job_dir])
    return argv


def build_job_stop_argv(job_id: str, jobs_dir: str | None = None) -> list[str]:
    argv = ['job', 'stop']
    if jobs_dir:
        argv.extend(['--jobs-dir', jobs_dir])
    argv.append(job_id)
    return argv


def build_job_retry_argv(job_id: str, jobs_dir: str | None = None) -> list[str]:
    argv = ['job', 'retry']
    if jobs_dir:
        argv.extend(['--jobs-dir', jobs_dir])
    argv.append(job_id)
    return argv


def build_job_delete_argv(job_ids: list[str], jobs_dir: str | None = None) -> list[str]:
    argv = ['job', 'delete']
    if jobs_dir:
        argv.extend(['--jobs-dir', jobs_dir])
    argv.extend(job_ids)
    return argv


def build_job_clear_argv(
    *,
    finished: bool = False,
    failed: bool = False,
    stopped: bool = False,
    inactive: bool = False,
    jobs_dir: str | None = None,
) -> list[str]:
    argv = ['job', 'clear']
    if jobs_dir:
        argv.extend(['--jobs-dir', jobs_dir])
    if inactive:
        argv.append('--inactive')
        return argv
    if finished:
        argv.append('--finished')
    if failed:
        argv.append('--failed')
    if stopped:
        argv.append('--stopped')
    return argv


def build_job_list_argv(jobs_dir: str | None = None) -> list[str]:
    argv = ['job', 'list']
    if jobs_dir:
        argv.extend(['--jobs-dir', jobs_dir])
    return argv


def build_job_status_argv(job_id: str | None = None, jobs_dir: str | None = None) -> list[str]:
    argv = ['job', 'status']
    if jobs_dir:
        argv.extend(['--jobs-dir', jobs_dir])
    if job_id:
        argv.append(job_id)
    return argv


def build_tag_graph_argv(
    names_file: str | None,
    output: str,
    *,
    jsonl: str | None = None,
    open_browser: bool = True,
) -> list[str]:
    argv = [
        'tags',
        'graph',
        '-o',
        output,
        '--format',
        'html',
    ]
    if names_file:
        argv.extend(['--names-file', names_file])
    if jsonl:
        argv.extend(['--jsonl', jsonl])
    if open_browser:
        argv.append('--open')
    return argv


def live_graph_reload_argv() -> list[str]:
    return ['tags', 'graph', 'reload']


def build_graph_serve_argv(*, port: int | None = None) -> list[str]:
    argv = ['tags', 'graph', 'serve', '--no-open']
    if port:
        argv.extend(['--port', str(port)])
    return argv


def prepare_scrape_command(options: dict[str, Any], tmp: str | Path) -> tuple[list[str], Path]:
    """Write a criteria file when needed and return ``(argv, jsonl_path)``."""
    tmp_path = Path(tmp)
    epub_dir = None
    if options.get('download_epubs'):
        dest = tmp_path / 'bundle'
        dest.mkdir(parents=True, exist_ok=True)
        jsonl = dest / 'results.jsonl'
        epub_dir = str(dest)
    else:
        jsonl = tmp_path / 'results.jsonl'
    criteria_file = None
    if not uses_url_search(options):
        criteria_path = tmp_path / 'criteria.json'
        write_criteria_file(criteria_path, options)
        criteria_file = str(criteria_path)
    argv = build_scrape_argv(
        options,
        output=str(jsonl),
        criteria_file=criteria_file,
        epub_dir=epub_dir,
    )
    return argv, jsonl


def describe_scrape(options: dict[str, Any]) -> str:
    """Short user-facing summary of a plugin search (no paths or argv)."""
    criteria = criteria_from_options(options)
    tag = criteria.get('tag_id')
    query = criteria.get('query')
    downloading = bool(options.get('download_epubs'))
    verb = 'Searching AO3 and downloading' if downloading else 'Searching AO3'
    if tag and query:
        headline = f'{verb}: {tag}'
        extras = [f'Query: {query}']
    elif tag:
        headline = f'{verb}: {tag}'
        extras = []
    elif query:
        headline = f'{verb}: {query}'
        extras = []
    elif uses_url_search(options):
        headline = (
            'Searching AO3 from pasted URL and downloading EPUBs'
            if downloading
            else 'Searching AO3 from pasted URL'
        )
        extras = []
    else:
        headline = f'{verb}' if downloading else 'Searching AO3'
        extras = []

    sort_key = str(criteria.get('sort_column') or 'kudos_count')
    sort_label = dict(SORT_OPTIONS).get(sort_key, sort_key)
    filters: list[str] = [f'sorted by {sort_label}']
    max_results = optional_int(options.get('max_results'))
    if max_results:
        filters.append(f'up to {max_results} work{"s" if max_results != 1 else ""}')
    min_score = optional_float(options.get('min_score'))
    if min_score is not None:
        filters.append(f'min score {min_score:g}')
    min_kudos = optional_int(options.get('min_kudos'))
    if min_kudos is not None:
        filters.append(f'min kudos {min_kudos}')
    min_words = optional_int(options.get('min_words'))
    if min_words is not None:
        filters.append(f'min {min_words} words')
    if options.get('complete_only') or criteria.get('complete') is True:
        filters.append('complete only')
    start_page = optional_int(options.get('start_page'))
    if start_page and start_page > 1:
        filters.append(f'start page {start_page}')
    extras.append(', '.join(filters))
    if downloading:
        extras.append('One run: search, then native EPUBs.')
    if options.get('simplify_tags'):
        extras.append(
            'Will simplify tags, fandoms, and relationships after import '
            'metadata is ready.'
        )
    if options.get('include_series'):
        extras.append('Will also import other works in the same series.')
    return '\n'.join([headline, *extras])
