"""Identify AO3 works from URLs, EPUBs, and title+author search."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from ao3kit.identify import (
    IdentifyHint,
    apply_identify_choices,
    classify_search_matches,
    extract_work_id_from_epub_bytes,
    identify_hint,
    identify_records,
    main as identify_main,
    search_query_for_title,
    split_identify_records,
    titles_match,
    work_id_from_text,
)
from ao3kit.scrape import WorkRecord, main as scrape_main, parse_work_page

FIXTURES = Path(__file__).parent / "fixtures"


def ao3_epub_with_url(work_id: str = "90876776") -> bytes:
    url = f"https://archiveofourown.org/works/{work_id}"
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Time Storm</dc:title>
    <dc:creator>whovian</dc:creator>
    <dc:identifier id="uid">uuid:test</dc:identifier>
    <dc:identifier>{url}</dc:identifier>
    <dc:source>{url}</dc:source>
  </metadata>
  <manifest>
    <item id="page" href="titlepage.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="page"/>
  </spine>
</package>
"""
    titlepage = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
<p>Posted originally on the Archive of Our Own at <a href="{url}">{url}</a>.</p>
</body>
</html>
"""
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", opf)
        zf.writestr("titlepage.xhtml", titlepage)
    return buf.getvalue()


def _work(
    work_id: str,
    title: str,
    author: str,
    fandom: str = "Doctor Who (2005)",
) -> WorkRecord:
    return WorkRecord(
        work_id=work_id,
        url=f"https://archiveofourown.org/works/{work_id}",
        title=title,
        author=author,
        fandoms=[fandom],
    )


def test_work_id_from_url_and_comments():
    assert work_id_from_text("https://archiveofourown.org/works/90876776") == "90876776"
    assert (
        work_id_from_text("https://www.archiveofourown.org/works/90876776/chapters/1")
        == "90876776"
    )
    assert (
        work_id_from_text(
            "<p>Source: http://archiveofourown.org/works/42?view_adult=true</p>"
        )
        == "42"
    )
    assert (
        work_id_from_text("https://archiveofourown.org/downloads/90876776/Time.epub")
        == "90876776"
    )
    assert work_id_from_text({"ao3": "90876776", "isbn": "9781234567890"}) == "90876776"
    assert work_id_from_text({"isbn": "9781234567890"}) is None


def test_extract_work_id_from_epub_opf_and_html():
    assert extract_work_id_from_epub_bytes(ao3_epub_with_url("79168296")) == "79168296"


def test_titles_match_ignores_punctuation():
    assert titles_match("Time Storm!", "time storm")
    assert not titles_match("Time Storm", "Time Lord")


def test_search_query_quotes_title():
    assert search_query_for_title('Time "Storm"') == 'title: "Time  Storm"'


def test_classify_unique_title_and_author():
    hint = IdentifyHint(title="Time Storm", authors=["whovian"])
    result = classify_search_matches(
        hint,
        [
            _work("90876776", "Time Storm", "whovian"),
            _work("100", "Standalone", "x"),
        ],
    )
    assert result.status == "identified"
    assert result.record["work_id"] == "90876776"
    assert result.source == "search"


def test_classify_ambiguous_same_title_two_authors():
    hint = IdentifyHint(title="Time Storm", authors=["whovian"])
    result = classify_search_matches(
        hint,
        [
            _work("1", "Time Storm", "alice"),
            _work("2", "Time Storm", "bob"),
        ],
    )
    assert result.status == "ambiguous"
    assert len(result.candidates) == 2


def test_classify_single_inexact_title_asks_to_pick():
    hint = IdentifyHint(title="Time Storms", authors=["whovian"])
    result = classify_search_matches(hint, [_work("90876776", "Time Storm", "whovian")])
    assert result.status == "ambiguous"
    assert result.candidates[0]["work_id"] == "90876776"


def test_identify_hint_uses_identifier_without_search():
    result = identify_hint(
        IdentifyHint(
            title="Whatever",
            url="https://archiveofourown.org/works/9",
        ),
        search=False,
    )
    assert result.status == "identified"
    assert result.source == "identifier"
    assert result.record["work_id"] == "9"


def test_identify_hint_uses_epub(tmp_path: Path):
    epub = tmp_path / "book.epub"
    epub.write_bytes(ao3_epub_with_url("50448730"))
    result = identify_hint(
        IdentifyHint(title="Clandestine", epub_file=str(epub)),
        search=False,
    )
    assert result.status == "identified"
    assert result.source == "epub"
    assert result.record["work_id"] == "50448730"


def test_identify_hint_skips_unknown_title_without_url():
    result = identify_hint(IdentifyHint(title="Unknown", authors=["Unknown"]), search=False)
    assert result.status == "skipped"


def test_identify_records_search_monkeypatch(monkeypatch):
    called = {}

    def fake_search(hint, **kwargs):
        called["title"] = hint.title
        return [_work("90876776", "Time Storm", "whovian")]

    monkeypatch.setattr("ao3kit.identify.search_candidates", fake_search)
    results = identify_records(
        [{"title": "Time Storm", "author": "whovian", "book_id": 12}],
        search=True,
        session=object(),
    )
    assert called["title"] == "Time Storm"
    assert results[0].status == "identified"
    assert results[0].record["book_id"] == 12
    assert results[0].record["work_id"] == "90876776"


def test_identify_cli_no_search_from_jsonl(tmp_path: Path, capsys):
    hints = tmp_path / "hints.jsonl"
    out = tmp_path / "out.jsonl"
    hints.write_text(
        json.dumps(
            {
                "title": "Time Storm",
                "url": "https://archiveofourown.org/works/90876776",
                "book_id": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = identify_main(["--from", str(hints), "-o", str(out), "--no-search", "--verbose"])
    assert rc == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["work_id"] == "90876776"
    assert rows[0]["status"] == "identified"
    assert "Identified 1" in capsys.readouterr().err


def test_split_and_apply_identify_choices():
    rows = [
        {"book_id": 1, "status": "identified", "work_id": "9", "title": "One"},
        {
            "book_id": 2,
            "status": "ambiguous",
            "title": "Storm",
            "candidates": [
                {"work_id": "11", "title": "Time Storm", "author": "A"},
                {"work_id": "22", "title": "Time Storm", "author": "B"},
            ],
        },
        {"book_id": 3, "status": "not_found", "title": "Nope"},
    ]
    identified, ambiguous, failed = split_identify_records(rows)
    assert [row["book_id"] for row in identified] == [1]
    assert [row["book_id"] for row in ambiguous] == [2]
    assert [row["book_id"] for row in failed] == [3]
    merged = apply_identify_choices(identified + ambiguous, {"2": "22"})
    assert [row["work_id"] for row in merged] == ["9", "22"]
    assert merged[1]["title"] == "Time Storm"
    skipped = apply_identify_choices(ambiguous, {"2": ""})
    assert skipped == []


def test_parse_work_page_reads_meta():
    html = (FIXTURES / "work_page_full.html").read_text(encoding="utf-8")
    work = parse_work_page(html, url="https://archiveofourown.org/works/90876776")
    assert work is not None
    assert work.work_id == "90876776"
    assert work.title == "Time Storm"
    assert work.author == "whovian"
    assert work.fandoms == ["Doctor Who (2005)"]
    assert work.relationships == ["Tenth Doctor/Rose Tyler"]
    assert "Fluff" in work.tags
    assert "Tenth Doctor" in work.tags
    assert work.metadata.words == 12345
    assert work.metadata.kudos == 80
    assert work.series[0].series_id == "6133236"
    assert work.series[0].position == 2


def test_scrape_works_from(tmp_path: Path, monkeypatch):
    html = (FIXTURES / "work_page_full.html").read_text(encoding="utf-8")
    monkeypatch.setattr("ao3kit.scrape.create_session", lambda *a, **k: object())
    monkeypatch.setattr("ao3kit.scrape.fetch_page", lambda url, session=None: html)
    seed = tmp_path / "seed.jsonl"
    out = tmp_path / "out.jsonl"
    seed.write_text(
        json.dumps(
            {
                "work_id": "90876776",
                "url": "https://archiveofourown.org/works/90876776",
                "book_id": 4,
                "title": "old",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = scrape_main(["-o", str(out), "--works-from", str(seed), "--verbose"])
    assert rc == 0
    record = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert record["title"] == "Time Storm"
    assert record["book_id"] == 4
    assert record["fandoms"] == ["Doctor Who (2005)"]
    assert record["metadata"]["words"] == 12345


def test_scrape_known_works_continues_after_fetch_error(monkeypatch):
    from ao3kit.scrape import scrape_known_works

    html = (FIXTURES / "work_page_full.html").read_text(encoding="utf-8")
    calls: list[str] = []

    def fake_fetch(url, session=None):
        calls.append(url)
        if "90876776" in url:
            return html
        raise RuntimeError("network down")

    monkeypatch.setattr("ao3kit.scrape.fetch_page", fake_fetch)
    records = scrape_known_works(
        [
            {
                "work_id": "90876776",
                "url": "https://archiveofourown.org/works/90876776",
                "book_id": 4,
                "title": "ok",
            },
            {
                "work_id": "99999999",
                "url": "https://archiveofourown.org/works/99999999",
                "book_id": 5,
                "title": "bad",
            },
        ],
        session=object(),
    )
    assert len(calls) == 2
    assert len(records) == 2
    ok = next(row for row in records if row["work_id"] == "90876776")
    bad = next(row for row in records if row["work_id"] == "99999999")
    assert ok["title"] == "Time Storm"
    assert ok["book_id"] == 4
    assert bad["scrape_error"] == "network down"


def test_scrape_works_from_partial_success_exit_code(tmp_path: Path, monkeypatch):
    html = (FIXTURES / "work_page_full.html").read_text(encoding="utf-8")
    monkeypatch.setattr("ao3kit.scrape.create_session", lambda *a, **k: object())

    def fake_fetch(url, session=None):
        if "90876776" in url:
            return html
        raise RuntimeError("network down")

    monkeypatch.setattr("ao3kit.scrape.fetch_page", fake_fetch)
    seed = tmp_path / "seed.jsonl"
    out = tmp_path / "out.jsonl"
    seed.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "work_id": "90876776",
                        "url": "https://archiveofourown.org/works/90876776",
                        "book_id": 4,
                    }
                ),
                json.dumps(
                    {
                        "work_id": "99999999",
                        "url": "https://archiveofourown.org/works/99999999",
                        "book_id": 5,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rc = scrape_main(["-o", str(out), "--works-from", str(seed), "--verbose"])
    assert rc == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert any(row.get("title") == "Time Storm" for row in rows)
    assert any(row.get("scrape_error") for row in rows)
