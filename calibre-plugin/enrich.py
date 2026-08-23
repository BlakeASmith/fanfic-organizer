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
PollCallback = Callable[[], None]


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


def _runtime_options() -> dict[str, Any]:
    try:
        from calibre_plugins.ao3_scraper.prefs import plugin_runtime_settings
        from calibre_plugins.ao3_scraper.scrape_run import merge_plugin_settings

        return merge_plugin_settings({}, plugin_runtime_settings())
    except Exception:
        return {}


def _is_calibre_gui(path: str) -> bool:
    from calibre_plugins.ao3_scraper.runtime import looks_like_calibre_gui

    return looks_like_calibre_gui(path)


def _prefs_get(key: str) -> str:
    try:
        from calibre_plugins.ao3_scraper.prefs import prefs

        return (prefs.get(key) or '').strip()
    except Exception:
        return ''


def _prefs_set(key: str, value: str) -> None:
    try:
        from calibre_plugins.ao3_scraper.prefs import prefs

        prefs[key] = value
    except Exception:
        pass


def _python_stamp_path(project: Path) -> Path:
    return project / '.ao3kit' / 'python'


def _stamp_python(project: Path) -> str:
    stamp = _python_stamp_path(project)
    try:
        value = stamp.read_text(encoding='utf-8').strip()
    except OSError:
        return ''
    return value if value and Path(value).is_file() else ''


def _write_python_stamp(project: Path, python: str) -> None:
    stamp = _python_stamp_path(project)
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(python + '\n', encoding='utf-8')
    except OSError:
        pass
    _prefs_set('ao3kit_python', python)


