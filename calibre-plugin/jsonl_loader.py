# -*- coding: utf-8 -*-
"""Load AO3 scrape results from JSONL or an import zip."""

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterator

MANIFEST_NAME = 'results.jsonl'
EPUB_DIRNAME = 'epubs'


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


def find_manifest(root: Path) -> Path:
    direct = root / MANIFEST_NAME
    if direct.exists():
        return direct
    matches = sorted(root.rglob('*.jsonl'))
    if not matches:
        raise ValueError(f'{root} contains no JSONL manifest')
    return matches[0]


def extract_import_zip(zip_path: str | Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    return find_manifest(dest)


def resolve_epub_path(record: dict[str, Any], bundle_root: str | Path) -> Path | None:
    root = Path(bundle_root)
    epub_file = record.get('epub_file')
    candidates: list[Path] = []
    if epub_file:
        path = Path(str(epub_file))
        candidates.append(path if path.is_absolute() else root / path)
    work_id = str(record.get('work_id') or '').strip()
    if work_id:
        candidates.append(root / EPUB_DIRNAME / f'{work_id}.epub')
        candidates.append(root / f'{work_id}.epub')
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def load_import_source(
    path: str | Path,
    *,
    extract_dir: str | Path | None = None,
) -> tuple[list[dict[str, Any]], Path, Path | None]:
    """Load records from a JSONL file or import zip.

    Returns (records, bundle_root, cleanup_dir). cleanup_dir is set when a zip
    was extracted to a temporary directory that the caller should delete.
    """
    path = Path(path)
    if path.suffix.lower() == '.zip':
        dest = Path(extract_dir) if extract_dir else Path(tempfile.mkdtemp(prefix='ao3-import-'))
        extract_import_zip(path, dest)
        records = load_jsonl_records(find_manifest(dest))
        cleanup = None if extract_dir else dest
        return records, dest, cleanup

    records = load_jsonl_records(path)
    return records, path.parent, None
