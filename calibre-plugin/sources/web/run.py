# -*- coding: utf-8 -*-
"""Build ``python -m ao3kit web`` / ``webcompile`` argv (Calibre-free)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def web_import_is_usable(options: dict[str, Any]) -> bool:
    mode = str(options.get('mode') or 'single').strip().casefold()
    if mode == 'compile':
        if str(options.get('bundle_path') or '').strip():
            return True
        seeds = options.get('seeds') or []
        if isinstance(seeds, str):
            seeds = [seeds]
        if any(str(s).strip() for s in seeds):
            return True
        if str(options.get('url') or '').strip():
            return True
        return False
    if str(options.get('url') or '').strip():
        return True
    return bool(str(options.get('html_path') or '').strip())


def describe_web(options: dict[str, Any]) -> str:
    mode = str(options.get('mode') or 'single').strip().casefold()
    if mode == 'compile':
        bundle = str(options.get('bundle_path') or '').strip()
        if bundle:
            return f'Web compile bundle: {Path(bundle).name}'
        seeds = options.get('seeds') or []
        if isinstance(seeds, str):
            seeds = [seeds]
        seeds = [str(s).strip() for s in seeds if str(s).strip()]
        title = str(options.get('book_title') or '').strip()
        if title:
            return f'Web compile: {title}'
        if seeds:
            return f'Web compile: {seeds[0]}'
        url = str(options.get('url') or '').strip()
        if url:
            return f'Web compile: {url}'
        return 'Web compile'
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
    mode = str(options.get('mode') or 'single').strip().casefold()

    if mode == 'compile':
        return _prepare_compile(options, work, jsonl)

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


def _prepare_compile(
    options: dict[str, Any], work: Path, jsonl: Path
) -> tuple[list[str], Path]:
    argv = ['webcompile', '--output', str(jsonl), '--epub-dir', str(work)]
    bundle = str(options.get('bundle_path') or '').strip()
    if bundle:
        src = Path(bundle)
        if not src.is_file():
            raise ValueError(f'Bundle file not found: {src}')
        dest = work / 'bundle.json'
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        argv.extend(['--bundle', str(dest)])
    else:
        seeds = options.get('seeds') or []
        if isinstance(seeds, str):
            seeds = [seeds]
        seeds = [str(s).strip() for s in seeds if str(s).strip()]
        url = str(options.get('url') or '').strip()
        if url and url not in seeds:
            seeds.insert(0, url)
        if not seeds:
            raise ValueError('Web compile needs seed URLs or a crawl bundle')

        full_list = bool(options.get('full_list'))
        expand = str(options.get('expand') or 'same_domain').strip()
        if full_list or expand == 'none':
            for seed in seeds:
                argv.extend(['--url', seed])
            argv.extend(['--expand', 'none'])
        else:
            for seed in seeds:
                argv.extend(['--seed', seed])
            argv.extend(['--expand', expand])
            if expand == 'domains':
                domains = options.get('domains') or []
                if isinstance(domains, str):
                    domains = [d.strip() for d in domains.split(',') if d.strip()]
                for domain in domains:
                    argv.extend(['--domain', str(domain).strip()])

        max_pages = int(options.get('max_pages') or 50)
        max_depth = int(options.get('max_depth') or 2)
        argv.extend(['--max-pages', str(max(1, max_pages))])
        argv.extend(['--max-depth', str(max(0, max_depth))])

    title = str(options.get('book_title') or '').strip()
    if title:
        argv.extend(['--title', title])
    author = str(options.get('book_author') or '').strip()
    if author:
        argv.extend(['--author', author])

    if options.get('verbose'):
        argv.append('--verbose')
    if options.get('download_epubs', True):
        # webcompile always builds an EPUB; cover follows flag.
        if options.get('cover', True):
            argv.append('--cover')
        else:
            argv.append('--no-cover')
    else:
        # Still produce EPUB for attach path; flag kept for symmetry.
        argv.append('--cover')
    return argv, jsonl
