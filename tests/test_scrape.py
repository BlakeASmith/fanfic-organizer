from __future__ import annotations

import json
from pathlib import Path

import pytest

from ao3kit.epubs import DownloadReport
from ao3kit.scrape import WorkRecord
from ao3kit.scrape import main as scrape_main
from ao3kit.scrape import parse_url_payload


SAMPLE_URL = (
    "https://archiveofourown.org/works?"
    "work_search%5Bsort_column%5D=kudos_count"
    "&work_search%5Blanguage_id%5D=en"
    "&tag_id=Doctor+Who+%282005%29"
    "&page=2"
)


def test_parse_url_payload_extracts_criteria():
    payload = parse_url_payload(SAMPLE_URL)
    assert payload["start_page"] == 2
    assert payload["criteria"]["sort_column"] == "kudos_count"
    assert payload["criteria"]["language_id"] == "en"
    assert "Doctor Who" in (payload["criteria"]["tag_id"] or "")
    assert "archiveofourown.org/works/search?" in payload["search_url"]


def test_scrape_parse_only_prints_json(capsys):
    rc = scrape_main(["--parse-only", "--url", SAMPLE_URL])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["start_page"] == 2
    assert "Doctor Who" in (payload["criteria"]["tag_id"] or "")


def test_scrape_parse_only_requires_url():
    with pytest.raises(SystemExit):
        scrape_main(["--parse-only"])


def test_scrape_requires_output_without_parse_only():
    with pytest.raises(SystemExit):
        scrape_main(["--tag-id", "Doctor Who (2005)"])


def test_parse_works_search_url():
    payload = parse_url_payload(
        "https://archiveofourown.org/works/search?"
        "work_search%5Bquery%5D=title%3A%20%22Home%22"
    )
    assert payload["kind"] == "search"
    assert payload["list_path"] == "/works/search"
    assert payload["criteria"]["query"] == 'title: "Home"'
    assert "archiveofourown.org/works/search?" in payload["search_url"]
    assert "Home" in payload["search_url"]


def test_parse_tag_works_url():
    payload = parse_url_payload(
        "https://archiveofourown.org/tags/Doctor%20Who%20(2005)/works"
    )
    assert payload["criteria"]["tag_id"] == "Doctor Who (2005)"
    assert payload["start_page"] == 1
    assert "archiveofourown.org/works/search?" in payload["search_url"]
    assert "Doctor" in payload["search_url"]


def test_parse_tag_works_url_keeps_query_filters():
    payload = parse_url_payload(
        "https://archiveofourown.org/tags/Doctor%20Who%20(2005)/works"
        "?work_search%5Bsort_column%5D=hits&page=3"
    )
    assert payload["criteria"]["tag_id"] == "Doctor Who (2005)"
    assert payload["criteria"]["sort_column"] == "hits"
    assert payload["start_page"] == 3


