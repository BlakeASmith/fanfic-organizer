"""XDG Base Directory locations for wranglekit / ao3kit user files.

See https://specifications.freedesktop.org/basedir/latest/

These are files that are **not** part of a Calibre library: settings, the tag
cache, jobs, the host-wide rate limiter, and the AO3 session cookie jar.

Layout (Unix defaults)::

    $XDG_CONFIG_HOME/wranglekit/     # ~/.config/wranglekit
        config.yaml, mappings.yaml, collections.yaml, rules/
    $XDG_CACHE_HOME/wranglekit/      # ~/.cache/wranglekit
        ao3_tag_cache.sqlite, tag-graph outputs, plugin-vendor/
    $XDG_STATE_HOME/wranglekit/      # ~/.local/state/wranglekit
        jobs/, ao3_rate.sqlite, ao3_session.json, python stamps
    $XDG_RUNTIME_DIR/wranglekit/     # Calibre restart lock (fallback: state/runtime)

Environment overrides (all optional)::

    AO3KIT_HOME          config directory
    AO3KIT_CONFIG_DIR    config directory
    AO3KIT_CACHE_DIR     cache directory
    AO3KIT_STATE_DIR     state directory
    AO3KIT_RUNTIME_DIR   runtime directory
    AO3KIT_JOBS_DIR, AO3KIT_RATE_DB, AO3KIT_SESSION_FILE, AO3KIT_TAG_CACHE,
    AO3KIT_CALIBRE_LOCK  individual files / job store
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

APP_NAME = "wranglekit"

CONFIG_FILENAME = "config.yaml"
MAPPINGS_FILENAME = "mappings.yaml"
COLLECTIONS_FILENAME = "collections.yaml"
SESSION_FILENAME = "ao3_session.json"
TAG_CACHE_FILENAME = "ao3_tag_cache.sqlite"
TAG_CACHE_LEGACY_JSON = "ao3_tag_cache.json"
RATE_DB_FILENAME = "ao3_rate.sqlite"
CALIBRE_LOCK_FILENAME = "calibre_restart.lock"


def _home() -> Path:
    return Path.home()


def _absolute_env(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None
    return path


def _win_roaming() -> Path:
    return Path(os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming"))


def _win_local() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local"))


def xdg_config_home() -> Path:
    env = _absolute_env("XDG_CONFIG_HOME")
    if env is not None:
        return env
    if sys.platform == "win32":
        return _win_roaming()
    return _home() / ".config"


def xdg_cache_home() -> Path:
    env = _absolute_env("XDG_CACHE_HOME")
    if env is not None:
        return env
    if sys.platform == "win32":
        return _win_local() / "cache"
    return _home() / ".cache"


def xdg_state_home() -> Path:
    env = _absolute_env("XDG_STATE_HOME")
    if env is not None:
        return env
    if sys.platform == "win32":
        return _win_local() / "state"
    return _home() / ".local" / "state"


def xdg_runtime_dir() -> Path:
    env = _absolute_env("XDG_RUNTIME_DIR")
    if env is not None:
        return env
    uid = getattr(os, "getuid", lambda: os.getpid())()
    return Path(os.environ.get("TMPDIR") or "/tmp") / f"{APP_NAME}-{uid}"


def _xdg_explicit(name: str) -> bool:
    """True when ``name`` is set to an absolute path (relative values are ignored)."""
    return _absolute_env(name) is not None


def _app_override(name: str) -> Path | None:
    """App-specific override; relative values are resolved (unlike XDG vars)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def config_dir() -> Path:
    env = _app_override("AO3KIT_CONFIG_DIR") or _app_override("AO3KIT_HOME")
    if env is not None:
        return env
    return xdg_config_home() / APP_NAME


def cache_dir() -> Path:
    env = _app_override("AO3KIT_CACHE_DIR")
    if env is not None:
        return env
    return xdg_cache_home() / APP_NAME


def state_dir() -> Path:
    env = _app_override("AO3KIT_STATE_DIR")
    if env is not None:
        return env
    return xdg_state_home() / APP_NAME


def runtime_dir() -> Path:
    env = _app_override("AO3KIT_RUNTIME_DIR")
    if env is not None:
        return env
    if _xdg_explicit("XDG_RUNTIME_DIR"):
        return xdg_runtime_dir() / APP_NAME
    return state_dir() / "runtime"


def jobs_dir() -> Path:
    env = _app_override("AO3KIT_JOBS_DIR")
    if env is not None:
        return env
    return state_dir() / "jobs"


def ensure_dir(path: Path) -> Path:
    """Create ``path`` (and parents) with mode 0700; do not chmod an existing dir."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def tag_cache_file() -> Path:
    env = os.environ.get("AO3KIT_TAG_CACHE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return cache_dir() / TAG_CACHE_FILENAME


def tag_cache_legacy_json() -> Path:
    return cache_dir() / TAG_CACHE_LEGACY_JSON


def rate_db_file() -> Path:
    env = os.environ.get("AO3KIT_RATE_DB", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return state_dir() / RATE_DB_FILENAME


def session_file() -> Path:
    env = os.environ.get("AO3KIT_SESSION_FILE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return state_dir() / SESSION_FILENAME


def calibre_lock_file() -> Path:
    env = os.environ.get("AO3KIT_CALIBRE_LOCK", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return runtime_dir() / CALIBRE_LOCK_FILENAME


def plugin_vendor_dir() -> Path:
    return cache_dir() / "plugin-vendor"


def python_stamp_file(project: Path) -> Path:
    digest = hashlib.sha256(os.fsencode(str(Path(project).resolve()))).hexdigest()[:16]
    return state_dir() / "python-stamps" / digest


def graph_html_file() -> Path:
    return cache_dir() / "tag-graph.html"


def graph_jsonl_file() -> Path:
    return cache_dir() / "tag_graph_works.jsonl"


def graph_json_file() -> Path:
    return cache_dir() / "tag-graph.json"


def graph_serve_stamp_file() -> Path:
    return cache_dir() / "tag-graph-serve.json"


def graph_inbox_dir() -> Path:
    return cache_dir() / "graph-inbox"


def warm_names_file() -> Path:
    return cache_dir() / "tag_warm_names.txt"


def warm_log_file() -> Path:
    return cache_dir() / "tag_warm.log"


def warm_status_file() -> Path:
    return cache_dir() / "tag_warm.status.json"
