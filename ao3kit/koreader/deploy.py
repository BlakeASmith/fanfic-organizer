"""Build and deploy fanfic-organizer collections metadata to KOReader on Kobo."""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any, Iterable

COLLECTIONS_JSON_NAME = "fanfic.collections.json"
KOPLUGIN_DIRNAME = "fanficcollections.koplugin"
DEFAULT_KOREADER_SUBDIR = ".adds/koreader"
COLLECTIONS_COLUMN = "#collections"


def _strip_collections(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [raw]
    else:
        try:
            values = list(raw)
        except TypeError:
            values = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value).strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def build_collections_index(db: Any, device: Any) -> list[dict[str, Any]]:
    """Build the collections-only JSON from books on the connected device."""
    entries: list[dict[str, Any]] = []
    books_method = getattr(device, "books", None)
    if not callable(books_method):
        return entries
    for book in books_method(main_memory=True):
        db_id = getattr(book, "db_id", None)
        lpath = getattr(book, "lpath", None)
        if not db_id or not lpath:
            continue
        mi = db.get_metadata(db_id, get_user_categories=True)
        collections = _strip_collections(mi.get(COLLECTIONS_COLUMN))
        if not collections:
            collections = _strip_collections(mi.get("collections"))
        entry: dict[str, Any] = {
            "lpath": str(lpath).replace("\\", "/"),
            "collections": collections,
        }
        title = getattr(mi, "title", None)
        if title:
            entry["title"] = str(title)
        authors = getattr(mi, "authors", None)
        if authors:
            entry["authors"] = [str(author) for author in authors if str(author).strip()]
        entries.append(entry)
    entries.sort(key=lambda row: (row.get("lpath") or "").casefold())
    return entries


def build_collections_index_from_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build deploy JSON from plain dict rows (tests / CLI helpers)."""
    entries: list[dict[str, Any]] = []
    for row in rows:
        lpath = str(row.get("lpath") or "").strip().replace("\\", "/")
        if not lpath:
            continue
        collections = _strip_collections(row.get("collections"))
        entry: dict[str, Any] = {"lpath": lpath, "collections": collections}
        title = row.get("title")
        if title:
            entry["title"] = str(title)
        authors = row.get("authors")
        if authors:
            entry["authors"] = [str(author) for author in authors if str(author).strip()]
        entries.append(entry)
    entries.sort(key=lambda row: row["lpath"].casefold())
    return entries


def koreader_roots(device: Any, *, subdir: str = DEFAULT_KOREADER_SUBDIR) -> list[Path]:
    """Return absolute KOReader roots on the device (main memory and SD when set)."""
    roots: list[Path] = []
    seen: set[str] = set()
    for attr in ("_main_prefix", "_card_a_prefix"):
        prefix = getattr(device, attr, None)
        if not prefix:
            continue
        root = Path(str(prefix).rstrip("/\\")) / subdir.lstrip("/")
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)
    return roots


def atomic_write_json(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def deploy_metadata(
    device_root: Path,
    entries: list[dict[str, Any]],
    *,
    filename: str = COLLECTIONS_JSON_NAME,
) -> Path:
    """Write ``fanfic.collections.json`` under ``device_root/cache/``."""
    target = device_root / "cache" / filename
    atomic_write_json(target, entries)
    return target


def install_plugin(device_root: Path, source: Path) -> Path:
    """Copy the bundled KOReader plugin into ``device_root/plugins/``."""
    if not source.is_dir():
        raise FileNotFoundError(f"KOReader plugin source not found: {source}")
    dest = device_root / "plugins" / KOPLUGIN_DIRNAME
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    return dest


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "addons" / "koreader-collections" / KOPLUGIN_DIRNAME
        if candidate.is_dir():
            return parent
    return None


def _extract_plugin_from_zip(zip_path: Path, dest: Path) -> Path | None:
    prefix = f"resources/koreader/{KOPLUGIN_DIRNAME}/"
    if not zipfile.is_zipfile(zip_path):
        return None
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        members = [
            name
            for name in zf.namelist()
            if name.replace("\\", "/").startswith(prefix)
        ]
        if not members:
            return None
        for name in members:
            rel = name.replace("\\", "/")[len(prefix) :]
            if not rel or rel.endswith("/"):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    if not (dest / "main.lua").is_file():
        return None
    return dest


def cached_plugin_from_zip(zip_path: Path) -> Path | None:
    """Extract the bundled KOReader plugin from a release zip into XDG cache."""
    from ao3kit.paths import cache_dir

    cache_root = cache_dir() / "koreader"
    plugin_dir = cache_root / KOPLUGIN_DIRNAME
    stamp = cache_root / "plugin.stamp"
    try:
        stat = zip_path.stat()
        stamp_value = f"{stat.st_mtime_ns}:{stat.st_size}:{zip_path}"
    except OSError:
        return None
    if (
        stamp.is_file()
        and stamp.read_text(encoding="utf-8") == stamp_value
        and (plugin_dir / "main.lua").is_file()
    ):
        return plugin_dir
    extracted = _extract_plugin_from_zip(zip_path, plugin_dir)
    if extracted is None:
        return None
    stamp.write_text(stamp_value, encoding="utf-8")
    return extracted


def resolve_bundled_plugin_source(
    *,
    plugin_zip: Path | None = None,
    checkout_root: Path | None = None,
) -> Path | None:
    """Locate ``fanficcollections.koplugin`` in a git checkout or release zip."""
    if checkout_root is None:
        checkout_root = _repo_root()
    if checkout_root is not None:
        dev = checkout_root / "addons" / "koreader-collections" / KOPLUGIN_DIRNAME
        if dev.is_dir() and (dev / "main.lua").is_file():
            return dev
    if plugin_zip is not None and plugin_zip.is_file():
        return cached_plugin_from_zip(plugin_zip)
    return None


def deploy_to_device(
    db: Any,
    device: Any,
    *,
    plugin_source: Path | None = None,
    install_koplugin: bool = True,
    koreader_subdir: str = DEFAULT_KOREADER_SUBDIR,
) -> dict[str, Any]:
    """Install the KOReader plugin (optional) and write collections JSON on the device."""
    entries = build_collections_index(db, device)
    roots = koreader_roots(device, subdir=koreader_subdir)
    if not roots:
        raise RuntimeError("Could not locate KOReader folder on the connected device.")
    written: list[str] = []
    installed: list[str] = []
    for root in roots:
        path = deploy_metadata(root, entries)
        written.append(str(path))
        if install_koplugin and plugin_source is not None:
            dest = install_plugin(root, plugin_source)
            installed.append(str(dest))
    return {
        "books": len(entries),
        "collections_json": written,
        "plugin_installed": installed,
    }
