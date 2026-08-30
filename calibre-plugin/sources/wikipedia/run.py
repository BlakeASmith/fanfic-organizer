# -*- coding: utf-8 -*-
"""Build ``python -m ao3kit wikipedia`` argv (Calibre-free)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def wikipedia_search_is_usable(options: dict[str, Any]) -> bool:
    if str(options.get('query') or '').strip():
        return True
    if str(options.get('url') or '').strip():
        return True
    if str(options.get('title') or '').strip():
        return True
    page_id = str(options.get('page_id') or '').strip()
    return bool(page_id.isdigit())


def describe_wikipedia(options: dict[str, Any]) -> str:
    url = str(options.get('url') or '').strip()
    if url:
        return f'Wikipedia: {url}'
    title = str(options.get('title') or '').strip()
    if title:
        return f'Wikipedia: {title}'
    page_id = str(options.get('page_id') or '').strip()
    if page_id.isdigit():
        return f'Wikipedia page {page_id}'
    query = str(options.get('query') or '').strip()
    lang = str(options.get('lang') or 'en').strip() or 'en'
    if query:
        return f'Wikipedia search ({lang}): {query}'
    return 'Wikipedia import'


def prepare_wikipedia_command(
    options: dict[str, Any], work_dir: str | Path
) -> tuple[list[str], Path]:
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    jsonl = work / 'results.jsonl'
    argv = ['wikipedia', '--output', str(jsonl)]
    lang = str(options.get('lang') or 'en').strip() or 'en'
    argv.extend(['--lang', lang])

    url = str(options.get('url') or '').strip()
    query = str(options.get('query') or '').strip()
    title = str(options.get('title') or '').strip()
    page_id = str(options.get('page_id') or '').strip()

    if url:
        argv.extend(['--url', url])
    elif page_id.isdigit():
        argv.extend(['--page-id', page_id])
    elif title:
        argv.extend(['--title', title])
    elif query:
        argv.extend(['--query', query])
        max_results = str(options.get('max_results') or '').strip()
        if max_results.isdigit():
            argv.extend(['--max-results', max_results])
    else:
        raise ValueError('Wikipedia import needs a query, URL, title, or page id')

    if options.get('verbose'):
        argv.append('--verbose')
    if options.get('download_epubs'):
        argv.append('--epub')
        argv.extend(['--epub-dir', str(work)])
    else:
        argv.append('--no-epub')
    return argv, jsonl
