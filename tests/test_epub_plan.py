from __future__ import annotations

import importlib.util
from pathlib import Path


PLUGIN_EPUB_PLAN = Path(__file__).resolve().parents[1] / "calibre-plugin" / "epub_plan.py"


def load_epub_plan():
    spec = importlib.util.spec_from_file_location("ao3_epub_plan", PLUGIN_EPUB_PLAN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_formats_include_epub_accepts_calibre_shapes():
    mod = load_epub_plan()
    assert mod.formats_include_epub("EPUB")
    assert mod.formats_include_epub("EPUB,PDF")
    assert mod.formats_include_epub(["PDF", "epub"])
    assert not mod.formats_include_epub(None)
    assert not mod.formats_include_epub("")
    assert not mod.formats_include_epub("PDF,TXT")
    assert not mod.formats_include_epub([])


def test_plan_skips_existing_epub_and_books_without_ao3():
    mod = load_epub_plan()
    ready, skipped = mod.plan_missing_epub_downloads(
        [
            {
                "book_id": 1,
                "title": "Has file",
                "record": {
                    "work_id": "11",
                    "url": "https://archiveofourown.org/works/11",
                },
                "has_epub": True,
            },
            {
                "book_id": 2,
                "title": "Needs file",
                "record": {
                    "work_id": "22",
                    "url": "https://archiveofourown.org/works/22",
                    "title": "Needs file",
                },
                "has_epub": False,
            },
            {
                "book_id": 3,
                "title": "No id",
                "record": None,
                "has_epub": False,
            },
        ]
    )
    assert [item["book_id"] for item in ready] == [2]
    reasons = {item["book_id"]: item["reason"] for item in skipped}
    assert reasons[1] == mod.REASON_HAS_EPUB
    assert reasons[3] == mod.REASON_NO_AO3


def test_merge_download_manifest_copies_epub_file():
    mod = load_epub_plan()
    items = [
        {
            "book_id": 2,
            "title": "Needs file",
            "record": {"work_id": "22", "title": "Needs file"},
        }
    ]
    merged = mod.merge_download_manifest(
        items,
        [
            {
                "work_id": "22",
                "title": "Needs file",
                "epub_file": "epubs/22.epub",
            }
        ],
    )
    assert merged[0]["book_id"] == 2
    assert merged[0]["record"]["epub_file"] == "epubs/22.epub"


def test_summarize_epub_download():
    mod = load_epub_plan()
    text = mod.summarize_epub_download(
        [
            {"action": "added"},
            {"action": "failed"},
        ],
        [
            {"reason": mod.REASON_HAS_EPUB},
            {"reason": mod.REASON_NO_AO3},
        ],
    )
    assert "Added EPUB to 1 book" in text
    assert "already had one" in text
    assert "without an AO3 URL" in text
    assert "1 failed" in text


def test_pending_epub_attachments_only_new_files():
    mod = load_epub_plan()
    items = [
        {
            "book_id": 1,
            "title": "One",
            "record": {"work_id": "11", "title": "One"},
        },
        {
            "book_id": 2,
            "title": "Two",
            "record": {"work_id": "22", "title": "Two"},
        },
    ]
    first = mod.pending_epub_attachments(
        items,
        [{"work_id": "11", "title": "One", "epub_file": "epubs/11.epub"}],
        set(),
    )
    assert [item["book_id"] for item in first] == [1]
    assert first[0]["record"]["epub_file"] == "epubs/11.epub"

    second = mod.pending_epub_attachments(
        items,
        [
            {"work_id": "11", "title": "One", "epub_file": "epubs/11.epub"},
            {"work_id": "22", "title": "Two", "epub_file": "epubs/22.epub"},
        ],
        {1},
    )
    assert [item["book_id"] for item in second] == [2]


def test_pending_incremental_imports_metadata_then_epub():
    mod = load_epub_plan()
    records = [
        {"work_id": "11", "title": "One"},
        {"work_id": "22", "title": "Two", "epub_file": "epubs/22.epub"},
    ]
    imported: dict = {}
    new, epubs = mod.pending_incremental_imports(
        records, imported, work_id_of=lambda rec: rec.get("work_id")
    )
    assert [row["work_id"] for row in new] == ["11", "22"]
    assert epubs == []

    imported["11"] = {"book_id": 1, "has_epub": False}
    imported["22"] = {"book_id": 2, "has_epub": True}
    records[0]["epub_file"] = "epubs/11.epub"
    new, epubs = mod.pending_incremental_imports(
        records, imported, work_id_of=lambda rec: rec.get("work_id")
    )
    assert new == []
    assert [row["work_id"] for row in epubs] == ["11"]


def test_summarize_epub_download_cancelled():
    mod = load_epub_plan()
    assert (
        mod.summarize_epub_download([], [], cancelled=True)
        == "Cancelled before any EPUB was added."
    )
    text = mod.summarize_epub_download(
        [{"action": "added"}],
        [],
        cancelled=True,
    )
    assert "Added EPUB to 1 book" in text
    assert "Cancelled before the rest finished" in text
