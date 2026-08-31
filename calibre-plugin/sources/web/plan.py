# -*- coding: utf-8 -*-
"""Job plan for generic URL/HTML → Calibre import."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from calibre_plugins.fanfic_organizer.jobs import write_json
    from calibre_plugins.fanfic_organizer.sources.web.run import (
        describe_web,
        prepare_web_command,
    )
except ImportError:
    from jobs import write_json
    from sources.web.run import describe_web, prepare_web_command


def plan_web(options: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    work = job_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    argv, jsonl = prepare_web_command(options, work)
    mode = str(options.get('mode') or 'single').strip().casefold()
    kind = 'webcompile' if mode == 'compile' else 'web'
    label = 'book' if mode == 'compile' else 'page'
    spec: dict[str, Any] = {
        'id': job_dir.name,
        'title': describe_web(options)[:80],
        'kind': kind,
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
            'label': label,
        },
    }
    write_json(job_dir / 'spec.json', spec)
    return spec
