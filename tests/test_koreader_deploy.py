"""Tests for KOReader collections deploy helpers."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import makeplugin
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
    def __init__(self, db_id: int, lpath: str):
        self.db_id = db_id
        self.lpath = lpath


class _Db:
    def __init__(self, meta: dict[int, _Meta]):
        self._meta = meta

    def get_metadata(self, db_id, get_user_categories=False):
        return self._meta[db_id]


class _Device:
    def __init__(self, books: list[_Book], main_prefix: str, card_prefix: str = ""):
        self._books = books
        self._main_prefix = main_prefix
        self._card_a_prefix = card_prefix

    def books(self, main_memory=True):
        return list(self._books)


def test_build_collections_index_from_device_books():
    db = _Db(
        {
            1: _Meta("Alpha", ["Author A"], ["Harry Potter", "Fluff"]),
            2: _Meta("Beta", ["Author B"], []),
        }
    )
    device = _Device(
        [_Book(1, "Author A/Alpha.epub"), _Book(2, "Author B/Beta.epub")],
        "/mnt/kobo",
    )
    entries = build_collections_index(db, device)
    assert len(entries) == 2
    assert entries[0]["lpath"] == "Author A/Alpha.epub"
    assert entries[0]["collections"] == ["Harry Potter", "Fluff"]
    assert entries[1]["collections"] == []


def test_atomic_write_json(tmp_path: Path):
    target = tmp_path / "cache" / COLLECTIONS_JSON_NAME
    data = [{"lpath": "a.epub", "collections": ["X"]}]
    atomic_write_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data
    assert not (tmp_path / "cache" / (COLLECTIONS_JSON_NAME + ".tmp")).exists()


def test_deploy_metadata_and_install_plugin(tmp_path: Path):
    device_root = tmp_path / "kobo" / ".adds" / "koreader"
    source = tmp_path / "plugin"
    source.mkdir()
    (source / "main.lua").write_text("-- test\n", encoding="utf-8")
    entries = [{"lpath": "Author/Title.epub", "collections": ["River Song"]}]
    path = deploy_metadata(device_root, entries)
    assert path.name == COLLECTIONS_JSON_NAME
    installed = install_plugin(device_root, source)
    assert installed.name == KOPLUGIN_DIRNAME
    assert (installed / "main.lua").is_file()


def test_koreader_roots_main_and_card():
    device = _Device([], "/mnt/internal", "/mnt/sd")
    roots = koreader_roots(device)
    assert len(roots) == 2
    assert str(roots[0]).endswith(".adds/koreader")
    assert str(roots[1]).endswith(".adds/koreader")


def test_deploy_to_device_writes_both_roots(tmp_path: Path):
    plugin_source = tmp_path / KOPLUGIN_DIRNAME
    plugin_source.mkdir()
    (plugin_source / "main.lua").write_text("-- plugin\n", encoding="utf-8")
    db = _Db({1: _Meta("Title", ["Author"], ["DW"])})
    device = _Device([_Book(1, "Author/Title.epub")], str(tmp_path / "internal"))
    device._card_a_prefix = str(tmp_path / "sd")
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
