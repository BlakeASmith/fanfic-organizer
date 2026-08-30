"""Tests for KOReader collections deploy helpers."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import makeplugin
import pytest
from ao3kit.koreader.deploy import (
    COLLECTIONS_JSON_NAME,
    KOPLUGIN_DIRNAME,
    atomic_write_json,
    build_collections_index,
    build_collections_index_from_rows,
    cached_plugin_from_zip,
    deploy_metadata,
    deploy_to_device,
    install_plugin,
    koreader_roots,
    resolve_bundled_plugin_source,
)
from ao3kit.koreader.detect import (
    KoreaderDetectionError,
    detect_koreader_mounts,
    koreader_deployable,
)


class _Meta:
    def __init__(self, title: str, authors: list[str], collections: list[str]):
        self.title = title
        self.authors = authors
        self._collections = collections

    def get(self, key, default=None):
        if key in {"#collections", "collections"}:
            return self._collections
        return default


class _Book:
    def __init__(
        self,
        library_id: int | None,
        lpath: str,
        *,
        via: str = "application_id",
    ):
        self.lpath = lpath
        self.application_id = None
        self.db_id = None
        if library_id is not None:
            setattr(self, via, library_id)


class _Db:
    def __init__(self, meta: dict[int, _Meta]):
        self._meta = meta
        self.get_metadata_calls: list[tuple[object, bool]] = []

    def get_metadata(self, db_id, index_is_id=False, get_user_categories=False):
        self.get_metadata_calls.append((db_id, index_is_id))
        return self._meta[db_id]


class _Device:
    DEVICE_PLUGBOARD_NAME = "KOBOTOUCH"

    def __init__(
        self,
        books: list[_Book],
        main_prefix: str,
        card_prefix: str = "",
        *,
        plugboard: str = "KOBOTOUCH",
        card_books: list[_Book] | None = None,
    ):
        self._books = books
        self._card_books = list(card_books or [])
        self._main_prefix = main_prefix
        self._card_a_prefix = card_prefix
        self.DEVICE_PLUGBOARD_NAME = plugboard
        self.books_calls: list[tuple[object, bool]] = []

    def books(self, oncard=None, end_session=True):
        # Mirror stock Calibre: reject the old mistaken main_memory kwarg.
        self.books_calls.append((oncard, end_session))
        if oncard == "carda":
            return list(self._card_books) if self._card_a_prefix else []
        if oncard == "cardb":
            return []
        return list(self._books)


def _seed_kobo_koreader(prefix: Path, *, subdir: str = ".adds/koreader") -> Path:
    (prefix / ".kobo").mkdir(parents=True)
    root = prefix / Path(subdir.lstrip("/"))
    (root / "settings").mkdir(parents=True)
    return root


def _seed_android_koreader(prefix: Path) -> Path:
    root = prefix / "koreader"
    (root / "cache").mkdir(parents=True)
    return root


def test_build_collections_index_from_device_books(tmp_path: Path):
    mount = tmp_path / "kobo"
    _seed_kobo_koreader(mount)
    db = _Db(
        {
            1: _Meta("Alpha", ["Author A"], ["Harry Potter", "Fluff"]),
            2: _Meta("Beta", ["Author B"], []),
        }
    )
    device = _Device(
        [_Book(1, "Author A/Alpha.epub"), _Book(2, "Author B/Beta.epub")],
        str(mount),
    )
    entries = build_collections_index(db, device)
    assert len(entries) == 2
    assert entries[0]["lpath"] == "Author A/Alpha.epub"
    assert entries[0]["collections"] == ["Harry Potter", "Fluff"]
    assert entries[1]["collections"] == []
    assert [call[0] for call in device.books_calls] == [None, "carda", "cardb"]
    assert db.get_metadata_calls == [(1, True), (2, True)]


def test_build_collections_index_uses_application_id(tmp_path: Path):
    """Calibre Kobo matching sets application_id, not db_id."""
    mount = tmp_path / "kobo"
    _seed_kobo_koreader(mount)
    db = _Db({7: _Meta("Alpha", ["A"], ["DW"])})
    matched = _Book(7, "A/Alpha.epub", via="application_id")
    unmatched = _Book(None, "Other/Sideload.epub")
    sony_style = _Book(7, "A/Also.epub", via="db_id")
    # Distinct library ids so both matched rows are kept.
    sony_style.db_id = 7
    device = _Device([matched, unmatched, sony_style], str(mount))
    entries = build_collections_index(db, device)
    assert [row["lpath"] for row in entries] == ["A/Alpha.epub", "A/Also.epub"]
    assert all(row["collections"] == ["DW"] for row in entries)


def test_build_collections_index_prefers_gui_booklists(tmp_path: Path):
    mount = tmp_path / "kobo"
    _seed_kobo_koreader(mount)
    db = _Db({3: _Meta("From GUI", ["A"], ["Matched"])})
    device = _Device([_Book(99, "ignored.epub")], str(mount))
    booklists = ([_Book(3, "A/From GUI.epub")], [], [])
    entries = build_collections_index(db, device, booklists=booklists)
    assert len(entries) == 1
    assert entries[0]["lpath"] == "A/From GUI.epub"
    assert entries[0]["collections"] == ["Matched"]
    assert device.books_calls == []


def test_build_collections_index_includes_card_books(tmp_path: Path):
    mount = tmp_path / "kobo"
    sd = tmp_path / "sd"
    _seed_kobo_koreader(mount)
    _seed_kobo_koreader(sd)
    db = _Db(
        {
            1: _Meta("Main", ["A"], ["On Device"]),
            2: _Meta("Card", ["B"], ["On SD"]),
        }
    )
    device = _Device(
        [_Book(1, "A/Main.epub")],
        str(mount),
        str(sd),
        card_books=[_Book(2, "B/Card.epub")],
    )
    entries = build_collections_index(db, device)
    assert [row["lpath"] for row in entries] == ["A/Main.epub", "B/Card.epub"]
    assert entries[1]["collections"] == ["On SD"]


def test_build_collections_index_rejects_main_memory_kwarg(tmp_path: Path):
    """Real KOBOTOUCH.books() raises TypeError on main_memory=…."""

    class _StrictKobo:
        DEVICE_PLUGBOARD_NAME = "KOBOTOUCH"

        def __init__(self, books: list[_Book], prefix: str):
            self._books = books
            self._main_prefix = prefix
            self._card_a_prefix = ""

        def books(self, oncard=None, end_session=True):
            if oncard in ("carda", "cardb"):
                return []
            return list(self._books)

    mount = tmp_path / "kobo"
    _seed_kobo_koreader(mount)
    db = _Db({1: _Meta("Alpha", ["A"], ["X"])})
    device = _StrictKobo([_Book(1, "A/Alpha.epub")], str(mount))

    with pytest.raises(TypeError, match="main_memory"):
        device.books(main_memory=True)

    entries = build_collections_index(db, device)
    assert len(entries) == 1
    assert entries[0]["collections"] == ["X"]


def test_atomic_write_json(tmp_path: Path):
    target = tmp_path / "cache" / COLLECTIONS_JSON_NAME
    data = [{"lpath": "a.epub", "collections": ["X"]}]
    atomic_write_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data
    assert not (tmp_path / "cache" / (COLLECTIONS_JSON_NAME + ".tmp")).exists()


def test_deploy_metadata_and_install_plugin(tmp_path: Path):
    koreader_root = tmp_path / ".adds" / "koreader"
    (koreader_root / "settings").mkdir(parents=True)
    source = tmp_path / "plugin"
    source.mkdir()
    (source / "main.lua").write_text("-- test\n", encoding="utf-8")
    entries = [{"lpath": "Author/Title.epub", "collections": ["River Song"]}]
    path = deploy_metadata(koreader_root, entries)
    assert path.name == COLLECTIONS_JSON_NAME
    installed = install_plugin(koreader_root, source)
    assert installed.name == KOPLUGIN_DIRNAME
    assert (installed / "main.lua").is_file()


def test_detect_kobo_koreader_mount(tmp_path: Path):
    mount = tmp_path / "internal"
    root = _seed_kobo_koreader(mount)
    device = _Device([], str(mount))
    mounts = detect_koreader_mounts(device)
    assert len(mounts) == 1
    assert mounts[0].kind == "kobo"
    assert mounts[0].koreader_root == root


def test_detect_android_koreader_mount(tmp_path: Path):
    mount = tmp_path / "phone"
    root = _seed_android_koreader(mount)
    device = _Device([], str(mount), plugboard="FOLDER_DEVICE")
    mounts = detect_koreader_mounts(device)
    assert len(mounts) == 1
    assert mounts[0].kind == "android"
    assert mounts[0].koreader_root == root


def test_detect_rejects_kobo_without_koreader(tmp_path: Path):
    mount = tmp_path / "internal"
    mount.mkdir()
    (mount / ".kobo").mkdir()
    device = _Device([], str(mount))
    with pytest.raises(KoreaderDetectionError, match="does not appear to have KOReader"):
        detect_koreader_mounts(device)


def test_detect_rejects_non_koreader_device(tmp_path: Path):
    mount = tmp_path / "kindle"
    mount.mkdir()
    device = _Device([], str(mount), plugboard="KINDLE2")
    with pytest.raises(KoreaderDetectionError, match="not a compatible KOReader device"):
        detect_koreader_mounts(device)


def test_detect_rejects_mtp_without_koreader():
    class _Entry:
        def __init__(self, name: str, *, is_folder: bool):
            self.name = name
            self.is_folder = is_folder

    class _Storage:
        storage_prefix = "mtp:::1:::"
        object_id = 1

    class _Mtp:
        DEVICE_PLUGBOARD_NAME = "MTP_DEVICE"

        def __init__(self):
            self.filesystem_cache = type("Cache", (), {"entries": [_Storage()]})()

        def list_folder_by_name(self, storage, *names):
            raise FileNotFoundError(names)

    with pytest.raises(KoreaderDetectionError, match="does not appear to have KOReader"):
        detect_koreader_mounts(_Mtp())


def test_detect_mtp_android_koreader_mount():
    class _Entry:
        def __init__(self, name: str, *, is_folder: bool):
            self.name = name
            self.is_folder = is_folder

    class _Storage:
        storage_prefix = "mtp:::1:::"
        object_id = 1

    class _Mtp:
        DEVICE_PLUGBOARD_NAME = "MTP_DEVICE"
        _main_id = 1

        def __init__(self):
            self.filesystem_cache = type("Cache", (), {"entries": [_Storage()]})()
            self.uploaded: dict[tuple[str, ...], bytes] = {}

        def list_folder_by_name(self, storage, *names):
            if names == ("koreader",):
                return (
                    _Entry("cache", is_folder=True),
                    _Entry("settings", is_folder=True),
                )
            raise FileNotFoundError(names)

        def ensure_parent(self, storage, path):
            return storage

        def put_file(self, parent, name, stream, size, replace=True):
            self.uploaded[name] = stream.read()

    device = _Mtp()
    mounts = detect_koreader_mounts(device)
    assert len(mounts) == 1
    assert mounts[0].kind == "android"
    assert mounts[0].transport == "mtp"
    assert koreader_deployable(device)


def test_deploy_to_mtp_device(tmp_path: Path):
    class _Entry:
        def __init__(self, name: str, *, is_folder: bool):
            self.name = name
            self.is_folder = is_folder

    class _Storage:
        storage_prefix = "mtp:::1:::"
        object_id = 1

    class _Mtp:
        DEVICE_PLUGBOARD_NAME = "MTP_DEVICE"
        _main_id = 1

        def __init__(self, books: list[_Book]):
            self._books = books
            self.filesystem_cache = type("Cache", (), {"entries": [_Storage()]})()
            self.uploaded: dict[tuple[str, ...], bytes] = {}

        def books(self, oncard=None, end_session=True):
            if oncard in ("carda", "cardb"):
                return []
            return list(self._books)

        def list_folder_by_name(self, storage, *names):
            if names == ("koreader",):
                return (_Entry("cache", is_folder=True),)
            raise FileNotFoundError(names)

        def ensure_parent(self, storage, path):
            return storage

        def put_file(self, parent, name, stream, size, replace=True):
            self.uploaded[name] = stream.read()

    plugin_source = tmp_path / KOPLUGIN_DIRNAME
    plugin_source.mkdir()
    (plugin_source / "main.lua").write_text("-- plugin\n", encoding="utf-8")

    db = _Db({1: _Meta("Title", ["Author"], ["DW"])})
    device = _Mtp([_Book(1, "Author/Title.epub")])

    result = deploy_to_device(
        db,
        device,
        plugin_source=plugin_source,
        install_koplugin=True,
    )
    assert result["books"] == 1
    assert result["koreader_kind"] == "android"
    assert COLLECTIONS_JSON_NAME in device.uploaded
    assert "main.lua" in device.uploaded


def test_koreader_roots_and_deploy_to_device(tmp_path: Path):
    plugin_source = tmp_path / KOPLUGIN_DIRNAME
    plugin_source.mkdir()
    (plugin_source / "main.lua").write_text("-- plugin\n", encoding="utf-8")

    internal = tmp_path / "internal"
    _seed_kobo_koreader(internal)
    sd = tmp_path / "sd"
    _seed_kobo_koreader(sd)

    db = _Db({1: _Meta("Title", ["Author"], ["DW"])})
    device = _Device([_Book(1, "Author/Title.epub")], str(internal), str(sd))

    roots = koreader_roots(device)
    assert len(roots) == 2
    assert koreader_deployable(device)

    result = deploy_to_device(
        db,
        device,
        plugin_source=plugin_source,
        install_koplugin=True,
    )
    assert result["books"] == 1
    assert len(result["collections_json"]) == 2
    assert len(result["plugin_installed"]) == 2


def test_build_collections_index_from_rows_dedupes_collections():
    rows = [
        {
            "lpath": "b.epub",
            "collections": ["Fluff", "fluff", "  Fluff "],
            "title": "B",
            "authors": ["A"],
        }
    ]
    entries = build_collections_index_from_rows(rows)
    assert entries[0]["collections"] == ["Fluff"]


def test_resolve_bundled_plugin_source_from_checkout():
    root = Path(__file__).resolve().parents[1]
    source = resolve_bundled_plugin_source(checkout_root=root)
    assert source is not None
    assert (source / "main.lua").is_file()


def test_zip_includes_koreader_resources(tmp_path: Path):
    entries = makeplugin.iter_zip_entries(vendor_dir=None)
    names = {arc for _path, arc in entries}
    assert f"resources/koreader/{KOPLUGIN_DIRNAME}/main.lua" in names


def test_cached_plugin_from_zip(tmp_path: Path):
    dest = tmp_path / "fanfic-organizer.zip"
    makeplugin.build_zip(dest, vendor=False)
    source = cached_plugin_from_zip(dest)
    assert source is not None
    assert (source / "main.lua").is_file()
    with zipfile.ZipFile(dest) as zf:
        assert any(
            name.startswith(f"resources/koreader/{KOPLUGIN_DIRNAME}/")
            for name in zf.namelist()
        )
