from __future__ import annotations

import pytest
import importlib.util
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile


PLUGIN_LOADER = Path(__file__).resolve().parents[1] / "calibre-plugin" / "jsonl_loader.py"


def load_jsonl_loader():
    spec = importlib.util.spec_from_file_location("ao3_jsonl_loader", PLUGIN_LOADER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def minimal_epub_bytes() -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
    return buf.getvalue()


def test_load_jsonl_and_resolve_epub(tmp_path: Path):
    loader = load_jsonl_loader()
    jsonl = tmp_path / "results.jsonl"
    jsonl.write_text(
        '{"work_id":"123","url":"https://archiveofourown.org/works/123","epub_file":"epubs/123.epub"}\n',
        encoding="utf-8",
    )
    epub = tmp_path / "epubs" / "123.epub"
    epub.parent.mkdir()
    epub.write_bytes(minimal_epub_bytes())

    records, root, cleanup = loader.load_import_source(jsonl)
    assert cleanup is None
    assert root == tmp_path
    assert records[0]["work_id"] == "123"
    assert loader.resolve_epub_path(records[0], root) == epub


def test_resolve_epub_falls_back_to_work_id(tmp_path: Path):
    loader = load_jsonl_loader()
    epub = tmp_path / "epubs" / "77.epub"
    epub.parent.mkdir()
    epub.write_bytes(b"PK")
    path = loader.resolve_epub_path({"work_id": "77"}, tmp_path)
    assert path == epub


def test_load_jsonl_accepts_calibre_identity_without_ao3(tmp_path: Path):
    """Process library simplify includes books that have no AO3 id yet."""
    loader = load_jsonl_loader()
    jsonl = tmp_path / "cleaned.jsonl"
    jsonl.write_text(
        '{"title":"No AO3 yet","calibre_book_id":42,'
        '"cleaned":{"simplified":["Fluff"]}}\n'
        '{"title":"UUID only","calibre_uuid":"abc-def",'
        '"cleaned":{"simplified":["Angst"]}}\n',
        encoding="utf-8",
    )
    records = loader.load_jsonl_records(jsonl)
    assert len(records) == 2
    assert records[0]["calibre_book_id"] == 42
    assert records[1]["calibre_uuid"] == "abc-def"


def test_load_jsonl_rejects_row_with_no_identity(tmp_path: Path):
    loader = load_jsonl_loader()
    jsonl = tmp_path / "bad.jsonl"
    jsonl.write_text('{"title":"orphan"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing work_id, url, calibre_book_id"):
        loader.load_jsonl_records(jsonl)


def test_record_has_identity():
    loader = load_jsonl_loader()
    assert loader.record_has_identity({"work_id": "1"})
    assert loader.record_has_identity({"url": "https://archiveofourown.org/works/1"})
    assert loader.record_has_identity({"calibre_book_id": 7})
    assert loader.record_has_identity({"calibre_uuid": "u"})
    assert not loader.record_has_identity({"title": "x"})
    assert not loader.record_has_identity({"calibre_book_id": 0})
    assert not loader.record_has_identity({"calibre_book_id": ""})


def test_load_import_zip_extracts_bundle(tmp_path: Path):
    loader = load_jsonl_loader()
    bundle = tmp_path / "bundle"
    epubs = bundle / "epubs"
    epubs.mkdir(parents=True)
    (bundle / "results.jsonl").write_text(
        '{"work_id":"9","url":"https://archiveofourown.org/works/9","epub_file":"epubs/9.epub"}\n',
        encoding="utf-8",
    )
    (epubs / "9.epub").write_bytes(minimal_epub_bytes())
    zip_path = tmp_path / "ao3-import.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.write(bundle / "results.jsonl", arcname="results.jsonl")
        zf.write(epubs / "9.epub", arcname="epubs/9.epub")

    extract_dir = tmp_path / "extracted"
    records, root, cleanup = loader.load_import_source(zip_path, extract_dir=extract_dir)
    assert cleanup is None
    assert root == extract_dir
    assert records[0]["work_id"] == "9"
    assert loader.resolve_epub_path(records[0], root) == extract_dir / "epubs" / "9.epub"
