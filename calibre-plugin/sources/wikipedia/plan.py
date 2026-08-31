# -*- coding: utf-8 -*-
"""Job plan for Wikipedia search/fetch → Calibre import."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from calibre_plugins.fanfic_organizer.jobs import write_json
    from calibre_plugins.fanfic_organizer.sources.wikipedia.run import (
        describe_wikipedia,
        prepare_wikipedia_command,
    )
except ImportError:
    from jobs import write_json
    from sources.wikipedia.run import describe_wikipedia, prepare_wikipedia_command


def plan_wikipedia(options: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    work = job_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    argv, jsonl = prepare_wikipedia_command(options, work)
    build_epub = bool(options.get('download_epubs'))
    spec: dict[str, Any] = {
        'id': job_dir.name,
        'title': describe_wikipedia(options)[:80],
        'kind': 'wikipedia',
        'steps': [argv],
        'plugin': {
            'action': 'import_records',
            'update_existing': bool(options.get('update_existing', True)),
            'skip_existing_epub': True,
            'jsonl': str(jsonl),
            'bundle_root': str(work),
            'results_jsonl': str(jsonl),
            'incremental_import': True,
        },
        'result': {
            'source': 'jsonl_count',
            'path': str(jsonl),
            'label': 'article',
        },
    }
    if build_epub:
        # EPUBs land in work/epubs/{pageid}.epub via --epub-dir.
        spec['plugin']['bundle_root'] = str(work)
    write_json(job_dir / 'spec.json', spec)
    return spec
