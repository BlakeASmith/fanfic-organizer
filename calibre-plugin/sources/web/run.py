# -*- coding: utf-8 -*-
"""Build ``python -m ao3kit web`` argv (Calibre-free)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def web_import_is_usable(options: dict[str, Any]) -> bool:
    if str(options.get('url') or '').strip():
        return True
    return bool(str(options.get('html_path') or '').strip())


def describe_web(options: dict[str, Any]) -> str:
    url = str(options.get('url') or '').strip()
    html_path = str(options.get('html_path') or '').strip()
    if html_path and url:
        return f'Web HTML+URL: {url}'
    if html_path:
        name = Path(html_path).name
        return f'Web HTML: {name}'
    if url:
        return f'Web URL: {url}'
    return 'Web import'


def prepare_web_command(
    options: dict[str, Any], work_dir: str | Path
) -> tuple[list[str], Path]:
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    jsonl = work / 'results.jsonl'
    argv = ['web', '--output', str(jsonl)]

    url = str(options.get('url') or '').strip()
    html_path = str(options.get('html_path') or '').strip()

    if html_path:
        src = Path(html_path)
        if not src.is_file():
            raise ValueError(f'HTML file not found: {src}')
        dest = work / 'input.html'
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        argv.extend(['--html', str(dest)])
        if url:
            argv.extend(['--page-url', url])
    elif url:
        argv.extend(['--url', url])
    else:
        raise ValueError('Web import needs a URL or saved HTML file')

    if options.get('verbose'):
        argv.append('--verbose')
    if options.get('download_epubs'):
        argv.append('--epub')
        argv.extend(['--epub-dir', str(work)])
    else:
        argv.append('--no-epub')
    return argv, jsonl
