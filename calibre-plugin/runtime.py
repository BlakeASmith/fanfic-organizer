# -*- coding: utf-8 -*-
"""Bundled ao3kit runtime for a Calibre plugin zip (Calibre-free).

GitHub releases ship ``AO3Scraper.zip`` with ``ao3kit/``, pure-Python
``vendor/``, and ``run_ao3kit.py``. Calibre's frozen Python ignores
``PYTHONPATH`` and cannot ``python -m``, so jobs run:

    calibre-debug -e run_ao3kit.py -- scrape …
"""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

PLUGIN_NAME = 'AO3 Scraper'
RUNTIME_DIRNAME = 'ao3_scraper_runtime'
BUNDLED_ZIP_PREFIXES = ('ao3kit/', 'vendor/')
BUNDLED_ZIP_FILES = ('run_ao3kit.py',)
NATIVE_SUFFIXES = {'.so', '.pyd', '.dylib', '.dll'}


def plugin_version_string(version: tuple[int, ...] | None = None) -> str:
    if version is None:
        try:
            from calibre_plugins.ao3_scraper import __version__ as installed
        except ImportError:
            try:
                from calibre_plugins.ao3_scraper.__init__ import __version__ as installed
            except ImportError:
                installed = (0, 0, 0)
        version = tuple(installed)
    return '.'.join(str(part) for part in version)


def zip_has_bundled_ao3kit(zip_path: Path) -> bool:
    if not zipfile.is_zipfile(zip_path):
        return False
    with zipfile.ZipFile(zip_path) as zf:
        names = {name.replace('\\', '/') for name in zf.namelist()}
    return 'ao3kit/__init__.py' in names and 'run_ao3kit.py' in names


def is_bundled_project(project: Path) -> bool:
    return (project / 'ao3kit' / '__init__.py').is_file() and (
        (project / 'vendor').is_dir() or (project / 'run_ao3kit.py').is_file()
    )


def looks_like_calibre_gui(python: str) -> bool:
    name = Path(python).name.lower()
    if name.endswith('.exe'):
        name = name[:-4]
    return name == 'calibre'


def looks_like_calibre_debug(python: str) -> bool:
    return 'calibre-debug' in Path(python).name.lower()


def looks_like_calibre_binary(python: str) -> bool:
    name = Path(python).name.lower()
    if name.endswith('.exe'):
        name = name[:-4]
    return 'calibre' in name


def plugin_ao3kit_command(
    python: str,
    args: list[str],
    *,
    launcher: str = '',
) -> list[str]:
    script = (launcher or os.environ.get('AO3KIT_LAUNCHER', '')).strip()
    extra = [str(part) for part in args]
    if script:
        if looks_like_calibre_binary(python):
            return [python, '-e', script, '--', *extra]
        return [python, '-u', script, *extra]
    return [python, '-u', '-m', 'ao3kit', *extra]


def find_calibre_debug(*, executable: str = '') -> str:
    exe = Path(executable or os.environ.get('AO3KIT_CALIBRE_DEBUG', '') or '')
    sibling_name = 'calibre-debug.exe' if os.name == 'nt' else 'calibre-debug'
    if looks_like_calibre_debug(str(exe)) and exe.is_file():
        return str(exe)
    if exe.is_file():
        sibling = exe.parent / sibling_name
        if sibling.is_file():
            return str(sibling)
    which = shutil.which('calibre-debug')
    if which:
        return which
    mac = Path('/Applications/calibre.app/Contents/MacOS/calibre-debug')
    if mac.is_file():
        return str(mac)
    program_files = os.environ.get('PROGRAMFILES', r'C:\Program Files')
    program_files_x86 = os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')
    for folder in (program_files, program_files_x86):
        win = Path(folder) / 'Calibre2' / 'calibre-debug.exe'
        if win.is_file():
            return str(win)
    return ''


def installed_plugin_zip() -> Path | None:
    try:
        from calibre.customize.ui import find_plugin

        plugin = find_plugin(PLUGIN_NAME)
        path = getattr(plugin, 'plugin_path', None) if plugin else None
        if path:
            candidate = Path(path)
            if candidate.is_file():
                return candidate
    except Exception:
        pass
    try:
        from calibre.utils.config import config_dir

        plugins = Path(config_dir) / 'plugins'
        for name in (f'{PLUGIN_NAME}.zip', 'AO3Scraper.zip'):
            candidate = plugins / name
            if candidate.is_file():
                return candidate
    except Exception:
        pass
    return None


def bundled_runtime_dir() -> Path | None:
    try:
        from calibre.utils.config import config_dir
    except Exception:
        return None
    return Path(config_dir) / 'plugins' / RUNTIME_DIRNAME


def _zip_stamp(zip_path: Path, version: str) -> str:
    stat = zip_path.stat()
    return f'{version}\n{stat.st_mtime_ns}\n{stat.st_size}\n'


def _safe_members(names: Iterable[str]) -> list[str]:
    members: list[str] = []
    for raw in names:
        name = raw.replace('\\', '/')
        if name.endswith('/') or not name:
            continue
        parts = Path(name).parts
        if '..' in parts or parts[:1] == ('/',):
            continue
        members.append(name)
    return members


def extract_bundled_runtime(
    zip_path: Path,
    dest: Path,
    *,
    version: str,
) -> Path:
    """Unpack ao3kit + vendor into ``dest``, keeping ``.ao3kit`` / ``.cache``."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    stamp_path = dest / 'PLUGIN_STAMP'
    wanted = _zip_stamp(zip_path, version)
    if (
        stamp_path.is_file()
        and stamp_path.read_text(encoding='utf-8') == wanted
        and (dest / 'ao3kit' / '__init__.py').is_file()
        and (dest / 'run_ao3kit.py').is_file()
    ):
        return dest

    with zipfile.ZipFile(zip_path) as zf:
        names = _safe_members(zf.namelist())
        to_extract = [
            name
            for name in names
            if name in BUNDLED_ZIP_FILES
            or name.startswith(BUNDLED_ZIP_PREFIXES)
        ]
        if 'ao3kit/__init__.py' not in to_extract:
            raise FileNotFoundError(f'{zip_path} has no bundled ao3kit')
        for prefix in ('ao3kit', 'vendor'):
            target = dest / prefix
            if target.is_dir():
                shutil.rmtree(target)
        for name in to_extract:
            info = zf.getinfo(name)
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open('wb') as out:
                shutil.copyfileobj(src, out)

    stamp_path.write_text(wanted, encoding='utf-8')
    return dest


def ensure_bundled_runtime(
    *,
    zip_path: Path | None = None,
    dest: Path | None = None,
    version: str | None = None,
) -> Path | None:
    zip_path = zip_path or installed_plugin_zip()
    dest = dest or bundled_runtime_dir()
    if zip_path is None or dest is None:
        return None
    if not zip_has_bundled_ao3kit(zip_path):
        return None
    return extract_bundled_runtime(
        zip_path,
        dest,
        version=version or plugin_version_string(),
    )
