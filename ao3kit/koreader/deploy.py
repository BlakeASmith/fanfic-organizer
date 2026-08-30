"""Build and deploy fanfic-organizer collections metadata to KOReader on Kobo."""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from ao3kit.koreader.detect import (
    DEFAULT_KOREADER_SUBDIR,
    KoreaderMount,
    detect_koreader_mounts,
    koreader_deployable,
)

COLLECTIONS_JSON_NAME = "fanfic.collections.json"
KOPLUGIN_DIRNAME = "fanficcollections.koplugin"
DEFAULT_KOREADER_SUBDIR = DEFAULT_KOREADER_SUBDIR
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


def library_book_id(book: Any) -> int | None:
    """Return the open-library book id Calibre matched onto a device book.

    After connect, ``gui.set_books_in_library`` stores that id on
    ``application_id`` (Kobo and most drivers). ``db_id`` is only set by a
    few drivers (e.g. Sony). Prefer ``application_id``.
    """
    for attr in ("application_id", "db_id"):
        raw = getattr(book, attr, None)
        if raw is None or raw is False:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value:
            return value
    return None


def _storage_label(oncard: str | None) -> str:
    if oncard is None:
        return "main"
    return str(oncard)


def _dedupe_books(books: Iterable[Any], *, seen: set[str] | None = None) -> Iterable[Any]:
    if seen is None:
        seen = set()
    for book in books:
        if not book:
            continue
        lpath = getattr(book, "lpath", None)
        key = str(lpath).replace("\\", "/") if lpath else ""
        if key:
            if key in seen:
                continue
            seen.add(key)
        yield book


def _iter_booklists(booklists: Iterable[Any] | None) -> Iterable[tuple[Any, str]]:
    """Yield ``(book, storage)`` from GUI ``booklists()`` (main, card a, card b)."""
    if not booklists:
        return
    seen: set[str] = set()
    labels = ("main", "carda", "cardb")
    for idx, booklist in enumerate(booklists):
        if not booklist:
            continue
        storage = labels[idx] if idx < len(labels) else "main"
        for book in _dedupe_books(booklist, seen=seen):
            yield book, storage


def _iter_device_books(device: Any) -> Iterable[tuple[Any, str]]:
    """Yield ``(book, storage)`` from main memory and cards via ``books(oncard=…)``.

    Stock Calibre device drivers (including KOBOTOUCH) take ``oncard`` /
    ``end_session`` — never ``main_memory``. Match ``gui2.device``: scan
    main, carda, and cardb.

    Prefer ``gui.booklists()`` when available: those lists already have
    ``application_id`` from library matching. A fresh ``device.books()``
    call does not.
    """
    books_method = getattr(device, "books", None)
    if not callable(books_method):
        return
    seen: set[str] = set()
    locations = ((None, False), ("carda", False), ("cardb", True))
    for oncard, end_session in locations:
        try:
            booklist = books_method(oncard=oncard, end_session=end_session)
        except TypeError:
            # Non-Calibre stubs may expose a zero-arg books(); only try once.
            if oncard is not None:
                continue
            try:
                booklist = books_method()
            except TypeError:
                return
        if not booklist:
            continue
        storage = _storage_label(oncard)
        for book in _dedupe_books(booklist, seen=seen):
            yield book, storage


