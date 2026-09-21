# -*- coding: utf-8 -*-
"""Job plan for TWC issue/article import → Calibre."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from calibre_plugins.fanfic_organizer.jobs import write_json
    from calibre_plugins.fanfic_organizer.sources.twc.run import (
        describe_twc,
        prepare_twc_command,
    )
except ImportError:
    from jobs import write_json
    from sources.twc.run import describe_twc, prepare_twc_command


def plan_twc(options: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    work = job_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    argv, jsonl = prepare_twc_command(options, work)
    spec: dict[str, Any] = {
        'id': job_dir.name,
        'title': describe_twc(options)[:80],
        'kind': 'twc',
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
    write_json(job_dir / 'spec.json', spec)
    return spec
