# -*- coding: utf-8 -*-
"""XDG user directories for the Calibre plugin (no Calibre, no ao3kit).

Keep in sync with ``ao3kit.paths``. Used when Calibre's Python cannot import
ao3kit. Pytest / a checkout can import ``ao3kit.paths`` instead.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

APP_NAME = 'wranglekit'


def _absolute_env(name: str) -> Path | None:
    raw = os.environ.get(name, '').strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None
    return path


def _app_override(name: str) -> Path | None:
    raw = os.environ.get(name, '').strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _home() -> Path:
    return Path.home()


def _win_roaming() -> Path:
    return Path(os.environ.get('APPDATA') or (_home() / 'AppData' / 'Roaming'))


def _win_local() -> Path:
    return Path(os.environ.get('LOCALAPPDATA') or (_home() / 'AppData' / 'Local'))


def xdg_config_home() -> Path:
    env = _absolute_env('XDG_CONFIG_HOME')
    if env is not None:
        return env
    if sys.platform == 'win32':
        return _win_roaming()
    return _home() / '.config'


def xdg_cache_home() -> Path:
    env = _absolute_env('XDG_CACHE_HOME')
    if env is not None:
        return env
    if sys.platform == 'win32':
        return _win_local() / 'cache'
    return _home() / '.cache'


def xdg_state_home() -> Path:
    env = _absolute_env('XDG_STATE_HOME')
    if env is not None:
        return env
    if sys.platform == 'win32':
        return _win_local() / 'state'
    return _home() / '.local' / 'state'


def config_dir(project: Path | None = None) -> Path:
    env = _app_override('AO3KIT_CONFIG_DIR') or _app_override('AO3KIT_HOME')
    if env is not None:
        return env
    return xdg_config_home() / APP_NAME


def cache_dir(project: Path | None = None) -> Path:
    env = _app_override('AO3KIT_CACHE_DIR')
    if env is not None:
        return env
    return xdg_cache_home() / APP_NAME


def state_dir(project: Path | None = None) -> Path:
    env = _app_override('AO3KIT_STATE_DIR')
    if env is not None:
        return env
    return xdg_state_home() / APP_NAME


def jobs_dir(project: Path | None = None) -> Path:
    env = _app_override('AO3KIT_JOBS_DIR')
    if env is not None:
        return env
    return state_dir(project) / 'jobs'


def python_stamp_file(project: Path) -> Path:
    digest = hashlib.sha256(
        os.fsencode(str(Path(project).resolve()))
    ).hexdigest()[:16]
    return state_dir(project) / 'python-stamps' / digest


def graph_inbox_dir(project: Path | None = None) -> Path:
    return cache_dir(project) / 'graph-inbox'


def graph_jsonl_file(project: Path | None = None) -> Path:
    return cache_dir(project) / 'tag_graph_works.jsonl'


def graph_html_file(project: Path | None = None) -> Path:
    return cache_dir(project) / 'tag-graph.html'


def graph_serve_stamp_file(project: Path | None = None) -> Path:
    return cache_dir(project) / 'tag-graph-serve.json'


def warm_names_file(project: Path | None = None) -> Path:
    return cache_dir(project) / 'tag_warm_names.txt'


def warm_log_file(project: Path | None = None) -> Path:
    return cache_dir(project) / 'tag_warm.log'


def warm_status_file(project: Path | None = None) -> Path:
    return cache_dir(project) / 'tag_warm.status.json'


def _from_ao3kit():
    try:
        from ao3kit import paths
    except ImportError:
        return None
    return paths


def resolve_jobs_dir(project: Path | None = None) -> Path:
    impl = _from_ao3kit()
    if impl is not None:
        return impl.jobs_dir()
    return jobs_dir(project)


def resolve_cache_dir(project: Path | None = None) -> Path:
    impl = _from_ao3kit()
    if impl is not None:
        return impl.cache_dir()
    return cache_dir(project)


def resolve_python_stamp_file(project: Path) -> Path:
    impl = _from_ao3kit()
    if impl is not None:
        return impl.python_stamp_file(project)
    return python_stamp_file(project)


def resolve_graph_inbox_dir(project: Path | None = None) -> Path:
    impl = _from_ao3kit()
    if impl is not None:
        return impl.graph_inbox_dir()
    return graph_inbox_dir(project)


def resolve_graph_jsonl_file(project: Path | None = None) -> Path:
    impl = _from_ao3kit()
    if impl is not None:
        return impl.graph_jsonl_file()
    return graph_jsonl_file(project)


def resolve_graph_html_file(project: Path | None = None) -> Path:
    impl = _from_ao3kit()
    if impl is not None:
        return impl.graph_html_file()
    return graph_html_file(project)


def resolve_graph_serve_stamp_file(project: Path | None = None) -> Path:
    impl = _from_ao3kit()
    if impl is not None:
        return impl.graph_serve_stamp_file()
    return graph_serve_stamp_file(project)


def resolve_warm_names_file(project: Path | None = None) -> Path:
    impl = _from_ao3kit()
    if impl is not None:
        return impl.warm_names_file()
    return warm_names_file(project)


def resolve_warm_log_file(project: Path | None = None) -> Path:
    impl = _from_ao3kit()
    if impl is not None:
        return impl.warm_log_file()
    return warm_log_file(project)


def resolve_warm_status_file(project: Path | None = None) -> Path:
    impl = _from_ao3kit()
    if impl is not None:
        return impl.warm_status_file()
    return warm_status_file(project)