def build_collections_index(
    db: Any,
    device: Any,
    *,
    booklists: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the collections-only JSON from books on the connected device.

    Pass Calibre ``gui.booklists()`` when calling from the plugin so matched
    ``application_id`` values are used. Falls back to ``device.books()``.
    """
    entries: list[dict[str, Any]] = []
    source = (
        _iter_booklists(booklists)
        if booklists is not None
        else _iter_device_books(device)
    )
    for book, storage in source:
        db_id = library_book_id(book)
        lpath = getattr(book, "lpath", None)
        if not db_id or not lpath:
            continue
        mi = db.get_metadata(db_id, index_is_id=True, get_user_categories=True)
        collections = _strip_collections(mi.get(COLLECTIONS_COLUMN))
        if not collections:
            collections = _strip_collections(mi.get("collections"))
        entry: dict[str, Any] = {
            "lpath": str(lpath).replace("\\", "/"),
            "collections": collections,
            "storage": storage,
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
        storage = row.get("storage")
        if storage:
            entry["storage"] = str(storage)
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
    """Return absolute KOReader data roots on a compatible device."""
    roots: list[Path] = []
    for mount in detect_koreader_mounts(device, koreader_subdir=subdir):
        if mount.transport == "mtp":
            roots.append(Path(mount.storage_prefix) / Path(*mount.mtp_koreader_parts))
        else:
            roots.append(mount.koreader_root)
    return roots


def _mtp_put_file(
    device: Any,
    storage: Any,
    path_parts: tuple[str, ...],
    data: bytes,
    *,
    replace: bool = True,
) -> str:
    if not path_parts:
        raise ValueError("path_parts required")
    parent = device.ensure_parent(storage, path_parts)
    stream = BytesIO(data)
    device.put_file(parent, path_parts[-1], stream, len(data), replace=replace)
    return "/".join(path_parts)


def deploy_metadata_mtp(
    mount: KoreaderMount,
    entries: list[dict[str, Any]],
    *,
    filename: str = COLLECTIONS_JSON_NAME,
) -> str:
    """Write ``fanfic.collections.json`` on an MTP-mounted KOReader folder."""
    if mount.mtp_device is None or mount.mtp_storage is None:
        raise ValueError("MTP mount is missing device or storage")
    path_parts = mount.mtp_koreader_parts + ("cache", filename)
    payload = json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
    return _mtp_put_file(
        mount.mtp_device,
        mount.mtp_storage,
        path_parts,
        payload.encode("utf-8"),
    )


def install_plugin_mtp(mount: KoreaderMount, source: Path) -> str:
    """Copy the bundled KOReader plugin onto an MTP-mounted device."""
    if mount.mtp_device is None or mount.mtp_storage is None:
        raise ValueError("MTP mount is missing device or storage")
    if not source.is_dir():
        raise FileNotFoundError(f"KOReader plugin source not found: {source}")
    base = mount.mtp_koreader_parts + ("plugins", KOPLUGIN_DIRNAME)
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(source).parts
        _mtp_put_file(
            mount.mtp_device,
            mount.mtp_storage,
            base + rel,
            path.read_bytes(),
        )
    return "/".join(base)


def deploy_metadata(
    koreader_root: Path,
    entries: list[dict[str, Any]],
    *,
    filename: str = COLLECTIONS_JSON_NAME,
) -> Path:
    """Write ``fanfic.collections.json`` under ``koreader_root/cache/``."""
    target = koreader_root / "cache" / filename
    atomic_write_json(target, entries)
    return target


def install_plugin(koreader_root: Path, source: Path) -> Path:
    """Copy the bundled KOReader plugin into ``koreader_root/plugins/``."""
    if not source.is_dir():
        raise FileNotFoundError(f"KOReader plugin source not found: {source}")
    dest = koreader_root / "plugins" / KOPLUGIN_DIRNAME
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    return dest


def atomic_write_json(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


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
    booklists: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Install the KOReader plugin (optional) and write collections JSON on the device."""
    entries = build_collections_index(db, device, booklists=booklists)
    mounts = detect_koreader_mounts(device, koreader_subdir=koreader_subdir)
    written: list[str] = []
    installed: list[str] = []
    for mount in mounts:
        if mount.transport == "mtp":
            path = deploy_metadata_mtp(mount, entries)
            written.append(path)
            if install_koplugin and plugin_source is not None:
                dest = install_plugin_mtp(mount, plugin_source)
                installed.append(dest)
            continue
        path = deploy_metadata(mount.koreader_root, entries)
        written.append(str(path))
        if install_koplugin and plugin_source is not None:
            dest = install_plugin(mount.koreader_root, plugin_source)
            installed.append(str(dest))
    return {
        "books": len(entries),
        "collections_json": written,
        "plugin_installed": installed,
        "koreader_kind": mounts[0].kind if mounts else "",
    }
