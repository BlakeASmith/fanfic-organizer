# -*- coding: utf-8 -*-
"""Calibre-free helpers for ao3kit background jobs."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_READ_MAX_BYTES = 2_000_000
_PROGRESS_RE = re.compile(r'\[(\d+)/(\d+)\]')
_UNIQUE_TAGS_RE = re.compile(
    r'(\d+) unique tags across batch \((\d+) already cached, (\d+) need AO3'
)


def _user_dirs():
    name = 'wranglekit_user_dirs'
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / 'user_dirs.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def jobs_root(project: Path) -> Path:
    return _user_dirs().resolve_jobs_dir(Path(project))


def new_job_id(kind: str = 'job') -> str:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    slug = re.sub(r'[^a-z0-9]+', '-', (kind or 'job').lower()).strip('-')[:24]
    return f'{stamp}-{slug or "job"}-{uuid.uuid4().hex[:6]}'


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str) + '\n',
        encoding='utf-8',
    )


def read_json(path: Path) -> dict[str, Any] | None:
    if not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def parse_job_status_json(stdout: str) -> dict[str, Any] | None:
    text = (stdout or '').strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find('{')
        end = text.rfind('}')
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return data if isinstance(data, dict) else None


def parse_job_list_json(stdout: str) -> list[dict[str, Any]]:
    text = (stdout or '').strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find('[')
        end = text.rfind(']')
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def progress_from_message(message: str) -> tuple[int, int] | None:
    unique = _UNIQUE_TAGS_RE.search(message or '')
    if unique:
        need = int(unique.group(3))
        return 0, need if need else int(unique.group(1))
    match = _PROGRESS_RE.search(message or '')
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def read_log_tail(
    path: Path,
    *,
    lines: int = 400,
    max_bytes: int = LOG_READ_MAX_BYTES,
) -> str:
    path = Path(path)
    if not path.is_file():
        return ''
    size = path.stat().st_size
    with path.open('r', encoding='utf-8', errors='replace') as handle:
        if size > max_bytes:
            handle.seek(max(0, size - max_bytes))
            handle.readline()
        text = handle.read()
    if not text or lines <= 0:
        return text
    parts = text.splitlines()
    if len(parts) <= lines:
        return text if text.endswith('\n') or not text else text + '\n'
    return '\n'.join(parts[-lines:]) + '\n'


def first_line(value: Any, limit: int = 120) -> str:
    """First non-empty line of ``value``, truncated. Empty input → ``''``."""
    text = str(value or '').strip()
    if not text:
        return ''
    return text.splitlines()[0][:limit]


def job_was_notified(status: dict[str, Any] | None) -> bool:
    return bool((status or {}).get('notified'))


def job_watch_phase(status: dict[str, Any] | None) -> str:
    """Life cycle of the attached job window: starting/running/saving/done/failed/stopped."""
    data = dict(status or {})
    ingest = str(data.get('ingest') or '')
    if data.get('running'):
        return 'running'
    if ingest == 'pending':
        return 'saving'
    finished = bool(data.get('finished_at')) or data.get('exit_code') is not None
    if not finished and ingest not in ('done', 'cancelled', 'failed', 'skipped'):
        return 'starting'
    if ingest == 'cancelled':
        return 'stopped'
    if ingest in ('failed', 'skipped') or data.get('exit_code') not in (None, 0):
        return 'failed'
    return 'done'


def format_job_header(status: dict[str, Any] | None, log_path: Path | None = None) -> str:
    """Short, non-technical headline for the job window (log path is not shown)."""
    del log_path
    data = dict(status or {})
    title = data.get('title') or data.get('id') or 'Job'
    phase = job_watch_phase(data)
    labels = {
        'starting': 'Starting',
        'running': 'Working',
        'saving': 'Saving to your library',
        'done': 'Done',
        'failed': "Couldn't finish",
        'stopped': 'Stopped',
    }
    state = labels.get(phase, 'Working')
    message = first_line(data.get('message'), 200)
    result = first_line(data.get('result'), 200)
    bits = [f'{title} — {state}']
    if phase == 'saving':
        bits.append('Adding books to Calibre. You can hide this window; work keeps going.')
    elif phase == 'done':
        bits.append(result or message or 'You can close this window.')
    elif phase == 'failed':
        bits.append(result or message or 'Something went wrong. You can try again.')
    elif phase == 'stopped':
        bits.append(result or message or 'Stopped before it finished. You can try again.')
    elif message:
        bits.append(message)
    return '\n'.join(bits)


def job_is_retryable(status: dict[str, Any] | None) -> bool:
    """True when a finished failed/stopped job can be run again from its spec."""
    data = dict(status or {})
    if str(data.get('id') or '') == 'warm':
        return False
    if data.get('running'):
        return False
    if str(data.get('ingest') or '') == 'pending':
        return False
    ingest = str(data.get('ingest') or '')
    if ingest in ('cancelled', 'failed', 'skipped'):
        return True
    return data.get('exit_code') not in (None, 0)


def job_is_deletable(status: dict[str, Any] | None) -> bool:
    """True when a job can be removed from the list (not running, not ingesting)."""
    data = dict(status or {})
    if str(data.get('id') or '') == 'warm':
        return False
    if data.get('running'):
        return False
    return str(data.get('ingest') or '') != 'pending'


def job_clear_bucket(status: dict[str, Any] | None) -> str | None:
    """``finished``, ``failed``, or ``stopped`` when deletable; otherwise ``None``."""
    if not job_is_deletable(status):
        return None
    data = dict(status or {})
    ingest = str(data.get('ingest') or '')
    if ingest == 'cancelled':
        return 'stopped'
    if ingest in ('failed', 'skipped') or data.get('exit_code') not in (None, 0):
        return 'failed'
    return 'finished'


def job_paths(job_dir: Path) -> dict[str, Path]:
    job_dir = Path(job_dir)
    return {
        'dir': job_dir,
        'spec': job_dir / 'spec.json',
        'status': job_dir / 'status.json',
        'pid': job_dir / 'job.pid',
        'log': job_dir / 'job.log',
        'work': job_dir / 'work',
    }
