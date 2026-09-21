# -*- coding: utf-8 -*-
"""Build ``python -m ao3kit twc`` argv (Calibre-free)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def twc_import_is_usable(options: dict[str, Any]) -> bool:
    return bool(str(options.get('url') or '').strip())


def describe_twc(options: dict[str, Any]) -> str:
    url = str(options.get('url') or '').strip()
    if url:
        return f'TWC: {url}'
    return 'TWC journal import'


def prepare_twc_command(
    options: dict[str, Any], work_dir: str | Path
) -> tuple[list[str], Path]:
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    jsonl = work / 'results.jsonl'
    argv = ['twc', '--output', str(jsonl)]

    url = str(options.get('url') or '').strip()
    if not url:
        raise ValueError('TWC import needs an issue or article URL')
    argv.extend(['--url', url])

    if options.get('verbose'):
        argv.append('--verbose')
    if options.get('download_epubs', True):
        argv.append('--epub')
        argv.extend(['--epub-dir', str(work)])
    else:
        argv.append('--no-epub')
    return argv, jsonl
