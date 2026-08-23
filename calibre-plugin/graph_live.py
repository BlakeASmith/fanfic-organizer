# -*- coding: utf-8 -*-
"""Calibre-free live tag-graph helpers (inbox + JSONL upsert)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

GRAPH_JOB_ID = 'graph'
GRAPH_INBOX_NAME = 'graph-inbox'
GRAPH_JSONL_NAME = 'tag_graph_works.jsonl'
GRAPH_SERVE_STAMP_NAME = 'tag-graph-serve.json'

GRAPH_RECORD_KEYS = (
    'work_id',
    'title',
    'url',
    'authors',
    'tags',
    'fandoms',
    'relationships',
    'characters',
    'calibre_uuid',
)


def _user_dirs():
    try:
        from calibre_plugins.wranglekit.runtime import load_user_dirs
        return load_user_dirs()
    except ImportError:
        pass
    name = '_wranglekit_plugin_runtime'
    cached = sys.modules.get(name)
    if cached is None:
        path = Path(__file__).resolve().parent / 'runtime.py'
        spec = importlib.util.spec_from_file_location(name, path)
        cached = importlib.util.module_from_spec(spec)
        sys.modules[name] = cached
        assert spec.loader is not None
        spec.loader.exec_module(cached)
    return cached.load_user_dirs()


def graph_inbox_dir(project: Path) -> Path:
    return _user_dirs().resolve_graph_inbox_dir(Path(project))


def graph_jsonl_path(project: Path) -> Path:
    return _user_dirs().resolve_graph_jsonl_file(Path(project))


def graph_html_path(project: Path) -> Path:
    return _user_dirs().resolve_graph_html_file(Path(project))


def graph_serve_stamp_path(project: Path) -> Path:
    return _user_dirs().resolve_graph_serve_stamp_file(Path(project))


def read_serve_url(project: Path) -> str | None:
    stamp_path = graph_serve_stamp_path(project)
    if not stamp_path.is_file():
        return None
    try:
        data = json.loads(stamp_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get('pid')
    if isinstance(pid, int) and pid > 0:
        import os

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
        except OSError:
            return None
    url = str(data.get('url') or '').strip()
    return url or None


def slim_graph_record(record: dict[str, Any]) -> dict[str, Any]:
    slim: dict[str, Any] = {}
    for key in GRAPH_RECORD_KEYS:
        value = record.get(key)
        if value:
            slim[key] = value
    work_id = str(slim.get('work_id') or '').strip()
    if not work_id:
        url = str(record.get('url') or '')
        marker = '/works/'
        idx = url.find(marker)
        if idx >= 0:
            digits: list[str] = []
            for ch in url[idx + len(marker) :]:
                if ch.isdigit():
                    digits.append(ch)
                else:
                    break
            work_id = ''.join(digits)
            if work_id:
                slim['work_id'] = work_id
    if not slim.get('authors'):
        author = record.get('author')
        if author:
            slim['authors'] = (
                author if isinstance(author, list) else [str(author).strip()]
            )
    if not slim.get('title') and not slim.get('work_id'):
        return {}
    return slim


def upsert_graph_jsonl(path: Path, records: list[dict[str, Any]]) -> int:
    """Merge scrape/library records into the live graph dump. Returns new rows."""
    path = Path(path)
    existing: list[dict[str, Any]] = []
    by_id: dict[str, int] = {}
    if path.is_file():
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            work_id = str(row.get('work_id') or '').strip()
            existing.append(row)
            if work_id:
                by_id[work_id] = len(existing) - 1
    added = 0
    for record in records:
        slim = slim_graph_record(record)
        work_id = str(slim.get('work_id') or '').strip()
        if not work_id:
            continue
        if work_id in by_id:
            existing[by_id[work_id]] = slim
            continue
        by_id[work_id] = len(existing)
        existing.append(slim)
        added += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        for row in existing:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    return added


def pending_graph_commands(project: Path) -> list[tuple[Path, dict[str, Any]]]:
    inbox = graph_inbox_dir(project)
    if not inbox.is_dir():
        return []
    pending: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(inbox.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and str(data.get('status') or 'pending') == 'pending':
            pending.append((path, data))
    return pending


def mark_graph_command_done(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def mark_graph_command_error(path: Path, error: str) -> None:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    data['status'] = 'error'
    data['error'] = error
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