def _which_via_login_shell(name: str) -> str:
    """Resolve ``name`` using the user's login shell PATH (GUI apps often lack it)."""
    shell = os.environ.get('SHELL') or '/bin/zsh'
    try:
        completed = subprocess.run(
            [shell, '-lc', f'command -v {name}'],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    if completed.returncode != 0:
        return ''
    return (completed.stdout or '').strip().splitlines()[0].strip()


def _is_prerelease_python(path: str) -> bool:
    lowered = path.lower()
    return any(token in lowered for token in ('rc', 'a0', 'b0', 'dev', 'alpha', 'beta'))


def _pyenv_python_bins() -> list[str]:
    root = Path(os.environ.get('PYENV_ROOT') or (Path.home() / '.pyenv'))
    versions = root / 'versions'
    if not versions.is_dir():
        return []
    stables: list[str] = []
    prereleases: list[str] = []
    for version_dir in sorted(versions.iterdir(), reverse=True):
        if not version_dir.is_dir():
            continue
        chosen = ''
        for name in ('python3', 'python'):
            candidate = version_dir / 'bin' / name
            if candidate.is_file():
                chosen = str(candidate)
                break
        if not chosen:
            continue
        if _is_prerelease_python(str(version_dir)):
            prereleases.append(chosen)
        else:
            stables.append(chosen)
    return stables + prereleases


def _candidate_pythons(project: Path) -> list[str]:
    found: list[str] = []

    def add(path: str, *, must_exist: bool = False) -> None:
        path = (path or '').strip()
        if not path or path in found or _is_calibre_gui(path):
            return
        if must_exist and not Path(path).is_file():
            return
        found.append(path)

    add(_prefs_get('ao3kit_python'))
    add(os.environ.get('AO3KIT_PYTHON', ''))
    add(_stamp_python(project), must_exist=True)

    from calibre_plugins.ao3_scraper.runtime import (
        find_calibre_debug,
        is_bundled_project,
    )

    if is_bundled_project(project):
        add(find_calibre_debug(executable=sys.executable or ''), must_exist=True)

    for name in ('.venv', 'venv'):
        venv_root = project / name
        if not any(venv_root.glob('lib/python*/site-packages/bs4/__init__.py')):
            continue
        add(str(venv_root / 'bin' / 'python3'), must_exist=True)
        add(str(venv_root / 'bin' / 'python'), must_exist=True)

    # Login-shell python3 is usually the one with ao3kit deps — try it before
    # enumerating every pyenv version (rc builds often fail and are slow).
    add(_which_via_login_shell('python3'))
    add(_which_via_login_shell('python'))
    add(shutil.which('python3') or '')
    add(shutil.which('python') or '')

    for path in _pyenv_python_bins():
        add(path, must_exist=True)

    if not _is_calibre_gui(sys.executable or ''):
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

    try:
        from calibre_plugins.ao3_scraper.runtime import ensure_bundled_runtime

        bundled = ensure_bundled_runtime()
        if bundled is not None:
            projects.append(bundled)
    except Exception:
        pass

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


def _launcher_path(project: Path) -> str:
    script = Path(project) / 'run_ao3kit.py'
    return str(script) if script.is_file() else ''


def _enrich_env(project: Path) -> dict[str, str]:
    env = os.environ.copy()
    home = env.get('AO3KIT_HOME') or str(project / '.ao3kit')
    env['AO3KIT_HOME'] = home
    paths = [str(project)]
    vendor = Path(project) / 'vendor'
    if vendor.is_dir():
        paths.insert(0, str(vendor))
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = (
        os.pathsep.join(paths)
        if not existing
        else os.pathsep.join([*paths, existing])
    )
    launcher = _launcher_path(project)
    if launcher:
        env['AO3KIT_LAUNCHER'] = launcher
    # Force line-buffered progress from the child when possible.
    env['PYTHONUNBUFFERED'] = '1'
    options = _runtime_options()
    username = str(options.get('username') or '').strip()
    password = str(options.get('password') or '')
    if username and password:
        env['AO3_USERNAME'] = username
        env['AO3_PASSWORD'] = password
    return env


def _import_probe_code(project: Path) -> str:
    """Calibre's frozen Python ignores PYTHONPATH; poke sys.path in-process."""
    chunks = ['import sys']
    vendor = Path(project) / 'vendor'
    if vendor.is_dir():
        chunks.append(f'sys.path.insert(0, {str(vendor)!r})')
    chunks.append(f'sys.path.insert(0, {str(project)!r})')
    chunks.append('import ao3kit, bs4, requests, yaml; import ao3kit.tags.clean')
    return '; '.join(chunks)


def _python_can_import_ao3kit(python: str, project: Path) -> tuple[bool, str]:
    from calibre_plugins.ao3_scraper.runtime import looks_like_calibre_debug

    probe = [python, '-c', _import_probe_code(project)]
    timeout = 12.0 if looks_like_calibre_debug(python) else 4.0
    try:
        completed = subprocess.run(
            probe,
            cwd=str(project),
            env=_enrich_env(project),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or '').strip()
        return False, detail or f'exit {completed.returncode}'
    return True, ''


def _raise_if_cancelled(
    should_cancel: CancelCallback | None,
    message: str = 'Cancelled during tag simplification.',
) -> None:
    if should_cancel and should_cancel():
        raise EnrichCancelled(message)


def _redact_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for part in argv:
        if hide_next:
            redacted.append('***')
            hide_next = False
            continue
        redacted.append(part)
        if part in ('--password', '-p'):
            hide_next = True
    return redacted


def resolve_ao3kit_runtime(
    *,
    on_status: StatusCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> tuple[Path | None, str | None, str | None]:
    """Find bundled or checkout ao3kit and a Python that can import it.

    Returns ``(project, python, error)``. On success ``error`` is None.
    """
    project = find_ao3kit_project()
    if project is None:
        return None, None, (
            'Could not find ao3kit. Install AO3Scraper.zip from GitHub Releases '
            '(Calibre → Preferences → Plugins → Load plugin from file), or set '
            'Project path in plugin settings to a git checkout.'
        )

    pythons = _candidate_pythons(project)
    if not pythons:
        return project, None, (
            'No Python interpreter found. The plugin uses Calibre\'s '
            'calibre-debug when you install the GitHub zip. Otherwise set '
            'Python in plugin settings to a python3 with ao3kit deps.'
        )

    remembered = _prefs_get('ao3kit_python') or _stamp_python(project)
    if remembered:
        pythons = [remembered] + [p for p in pythons if p != remembered]

    if on_status:
        on_status('Looking for a Python that can run ao3kit…')

    errors: list[str] = []
    for python in pythons:
        _raise_if_cancelled(should_cancel, 'Cancelled while locating ao3kit.')
        if on_status:
            label = 'saved interpreter' if python == remembered else 'probe'
            on_status(f'Trying {python} ({label})')
        ok, detail = _python_can_import_ao3kit(python, project)
        if ok:
            if on_status:
                on_status(f'Using {python}')
            _write_python_stamp(project, python)
            return project, python, None
        errors.append(f'{python}: {detail.splitlines()[-1] if detail else "failed"}')

    hint = (
        'Could not run ao3kit. Re-install AO3Scraper.zip from GitHub Releases, '
        'or set Python in plugin settings to a python3 with deps installed. '
        'Tried:\n- '
        + '\n- '.join(errors[:8])
    )
    return project, None, hint


def run_ao3kit_command(
    python: str,
    project: Path,
    args: list[str],
    *,
    on_status: StatusCallback | None = None,
    on_poll: PollCallback | None = None,
    handle: EnrichHandle | None = None,
    should_cancel: CancelCallback | None = None,
    cancel_message: str = 'Cancelled.',
    log_setup: bool = False,
) -> tuple[int, str, str]:
    """Run ``python -m ao3kit <args>`` (or calibre-debug -e for the bundle)."""
    from calibre_plugins.ao3_scraper.runtime import plugin_ao3kit_command

    cmd = plugin_ao3kit_command(python, args, launcher=_launcher_path(project))
    if on_status and log_setup:
        on_status(f'Running: {" ".join(_redact_argv(cmd))}')

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
        return 127, '', f'ao3kit failed to start: {exc}'

    if handle is not None:
        handle.attach(proc)

    stdout_chunks: list[str] = []
    stderr_lines: list[str] = []

    def _read_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_chunks.append(line)

    def _read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip('\n')
            stderr_lines.append(line)
            if on_status and line.strip():
                on_status(line)

    stdout_reader = threading.Thread(target=_read_stdout, daemon=True)
    stderr_reader = threading.Thread(target=_read_stderr, daemon=True)
    stdout_reader.start()
    stderr_reader.start()

    while True:
        _raise_if_cancelled(should_cancel, cancel_message)
        if on_poll:
            on_poll()
        try:
            returncode = proc.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            continue

    stdout_reader.join(timeout=5)
    stderr_reader.join(timeout=5)
    if proc.stdout is not None:
        proc.stdout.close()
    if proc.stderr is not None:
        proc.stderr.close()

    if on_poll:
        on_poll()
    _raise_if_cancelled(should_cancel, cancel_message)
    return returncode, ''.join(stdout_chunks), '\n'.join(stderr_lines)


def run_ao3kit(
    args: list[str],
    *,
    on_status: StatusCallback | None = None,
    on_poll: PollCallback | None = None,
    handle: EnrichHandle | None = None,
    should_cancel: CancelCallback | None = None,
    cancel_message: str = 'Cancelled.',
    log_setup: bool = False,
) -> tuple[int, str, str]:
    """Resolve ao3kit and run ``python -m ao3kit <args>``.

    Returns ``(returncode, stdout, stderr)``. On setup failure, returncode is
    127 and stderr holds the error message.
    """
    project, python, error = resolve_ao3kit_runtime(
        on_status=on_status if log_setup else None,
        should_cancel=should_cancel,
    )
    if error or project is None or python is None:
        return 127, '', error or 'Could not run ao3kit.'
    return run_ao3kit_command(
        python,
        project,
        args,
        on_status=on_status,
        on_poll=on_poll,
        handle=handle,
        should_cancel=should_cancel,
        cancel_message=cancel_message,
        log_setup=log_setup,
    )


def enrich_records_via_ao3kit(
    records: list[dict[str, Any]],
    *,
    on_status: StatusCallback | None = None,
    handle: EnrichHandle | None = None,
    should_cancel: CancelCallback | None = None,
    force: bool = False,
    collections_recompute: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    """Enrich records using ``python -m ao3kit tags enrich``.

    Streams verbose stderr lines to ``on_status`` so the UI can show progress.
    Returns ``(records, error)``. On failure, returns the original records and
    an error message so import can continue with raw tags.

    When ``force`` is True, existing ``cleaned`` payloads are stripped so
    simplification runs again.

    When ``collections_recompute`` is True, runs ``tags collections`` so
    membership is computed from rules and hand-added collections become pins.
    That path does not fetch AO3 or rewrite tags.
    """
    if not records:
        return records, None

    if force and not collections_recompute:
        records = [dict(record) for record in records]
        for record in records:
            record.pop('cleaned', None)

    if (
        not collections_recompute
        and all(isinstance(record.get('cleaned'), dict) for record in records)
    ):
        if on_status:
            on_status('Tags already cleaned — skipping enrichment.')
        return records, None

    project, working, error = resolve_ao3kit_runtime(
        should_cancel=should_cancel,
    )
    if error or project is None or working is None:
        return records, error or 'Could not run ao3kit.'

    tmp = tempfile.mkdtemp(prefix='ao3-enrich-')
    tmp_path = Path(tmp)
    try:
        inp = tmp_path / 'in.jsonl'
        out = tmp_path / 'out.jsonl'
        with inp.open('w', encoding='utf-8') as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + '\n')

        if on_status:
            if collections_recompute:
                on_status(
                    f'Computing collections for {len(records)} work(s) from '
                    'rules. Tags are left as they are (no AO3 tag lookup).'
                )
            else:
                on_status(
                    f'Simplifying tags, fandoms, and relationships for '
                    f'{len(records)} work(s). '
                    'This may take a while on first run (AO3 tag fetches).'
                )

        from calibre_plugins.ao3_scraper.scrape_run import (
            build_collections_argv,
            build_enrich_argv,
        )

        argv = (
            build_collections_argv(str(inp), str(out), _runtime_options())
            if collections_recompute
            else build_enrich_argv(str(inp), str(out), _runtime_options())
        )
        returncode, _stdout, stderr = run_ao3kit_command(
            working,
            project,
            argv,
            on_status=on_status,
            handle=handle,
            should_cancel=should_cancel,
            cancel_message=(
                'Cancelled while recomputing collections.'
                if collections_recompute
                else 'Cancelled during tag simplification.'
            ),
        )

        if returncode != 0:
            detail = (stderr or '').strip()
            failed = (
                'Collection recompute failed'
                if collections_recompute
                else 'Tag simplification failed'
            )
            return records, (
                f'{failed} (exit {returncode}) via {working}:\n'
                f'{detail or "(no output)"}'
            )

        if not out.is_file():
            return records, (
                'Collection recompute produced no output file.'
                if collections_recompute
                else 'Tag simplification produced no output file.'
            )

        enriched: list[dict[str, Any]] = []
        for line in out.read_text(encoding='utf-8').splitlines():
            if line.strip():
                enriched.append(json.loads(line))
        if len(enriched) != len(records):
            kind = (
                'Collection recompute'
                if collections_recompute
                else 'Tag simplification'
            )
            return records, (
                f'{kind} returned {len(enriched)} records, '
                f'expected {len(records)}.'
            )
        return enriched, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