def test_parse_only_tag_works_url(capsys):
    rc = scrape_main(
        [
            "--parse-only",
            "--url",
            "https://archiveofourown.org/tags/Doctor%20Who%20(2005)/works",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["criteria"]["tag_id"] == "Doctor Who (2005)"


def test_parse_work_page_url_rejected():
    with pytest.raises(ValueError, match="AO3"):
        parse_url_payload("https://archiveofourown.org/works/50448730")


def test_scrape_download_uses_same_session(tmp_path: Path, monkeypatch, capsys):
    work = WorkRecord(
        work_id="1",
        url="https://archiveofourown.org/works/1",
        title="Test Work",
    )
    session = object()
    called: dict = {}

    monkeypatch.setattr("ao3kit.scrape.scrape_search", lambda *a, **k: [work])
    monkeypatch.setattr("ao3kit.scrape.create_session", lambda *a, **k: session)

    def fake_download(records, dest, sess, **kwargs):
        called["n"] = len(records)
        called["dest"] = Path(dest)
        called["session"] = sess
        called["simplify"] = kwargs.get("simplify_tags")
        called["make_zip"] = kwargs.get("make_zip")
        return DownloadReport()

    monkeypatch.setattr("ao3kit.epubs.download_records", fake_download)
    out = tmp_path / "bundle" / "results.jsonl"
    out.parent.mkdir()
    rc = scrape_main(
        [
            "-o",
            str(out),
            "--tag-id",
            "Doctor Who (2005)",
            "--download",
            "--no-zip",
            "--verbose",
        ]
    )
    assert rc == 0
    assert called["n"] == 1
    assert called["session"] is session
    assert called["simplify"] is False
    assert called["make_zip"] is False
    assert called["dest"] == out.parent
    err = capsys.readouterr().err
    assert "Downloading 1 EPUB" in err
    assert "Wrote 1 matching work." in err


def test_scrape_download_skipped_when_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("ao3kit.scrape.scrape_search", lambda *a, **k: [])
    monkeypatch.setattr("ao3kit.scrape.create_session", lambda *a, **k: object())

    def boom(*a, **k):
        raise AssertionError("download should not run when nothing matched")

    monkeypatch.setattr("ao3kit.epubs.download_records", boom)
    rc = scrape_main(
        [
            "-o",
            str(tmp_path / "results.jsonl"),
            "--tag-id",
            "Doctor Who (2005)",
            "--download",
        ]
    )
    assert rc == 0
    assert (tmp_path / "results.jsonl").is_file()


def test_scrape_writes_jsonl_as_works_match(tmp_path: Path, monkeypatch):
    work = WorkRecord(
        work_id="1",
        url="https://archiveofourown.org/works/1",
        title="Test Work",
    )
    seen: dict[str, str] = {}

    def fake_search(*_args, **kwargs):
        on_work = kwargs.get("on_work")
        if on_work:
            on_work(work)
        seen["text"] = (tmp_path / "results.jsonl").read_text(encoding="utf-8")
        return [work]

    monkeypatch.setattr("ao3kit.scrape.scrape_search", fake_search)
    monkeypatch.setattr("ao3kit.scrape.create_session", lambda *a, **k: object())
    rc = scrape_main(
        ["-o", str(tmp_path / "results.jsonl"), "--tag-id", "Doctor Who (2005)"]
    )
    assert rc == 0
    assert '"work_id": "1"' in seen["text"]
    assert "Test Work" in seen["text"]


def test_scrape_include_series_expands_matches(tmp_path: Path, monkeypatch):
    seed = WorkRecord(
        work_id="90876776",
        url="https://archiveofourown.org/works/90876776",
        title="Time Storm",
        series=[],
    )
    extra = WorkRecord(
        work_id="111",
        url="https://archiveofourown.org/works/111",
        title="Part One",
    )

    monkeypatch.setattr("ao3kit.scrape.scrape_search", lambda *a, **k: [seed])
    monkeypatch.setattr("ao3kit.scrape.create_session", lambda *a, **k: object())
    monkeypatch.setattr(
        "ao3kit.series.expand_with_series",
        lambda works, **k: [seed, extra],
    )

    out = tmp_path / "results.jsonl"
    rc = scrape_main(
        [
            "-o",
            str(out),
            "--tag-id",
            "Doctor Who (2005)",
            "--include-series",
        ]
    )
    assert rc == 0
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert '"work_id": "111"' in lines[1]


def test_parse_only_series_url(capsys):
    rc = scrape_main(
        ["--parse-only", "--url", "https://archiveofourown.org/series/6133236"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "series"
    assert payload["series_id"] == "6133236"


def test_scrape_fill_series_from(tmp_path: Path, monkeypatch):
    seed = tmp_path / "seeds.jsonl"
    seed.write_text(
        json.dumps({"work_id": "90876776", "title": "Time Storm"}) + "\n",
        encoding="utf-8",
    )

    def fake_fill(records, **kwargs):
        out = []
        for record in records:
            row = dict(record)
            row["series"] = [
                {
                    "series_id": "6133236",
                    "name": "Doctor Who:Predators of time and space",
                    "url": "https://archiveofourown.org/series/6133236",
                    "position": 2,
                }
            ]
            out.append(row)
        return out

    monkeypatch.setattr("ao3kit.scrape.create_session", lambda *a, **k: object())
    monkeypatch.setattr("ao3kit.series.fill_record_dicts", fake_fill)
    out = tmp_path / "filled.jsonl"
    rc = scrape_main(
        ["--fill-series-from", str(seed), "-o", str(out)]
    )
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8").strip())
    assert payload["series"][0]["series_id"] == "6133236"
    assert payload["title"] == "Time Storm"


def test_scrape_download_rejected_with_parse_only():
    with pytest.raises(SystemExit):
        scrape_main(
            [
                "--parse-only",
                "--download",
                "--url",
                SAMPLE_URL,
            ]
        )
