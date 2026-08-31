# -*- coding: utf-8 -*-
"""Bundled ao3kit runtime for a Calibre plugin zip (Calibre-free).

GitHub releases ship ``FanFicOrganizer-<version>.zip`` with ``ao3kit/``,
pure-Python ``vendor/``, and ``run_ao3kit.py``. Calibre's frozen Python ignores
``PYTHONPATH`` and cannot ``python -m``, so jobs run:

    calibre-debug -e run_ao3kit.py -- scrape …
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Iterable

PLUGIN_NAME = 'Fanfic Organizer'
RUNTIME_DIRNAME = 'fanfic_organizer_runtime'
BUNDLED_ZIP_PREFIXES = ('ao3kit/', 'webcompile/', 'vendor/')
BUNDLED_ZIP_FILES = ('run_ao3kit.py',)
NATIVE_SUFFIXES = {'.so', '.pyd', '.dylib', '.dll'}


def plugin_version_string(version: tuple[int, ...] | None = None) -> str:
    if version is None:
        try:
            from calibre_plugins.fanfic_organizer import __version_display__ as display

            if display:
                return str(display)
        except Exception:
            pass
        try:
            from calibre_plugins.fanfic_organizer import __version__ as installed
        except ImportError:
            try:
                from calibre_plugins.fanfic_organizer.__init__ import __version__ as installed
            except ImportError:
                installed = (0, 0, 0)
        version = tuple(installed)
    shown = getattr(version, 'display', None)
    if shown:
        return str(shown)
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
        for name in (
            f'{PLUGIN_NAME}.zip',
            'fanfic-organizer.zip',
        ):
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
    """Unpack ao3kit + vendor into ``dest``; leave unrelated files in place."""
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
        for prefix in ('ao3kit', 'webcompile', 'vendor'):
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


def _prepend_sys_path(path: Path) -> None:
    text = str(path)
    if text and text not in sys.path:
        sys.path.insert(0, text)


def ensure_ao3kit_importable(*, project: Path | None = None) -> bool:
    """Make ``import ao3kit`` work in Calibre's Python when possible.

    Jobs normally run via ``calibre-debug -e run_ao3kit.py``. A few GUI
    actions (KOReader deploy) must call library helpers in-process against
    the live device object, so the checkout or extracted bundle is added to
    ``sys.path`` first.
    """
    try:
        import ao3kit  # noqa: F401

        return True
    except ImportError:
        pass

    roots: list[Path] = []
    if project is not None:
        roots.append(Path(project))
    else:
        try:
            from calibre_plugins.fanfic_organizer.enrich import find_ao3kit_project

            found = find_ao3kit_project()
            if found is not None:
                roots.append(found)
        except Exception:
            pass
        try:
            bundled = ensure_bundled_runtime()
            if bundled is not None:
                roots.append(bundled)
        except Exception:
            pass

    seen: set[str] = set()
    for root in roots:
        resolved = str(Path(root).resolve()) if Path(root).exists() else str(root)
        if resolved in seen:
            continue
        seen.add(resolved)
        root_path = Path(root)
        vendor = root_path / 'vendor'
        if vendor.is_dir():
            _prepend_sys_path(vendor)
        _prepend_sys_path(root_path)
        try:
            import ao3kit  # noqa: F401

            return True
        except ImportError:
            continue
    return False


def load_user_dirs():
    """Load ``user_dirs`` from the Calibre plugin package or a checkout file.

    Calibre imports the plugin from a zip, so ``Path(__file__).parent / 'user_dirs.py'``
    is not a real filesystem path. Pytest loads these modules from disk.
    """
    name = 'fanfic_organizer_user_dirs'
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    try:
        from calibre_plugins.fanfic_organizer import user_dirs as module
    except ImportError:
        path = Path(__file__).resolve().parent / 'user_dirs.py'
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
    sys.modules[name] = module
    return module
