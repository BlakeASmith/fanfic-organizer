"""Locate the companion Tampermonkey userscript."""

from __future__ import annotations

import zipfile
from pathlib import Path

USERSCRIPT_NAME = "fanfic-organizer-webcompile.user.js"
ADDON_REL = Path("addons") / "webcompile-tampermonkey" / USERSCRIPT_NAME
ZIP_PREFIX = "resources/webcompile/"


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ADDON_REL
        if candidate.is_file():
            return parent
    return None


def resolve_userscript(
    *,
    plugin_zip: Path | None = None,
    checkout_root: Path | None = None,
    plugin_dir: Path | None = None,
) -> Path | None:
    """Return path to the ``.user.js`` in checkout, plugin resources, or zip cache."""
    if checkout_root is None:
        checkout_root = _repo_root()
    if checkout_root is not None:
        dev = checkout_root / ADDON_REL
        if dev.is_file():
            return dev
    if plugin_dir is not None:
        local = Path(plugin_dir) / "resources" / "webcompile" / USERSCRIPT_NAME
        if local.is_file():
            return local
    # calibre-plugin/resources when this package lives in a checkout.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "calibre-plugin" / "resources" / "webcompile" / USERSCRIPT_NAME
        if candidate.is_file():
            return candidate
        bundled = parent / "resources" / "webcompile" / USERSCRIPT_NAME
        if bundled.is_file() and (parent / "plugin-import-name-fanfic_organizer.txt").is_file():
            return bundled
    if plugin_zip is not None and plugin_zip.is_file():
        return _cached_from_zip(plugin_zip)
    return None


def _cached_from_zip(zip_path: Path) -> Path | None:
    from ao3kit.paths import cache_dir

    cache_root = cache_dir() / "webcompile"
    dest = cache_root / USERSCRIPT_NAME
    stamp = cache_root / "userscript.stamp"
    try:
        stat = zip_path.stat()
        stamp_value = f"{stat.st_mtime_ns}:{stat.st_size}:{zip_path}"
    except OSError:
        return None
    if (
        stamp.is_file()
        and stamp.read_text(encoding="utf-8") == stamp_value
        and dest.is_file()
    ):
        return dest
    if not zipfile.is_zipfile(zip_path):
        return None
    member = f"{ZIP_PREFIX}{USERSCRIPT_NAME}"
    with zipfile.ZipFile(zip_path) as zf:
        names = {n.replace("\\", "/") for n in zf.namelist()}
        if member not in names:
            return None
        cache_root.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, dest.open("wb") as out:
            out.write(src.read())
    stamp.write_text(stamp_value, encoding="utf-8")
    return dest


def userscript_text(
    *,
    plugin_zip: Path | None = None,
    checkout_root: Path | None = None,
) -> str | None:
    path = resolve_userscript(plugin_zip=plugin_zip, checkout_root=checkout_root)
    if path is None:
        return None
    return path.read_text(encoding="utf-8")
