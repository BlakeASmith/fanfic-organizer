# -*- coding: utf-8 -*-
"""Load AO3 scrape results from JSONL produced outside Calibre."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def iter_jsonl_records(path: str | Path) -> Iterator[dict[str, Any]]:
    path = Path(path)
    with path.open(encoding='utf-8') as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f'{path}:{line_no}: invalid JSON: {exc}') from exc
            if not isinstance(record, dict):
                raise ValueError(f'{path}:{line_no}: expected a JSON object')
            if not record.get('work_id') and not record.get('url'):
                raise ValueError(f'{path}:{line_no}: missing work_id and url')
            yield record


def load_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    return list(iter_jsonl_records(path))
