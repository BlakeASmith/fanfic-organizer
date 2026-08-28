"""Whole-library job estimates (plugin, Calibre-free)."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin"


def load_library_job():
    spec = importlib.util.spec_from_file_location(
        "library_job", PLUGIN / "library_job.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["library_job"] = module
    spec.loader.exec_module(module)
    return module


def _book(mod, **kwargs):
    return mod.LibraryBook(**kwargs)


def _write_cache(path: Path, names: list[str]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE entries (name TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO entries(name) VALUES (?)", [(n,) for n in names])
    conn.commit()
    conn.close()


def test_unique_tag_names_skips_injected_and_dedupes():
    mod = load_library_job()
    books = [
        _book(
            mod,
            book_id=1,
            tags=("Fluff", "Completed"),
            fandoms=("Harry Potter - J. K. Rowling",),
            relationships=("Draco Malfoy/Harry Potter",),
            original_tags=("Fluff", "Angst"),
        ),
        _book(mod, book_id=2, tags=("Angst", "Fanfiction")),
    ]
    assert mod.unique_tag_names(books) == [
        "Fluff",
        "Harry Potter - J. K. Rowling",
        "Draco Malfoy/Harry Potter",
        "Angst",
    ]


def test_uncached_tag_names_uses_sqlite(tmp_path: Path):
    mod = load_library_job()
    cache = tmp_path / "ao3_tag_cache.sqlite"
    _write_cache(cache, ["Fluff", "Angst"])
    missing = mod.uncached_tag_names(cache, ["Fluff", "Hurt/Comfort", "Angst"])
    assert missing == ["Hurt/Comfort"]
    assert mod.uncached_tag_names(tmp_path / "missing.sqlite", ["Fluff"]) == ["Fluff"]


def test_series_is_complete_needs_id_name_and_part():
    mod = load_library_job()
    complete = _book(
        mod,
        book_id=1,
        identifiers={"ao3": "1", "ao3series": "99"},
        series_name="A Series",
        series_index=2.0,
    )
    missing_part = _book(
        mod,
        book_id=2,
        identifiers={"ao3": "2", "ao3series": "99"},
        series_name="A Series",
    )
    assert complete.series_is_complete() is True
    assert missing_part.series_is_complete() is False
    assert complete.work_id == "1"


def test_estimate_simplify_counts_uncached(tmp_path: Path):
    mod = load_library_job()
    cache = tmp_path / "cache.sqlite"
    _write_cache(cache, ["Fluff"])
    books = [
        _book(
            mod,
            book_id=1,
            title="One",
            identifiers={"ao3": "11", "url": "https://archiveofourown.org/works/11"},
            tags=("Fluff", "Hurt/Comfort"),
            has_epub=False,
        ),
        _book(
            mod,
            book_id=2,
            title="Two",
            identifiers={"ao3": "22"},
            tags=("Fluff",),
            has_epub=True,
            series_name="S",
            series_index=1.0,
        ),
    ]
    books[1].identifiers["ao3series"] = "9"
    options = mod.LibraryJobOptions(simplify_tags=True, download_epubs=True)
    estimate = mod.estimate_library_job(
        books, options, cache_path=cache, request_interval=2.0
    )
    assert estimate.book_count == 2
    assert estimate.with_ao3 == 2
    assert estimate.unique_tags == 2
    assert estimate.cached_tags == 1
    assert estimate.uncached_tags == 1
    assert estimate.missing_epub == 1
    assert estimate.has_epub == 1
    assert estimate.tag_fetch_seconds == 2.0
    assert estimate.epub_fetch_seconds == 2.0
    text = mod.format_library_estimate(estimate, options)
    assert "unmatched" in text
    assert "Download EPUBs: 1" in text
    assert "no AO3 URLs are loaded" in text


def test_options_from_prefs_defaults_to_simplify_only():
    mod = load_library_job()
    options = mod.options_from_prefs({})
    assert options.simplify_tags is True
    assert options.import_series is False
    assert options.download_epubs is False
    saved = mod.options_from_prefs(
        {"library_simplify_tags": False, "library_download_epubs": True}
    )
    assert saved.simplify_tags is False
    assert saved.download_epubs is True


def test_parse_min_request_interval():
    mod = load_library_job()
    assert mod.parse_min_request_interval("min_request_interval: 2.5\n") == 2.5
    assert mod.parse_min_request_interval("# min_request_interval: 9\nnotes: x\n") == 1.5
    assert mod.format_duration(12) == "about 12s"
    assert "min" in mod.format_duration(180)
    assert mod.library_job_title(mod.LibraryJobOptions(), 3).startswith(
        "Process library (3 books: simplify)"
    )


def test_select_library_job_books_skips_without_ao3_when_needed():
    mod = load_library_job()
    books = [
        _book(mod, book_id=1, title="Has id", identifiers={"ao3": "11"}),
        _book(mod, book_id=2, title="No id", tags=("Fluff",)),
    ]
    ready, skipped = mod.select_library_job_books(
        books, mod.LibraryJobOptions(download_epubs=True, simplify_tags=False)
    )
    assert [book.book_id for book in ready] == [1]
    assert skipped[0]["book_id"] == 2
    all_ready, none_skipped = mod.select_library_job_books(
        books, mod.LibraryJobOptions(simplify_tags=True)
    )
    assert [book.book_id for book in all_ready] == [1, 2]
    assert none_skipped == []
