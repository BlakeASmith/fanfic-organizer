"""Tests for multi-source adapters and Wikipedia JSONL mapping."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "calibre-plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))


def load_cleaned():
    spec = importlib.util.spec_from_file_location(
        "ao3_cleaned_sources", PLUGIN / "cleaned.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_registry_lists_ao3_and_wikipedia():
    from sources import all_sources, source_menu_labels

    ids = [s.id for s in all_sources()]
    assert ids == ["wikipedia", "ao3"]
    assert source_menu_labels(group="toolbar") == ("Search AO3 and import...",)
    assert source_menu_labels(group="import") == ("Wikipedia...",)


def test_wikipedia_calibre_fields_use_wikipedia_identifier():
    mod = load_cleaned()
    record = {
        "source": "wikipedia",
        "work_id": "21721040",
        "url": "https://en.wikipedia.org/wiki/Doctor_Who",
        "title": "Doctor Who",
        "author": "Wikipedia contributors",
        "summary": "A British science fiction series.",
        "tags": ["BBC television"],
        "date": "2024-01-15",
        "metadata": {"language": "en", "words": 1200},
    }
    fields = mod.calibre_fields_for_record(record)
    assert fields["source"] == "wikipedia"
    assert fields["publisher"] == "Wikipedia"
    assert fields["identifiers"]["wikipedia"] == "21721040"
    assert "ao3" not in fields["identifiers"]
    assert fields["identifiers"]["url"] == record["url"]
    assert fields["series"] is None
    assert fields["tags"] == ["BBC television"]
    assert fields["published"].isoformat() == "2024-01-15"


def test_wikipedia_book_match_does_not_collide_with_ao3_id():
    mod = load_cleaned()
    wiki_ids = {
        "wikipedia": "10",
        "url": "https://en.wikipedia.org/wiki/Foo",
    }
    assert mod.book_matches_work(
        wiki_ids, work_id="10", url="", source="wikipedia"
    )
    assert not mod.book_matches_work(
        {"ao3": "10"}, work_id="10", url="", source="wikipedia"
    )
    assert mod.book_matches_work(
        {"ao3": "10"}, work_id="10", url="", source="ao3"
    )


def test_existing_book_id_from_wikipedia_record():
    mod = load_cleaned()
    books = [
        (1, {"ao3": "10"}),
        (2, {"wikipedia": "10", "url": "https://en.wikipedia.org/wiki/X"}),
    ]
    found = mod.existing_book_id_from_identifiers(
        books,
        {
            "source": "wikipedia",
            "work_id": "10",
            "url": "https://en.wikipedia.org/wiki/X",
        },
    )
    assert found == 2


def test_prepare_wikipedia_command(tmp_path: Path):
    from sources.wikipedia.run import prepare_wikipedia_command

    argv, jsonl = prepare_wikipedia_command(
        {
            "query": "Doctor Who",
            "lang": "en",
            "max_results": "5",
            "download_epubs": True,
        },
        tmp_path,
    )
    assert argv[:1] == ["wikipedia"]
    assert "--query" in argv
    assert argv[argv.index("--query") + 1] == "Doctor Who"
    assert "--epub" in argv
    assert "--epub-dir" in argv
    assert jsonl == tmp_path / "results.jsonl"


def test_write_article_epub(tmp_path: Path):
    from ao3kit.sources.wikipedia_epub import write_article_epub

    path = tmp_path / "epubs" / "99.epub"
    write_article_epub(
        path,
        title="TARDIS",
        html_body="<p>A time machine and spacecraft.</p>",
        url="https://en.wikipedia.org/wiki/TARDIS",
        work_id="99",
    )
    assert path.is_file()
    assert path.stat().st_size > 100
    import zipfile

    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert "mimetype" in names
        assert "OEBPS/chapter.xhtml" in names
        assert zf.read("mimetype") == b"application/epub+zip"


def test_plan_wikipedia(tmp_path: Path):
    from sources.wikipedia.plan import plan_wikipedia

    job_dir = tmp_path / "job1"
    job_dir.mkdir()
    spec = plan_wikipedia(
        {"query": "TARDIS", "lang": "en", "max_results": "3"},
        job_dir,
    )
    assert spec["kind"] == "wikipedia"
    assert spec["steps"][0][0] == "wikipedia"
    assert (job_dir / "spec.json").is_file()


def test_wikipedia_url_parse_and_record_shape():
    from ao3kit.sources.wikipedia import (
        parse_wikipedia_url,
        wiki_article_url,
        _page_to_record,
    )

    assert parse_wikipedia_url("https://en.wikipedia.org/wiki/Doctor_Who") == (
        "en",
        "Doctor Who",
    )
    assert "Doctor_Who" in wiki_article_url("Doctor Who", lang="en")
    record = _page_to_record(
        {
            "pageid": 99,
            "title": "TARDIS",
            "fullurl": "https://en.wikipedia.org/wiki/TARDIS",
            "extract": "A time machine.",
            "categories": [{"title": "Category:Doctor Who"}],
            "revisions": [{"timestamp": "2020-05-01T12:00:00Z"}],
            "wordcount": 400,
        },
        lang="en",
    )
    assert record is not None
    assert record["source"] == "wikipedia"
    assert record["work_id"] == "99"
    assert record["tags"] == ["Doctor Who"]
    assert record["summary"] == "A time machine."
    assert record["date"] == "2020-05-01"
