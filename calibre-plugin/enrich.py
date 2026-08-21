# -*- coding: utf-8 -*-
"""Run ao3kit tag enrichment from the Calibre plugin via subprocess."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable


StatusCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]


class EnrichCancelled(Exception):
    """Raised when the user cancels tag enrichment."""


class EnrichHandle:
    """Allows the GUI to cancel a running enrich subprocess."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._cancelled = False

    def attach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._proc = proc
            if self._cancelled:
                self._kill_unlocked()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            self._kill_unlocked()

    def _kill_unlocked(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.kill()
        except OSError:
            pass


def _is_calibre_binary(path: str) -> bool:
    name = Path(path).name.lower()
    return 'calibre' in name


def _prefs_get(key: str) -> str:
    try:
        from calibre_plugins.ao3_scraper.prefs import prefs

        return (prefs.get(key) or '').strip()
    except Exception:
        return ''


def _which_via_login_shell(name: str) -> str:
    """Resolve ``name`` using the user's login shell PATH (GUI apps often lack it)."""
    shell = os.environ.get('SHELL') or '/bin/zsh'
    try:
        completed = subprocess.run(
            [shell, '-lc', f'command -v {name}'],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    if completed.returncode != 0:
        return ''
    return (completed.stdout or '').strip().splitlines()[0].strip()


def _pyenv_python_bins() -> list[str]:
    root = Path(os.environ.get('PYENV_ROOT') or (Path.home() / '.pyenv'))
    versions = root / 'versions'
    if not versions.is_dir():
        return []
    found: list[str] = []
    for version_dir in sorted(versions.iterdir(), reverse=True):
        for name in ('python3', 'python'):
            candidate = version_dir / 'bin' / name
            if candidate.is_file():
                found.append(str(candidate))
                break
    return found


def _candidate_pythons(project: Path) -> list[str]:
    found: list[str] = []

    def add(path: str, *, must_exist: bool = False) -> None:
        path = (path or '').strip()
        if not path or path in found or _is_calibre_binary(path):
            return
        if must_exist and not Path(path).is_file():
            return
        found.append(path)

    add(_prefs_get('ao3kit_python'))
    add(os.environ.get('AO3KIT_PYTHON', ''))

    for rel in ('.venv/bin/python3', '.venv/bin/python', 'venv/bin/python3', 'venv/bin/python'):
        add(str(project / rel), must_exist=True)

    for path in _pyenv_python_bins():
        add(path, must_exist=True)

    add(_which_via_login_shell('python3'))
    add(_which_via_login_shell('python'))
    add(shutil.which('python3') or '')
    add(shutil.which('python') or '')

    if not _is_calibre_binary(sys.executable or ''):
        add(sys.executable or '')

    return found


def _candidate_projects() -> list[Path]:
    projects: list[Path] = []
    configured = _prefs_get('ao3kit_project')
    if configured:
        projects.append(Path(configured).expanduser())

    env_home = os.environ.get('AO3KIT_HOME', '').strip()
    if env_home:
        home = Path(env_home).expanduser()
        projects.append(home)
        projects.append(home.parent)

    projects.append(Path.home() / 'emily' / 'ao3')
    projects.append(Path('/Users/blake/emily/ao3'))

    unique: list[Path] = []
    for path in projects:
        resolved = path.resolve() if path.exists() else path
        if resolved not in unique:
            unique.append(resolved)
    return unique


def find_ao3kit_project() -> Path | None:
    for project in _candidate_projects():
        if (project / 'ao3kit' / '__init__.py').is_file():
            return project
        if project.name == '.ao3kit' and (project.parent / 'ao3kit' / '__init__.py').is_file():
            return project.parent
    return None


def _enrich_env(project: Path) -> dict[str, str]:
    env = os.environ.copy()
    home = env.get('AO3KIT_HOME') or str(project / '.ao3kit')
    env['AO3KIT_HOME'] = home
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = (
        str(project) if not existing else f'{project}{os.pathsep}{existing}'
    )
    # Force line-buffered progress from the child when possible.
    env['PYTHONUNBUFFERED'] = '1'
    return env


def _python_can_import_ao3kit(python: str, project: Path) -> tuple[bool, str]:
    probe = [
        python,
        '-c',
        'import ao3kit, bs4, requests; import ao3kit.tags.clean',
    ]
    try:
        completed = subprocess.run(
            probe,
            cwd=str(project),
            env=_enrich_env(project),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or '').strip()
        return False, detail or f'exit {completed.returncode}'
    return True, ''


def _raise_if_cancelled(should_cancel: CancelCallback | None) -> None:
    if should_cancel and should_cancel():
        raise EnrichCancelled('Cancelled during tag simplification.')


def enrich_records_via_ao3kit(
    records: list[dict[str, Any]],
    *,
    on_status: StatusCallback | None = None,
    handle: EnrichHandle | None = None,
    should_cancel: CancelCallback | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Enrich records using ``python -m ao3kit tags enrich``.

    Streams verbose stderr lines to ``on_status`` so the UI can show progress.
    Returns ``(records, error)``. On failure, returns the original records and
    an error message so import can continue with raw tags.
    """
    if not records:
        return records, None

    if all(isinstance(record.get('cleaned'), dict) for record in records):
        if on_status:
            on_status('Tags already cleaned — skipping enrichment.')
        return records, None

    project = find_ao3kit_project()
    if project is None:
        return records, (
            'Could not find the ao3kit project (set plugin preference '
            'ao3kit_project to your checkout, e.g. /Users/blake/emily/ao3).'
        )

    pythons = _candidate_pythons(project)
    if not pythons:
        return records, (
            'No Python interpreter found. Set plugin preference ao3kit_python '
            'to a Python that has ao3kit deps installed (pip install -r requirements.txt).'
        )

    if on_status:
        on_status('Looking for a Python that can run ao3kit…')

    errors: list[str] = []
    working: str | None = None
    for python in pythons:
        _raise_if_cancelled(should_cancel)
        if on_status:
            on_status(f'Probing {python}')
        ok, detail = _python_can_import_ao3kit(python, project)
        if ok:
            working = python
            if on_status:
                on_status(f'Using {python}')
            break
        errors.append(f'{python}: {detail.splitlines()[-1] if detail else "failed"}')

    if working is None:
        hint = (
            'Install deps into a normal Python (not Calibre\'s), then set '
            'ao3kit_python in plugin settings. Tried:\n- '
            + '\n- '.join(errors[:8])
        )
        return records, hint

    tmp = tempfile.mkdtemp(prefix='ao3-enrich-')
    tmp_path = Path(tmp)
    try:
        inp = tmp_path / 'in.jsonl'
        out = tmp_path / 'out.jsonl'
        with inp.open('w', encoding='utf-8') as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + '\n')

        cmd = [
            working,
            '-u',  # unbuffered
            '-m',
            'ao3kit',
            'tags',
            'enrich',
            '--jsonl',
            str(inp),
            '-o',
            str(out),
            '--verbose',
        ]
        if on_status:
            on_status(f'Running: {" ".join(cmd)}')
            on_status(
                f'Simplifying tags for {len(records)} work(s). '
                'This may take a while on first run (AO3 tag fetches).'
            )

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(project),
                env=_enrich_env(project),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            return records, f'Tag simplification failed to start: {exc}'

        if handle is not None:
            handle.attach(proc)

        stderr_lines: list[str] = []

        def _read_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                line = line.rstrip('\n')
                stderr_lines.append(line)
                if on_status and line.strip():
                    on_status(line)

        reader = threading.Thread(target=_read_stderr, daemon=True)
        reader.start()

        while True:
            _raise_if_cancelled(should_cancel)
            try:
                returncode = proc.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue

        reader.join(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()

        if should_cancel and should_cancel():
            raise EnrichCancelled('Cancelled during tag simplification.')

        if returncode != 0:
            detail = '\n'.join(stderr_lines).strip()
            return records, (
                f'Tag simplification failed (exit {returncode}) via {working}:\n'
                f'{detail or "(no output)"}'
            )

        if not out.is_file():
            return records, 'Tag simplification produced no output file.'

        enriched: list[dict[str, Any]] = []
        for line in out.read_text(encoding='utf-8').splitlines():
            if line.strip():
                enriched.append(json.loads(line))
        if len(enriched) != len(records):
            return records, (
                f'Tag simplification returned {len(enriched)} records, '
                f'expected {len(records)}.'
            )
        return enriched, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
