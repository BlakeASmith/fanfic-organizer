from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from ao3kit.scrape import (
    WorkRecord,
    parse_search_page,
    parse_series_url,
    parse_url_payload,
    parse_work_blurb,
)
from ao3kit.series import (
    expand_with_series,
    fill_record_dicts,
    fill_series_from_work_pages,
    parse_series_from_html,
    scrape_series,
    series_membership_is_complete,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_series_url():
    series_id, page = parse_series_url(
        "https://archiveofourown.org/series/6133236?page=2"
    )
    assert series_id == "6133236"
    assert page == 2


def test_parse_url_payload_series():
    payload = parse_url_payload("https://archiveofourown.org/series/6133236")
    assert payload["kind"] == "series"
    assert payload["series_id"] == "6133236"
    assert payload["search_url"].endswith("/series/6133236")


def test_parse_work_blurb_series_membership():
    html = (FIXTURES / "work_blurbs_series.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    blurb = soup.select_one("#work_90876776")
    work = parse_work_blurb(blurb)
    assert work is not None
    assert work.work_id == "90876776"
    assert work.relationships == ["Tenth Doctor/Rose Tyler"]
    assert "Tenth Doctor/Rose Tyler" in work.tags
    assert "Fluff" in work.tags
    assert len(work.series) == 1
    membership = work.series[0]
    assert membership.series_id == "6133236"
    assert membership.position == 2
    assert membership.name == "Doctor Who:Predators of time and space"
    assert membership.url.endswith("/series/6133236")
    payload = work.to_dict()
    assert payload["relationships"] == ["Tenth Doctor/Rose Tyler"]
    assert WorkRecord.from_dict(payload).relationships == ["Tenth Doctor/Rose Tyler"]

    standalone = parse_work_blurb(soup.select_one("#work_100"))
    assert standalone is not None
    assert standalone.series == []
    assert "series" not in standalone.to_dict()


def test_parse_search_page_series_blurbs():
    html = (FIXTURES / "work_blurbs_series.html").read_text(encoding="utf-8")
    page = parse_search_page(html)
    assert [work.work_id for work in page.works] == ["90876776", "100"]
    assert page.works[0].series[0].position == 2


def test_parse_series_from_work_page():
    html = (FIXTURES / "work_page_series.html").read_text(encoding="utf-8")
    memberships = parse_series_from_html(html)
    assert len(memberships) == 1
    assert memberships[0].series_id == "6133236"
    assert memberships[0].position == 2
    assert memberships[0].name == "Doctor Who:Predators of time and space"


def test_scrape_series_parses_listing(monkeypatch):
    html = (FIXTURES / "series_page.html").read_text(encoding="utf-8")
    monkeypatch.setattr("ao3kit.series.fetch_page", lambda *a, **k: html)
    works = scrape_series("6133236", session=object())
    assert [work.work_id for work in works] == ["111", "90876776"]
    assert works[0].series[0].position == 1
    assert works[1].series[0].position == 2


def test_expand_with_series_adds_missing_parts(monkeypatch):
    seed = WorkRecord(
        work_id="90876776",
        url="https://archiveofourown.org/works/90876776",
        title="Time Storm",
        series=[],
    )
    series_html = (FIXTURES / "series_page.html").read_text(encoding="utf-8")
    work_html = (FIXTURES / "work_page_series.html").read_text(encoding="utf-8")

    def fake_fetch(url, session=None):
        if "/series/" in url:
            return series_html
        return work_html

    monkeypatch.setattr("ao3kit.series.fetch_page", fake_fetch)
    expanded = expand_with_series(
        [seed],
        session=object(),
        fetch_missing=True,
    )
    assert [work.work_id for work in expanded] == ["90876776", "111"]
    assert expanded[0].series[0].series_id == "6133236"
    assert expanded[1].title == "Part One"


def test_work_record_series_round_trip():
    work = WorkRecord.from_dict(
        {
            "work_id": "90876776",
            "title": "Time Storm",
            "series": [
                {
                    "series_id": "6133236",
                    "name": "Doctor Who:Predators of time and space",
                    "url": "https://archiveofourown.org/series/6133236",
                    "position": 2,
                }
            ],
        }
    )
    assert work is not None
    payload = work.to_dict()
    assert payload["series"][0]["position"] == 2
    assert payload.get("relationships") in (None, [])
    again = WorkRecord.from_dict(payload)
    assert again is not None
    assert again.series[0].name == "Doctor Who:Predators of time and space"


def test_fill_series_from_work_pages(monkeypatch):
    html = (FIXTURES / "work_page_series.html").read_text(encoding="utf-8")
    monkeypatch.setattr("ao3kit.series.fetch_page", lambda *a, **k: html)
    work = WorkRecord(
        work_id="90876776",
        url="https://archiveofourown.org/works/90876776",
        title="Time Storm",
    )
    looked_up = fill_series_from_work_pages([work], session=object())
    assert looked_up == {"90876776"}
    assert series_membership_is_complete(work)
    assert work.series[0].position == 2


def test_fill_series_skips_complete_membership(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url, session=None):
        calls.append(url)
        return "<html></html>"

    monkeypatch.setattr("ao3kit.series.fetch_page", fake_fetch)
    work = WorkRecord.from_dict(
        {
            "work_id": "90876776",
            "title": "Time Storm",
            "series": [
                {
                    "series_id": "6133236",
                    "name": "Doctor Who:Predators of time and space",
                    "url": "https://archiveofourown.org/series/6133236",
                    "position": 2,
                }
            ],
        }
    )
    assert work is not None
    looked_up = fill_series_from_work_pages([work], session=object())
    assert looked_up == set()
    assert calls == []


def test_fill_record_dicts_preserves_cleaned(monkeypatch):
    html = (FIXTURES / "work_page_series.html").read_text(encoding="utf-8")
    monkeypatch.setattr("ao3kit.series.fetch_page", lambda *a, **k: html)
    records = [
        {
            "work_id": "90876776",
            "title": "Time Storm",
            "cleaned": {"simplified": ["Fluff"]},
        }
    ]
    filled = fill_record_dicts(records, session=object())
    assert filled[0]["cleaned"] == {"simplified": ["Fluff"]}
    assert filled[0]["series"][0]["series_id"] == "6133236"
    assert len(filled) == 1


def test_fill_record_dicts_keeps_records_without_work_id(monkeypatch):
    monkeypatch.setattr("ao3kit.series.fill_series_from_work_pages", lambda *a, **k: set())
    records = [
        {"title": "Local", "calibre_uuid": "abc", "tags": ["Fluff"]},
        {"work_id": "1", "title": "AO3"},
    ]
    filled = fill_record_dicts(records, session=object())
    assert [row.get("title") for row in filled] == ["Local", "AO3"]
    assert filled[0]["calibre_uuid"] == "abc"
