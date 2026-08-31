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


def test_source_registry_lists_ao3_wikipedia_and_web():
    from sources import all_sources, source_menu_labels

    ids = [s.id for s in all_sources()]
    assert ids == ["wikipedia", "web", "ao3"]
    assert source_menu_labels(group="toolbar") == ("Search AO3 and import...",)
    assert source_menu_labels(group="import") == ("Wikipedia...", "URL or HTML...")


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
            "touched": "2020-05-01T12:00:00Z",
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
    # Revisions remain a fallback when ``touched`` is absent (single-page callers).
    via_rev = _page_to_record(
        {
            "pageid": 100,
            "title": "TARDIS",
            "fullurl": "https://en.wikipedia.org/wiki/TARDIS",
            "revisions": [{"timestamp": "2019-01-02T00:00:00Z"}],
        },
        lang="en",
    )
    assert via_rev is not None
    assert via_rev["date"] == "2019-01-02"


def test_fetch_pages_omits_rvlimit_for_multipage(monkeypatch):
    """MediaWiki rejects rvlimit when titles/pageids list more than one page."""
    from ao3kit.sources import wikipedia as wiki

    captured: list[dict] = []

    def fake_api_get(session, api_url, params, *, on_status=None):
        captured.append(dict(params))
        return {
            "query": {
                "pages": [
                    {
                        "pageid": 1,
                        "title": "A",
                        "fullurl": "https://en.wikipedia.org/wiki/A",
                        "touched": "2024-01-01T00:00:00Z",
                        "extract": "a",
                    },
                    {
                        "pageid": 2,
                        "title": "B",
                        "fullurl": "https://en.wikipedia.org/wiki/B",
                        "touched": "2024-02-01T00:00:00Z",
                        "extract": "b",
                    },
                ]
            }
        }

    monkeypatch.setattr(wiki, "_api_get", fake_api_get)
    records = wiki.fetch_pages(pageids=[1, 2], lang="en")
    assert len(records) == 2
    assert len(captured) == 1
    params = captured[0]
    assert "rvlimit" not in params
    assert "rvprop" not in params
    assert "revisions" not in str(params.get("prop", ""))
    assert params["pageids"] == "1|2"
    assert records[0]["date"] == "2024-01-01"
    assert records[1]["date"] == "2024-02-01"


def test_web_extract_metadata_and_article():
    from ao3kit.sources.extract import extract_page
    from ao3kit.sources.web import record_from_html, work_id_for_url

    html = """
    <html lang="en">
    <head>
      <title>Ignore me</title>
      <meta property="og:title" content="The River Song Files"/>
      <meta property="og:description" content="Spoilers."/>
      <meta property="og:site_name" content="Example Fanfic"/>
      <meta name="author" content="River Song"/>
      <meta property="article:published_time" content="2021-03-15T10:00:00Z"/>
      <meta name="keywords" content="Doctor Who, Time travel"/>
    </head>
    <body>
      <nav><a href="/">Home</a></nav>
      <article>
        <h1>The River Song Files</h1>
        <p>""" + ("Hello world. " * 40) + """</p>
        <p>More paragraphs about adventures in time and space for the extract.</p>
      </article>
      <footer>Copyright</footer>
    </body>
    </html>
    """
    extracted = extract_page(html, url="https://example.com/river")
    assert extracted.title == "The River Song Files"
    assert extracted.author == "River Song"
    assert extracted.summary == "Spoilers."
    assert extracted.date == "2021-03-15"
    assert extracted.language == "en"
    assert "Doctor Who" in extracted.tags
    assert "Hello world" in extracted.html_body
    assert "<nav" not in extracted.html_body.casefold()

    record = record_from_html(html, url="https://example.com/river")
    assert record["source"] == "web"
    assert record["work_id"] == work_id_for_url("https://example.com/river")
    assert record["title"] == "The River Song Files"
    assert record["author"] == "River Song"


def test_web_calibre_fields_use_web_identifier():
    mod = load_cleaned()
    record = {
        "source": "web",
        "work_id": "abcd1234abcd1234",
        "url": "https://example.com/post",
        "title": "A post",
        "author": "Author",
        "summary": "Hello",
        "tags": ["Example"],
        "date": "2022-06-01",
        "metadata": {"language": "en", "words": 90},
    }
    fields = mod.calibre_fields_for_record(record)
    assert fields["source"] == "web"
    assert fields["publisher"] == "Web"
    assert fields["identifiers"]["web"] == "abcd1234abcd1234"
    assert "ao3" not in fields["identifiers"]
    assert fields["identifiers"]["url"] == record["url"]
    assert fields["published"].isoformat() == "2022-06-01"


def test_prepare_web_command_html_copies_file(tmp_path: Path):
    from sources.web.run import prepare_web_command

    html = tmp_path / "page.html"
    html.write_text("<html><body><p>Hi</p></body></html>", encoding="utf-8")
    work = tmp_path / "work"
    argv, jsonl = prepare_web_command(
        {
            "html_path": str(html),
            "url": "https://example.com/page",
            "download_epubs": True,
        },
        work,
    )
    assert argv[:1] == ["web"]
    assert "--html" in argv
    assert "--page-url" in argv
    assert "--epub" in argv
    assert (work / "input.html").is_file()
    assert jsonl == work / "results.jsonl"


def test_write_web_epub(tmp_path: Path):
    from ao3kit.sources.web_epub import write_web_epub
    import zipfile

    path = tmp_path / "epubs" / "abc.epub"
    write_web_epub(
        path,
        title="Hello",
        html_body="<p>Body text for the page.</p>",
        url="https://example.com/hello",
        work_id="abc",
    )
    assert path.is_file()
    with zipfile.ZipFile(path) as zf:
        assert zf.read("mimetype") == b"application/epub+zip"
        chapter = zf.read("OEBPS/chapter.xhtml").decode("utf-8")
        assert "Body text" in chapter


def test_plan_web(tmp_path: Path):
    from sources.web.plan import plan_web

    html = tmp_path / "saved.html"
    html.write_text("<html><body><article><p>" + ("x " * 80) + "</p></article></body></html>")
    job_dir = tmp_path / "job1"
    job_dir.mkdir()
    spec = plan_web(
        {"html_path": str(html), "url": "https://example.com/x", "download_epubs": True},
        job_dir,
    )
    assert spec["kind"] == "web"
    assert spec["steps"][0][0] == "web"
    assert (job_dir / "spec.json").is_file()
