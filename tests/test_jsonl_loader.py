from __future__ import annotations

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
