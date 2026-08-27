from __future__ import annotations

import json
from pathlib import Path

import pytest

from ao3kit.scrape import main as scrape_main
from ao3kit.scrape import parse_search_page, parse_url_payload
from ao3kit.work_lists import WorkListTarget, parse_work_list_url, scrape_work_list


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_collection_home_url():
    target = parse_work_list_url("https://archiveofourown.org/collections/anonymous")
    assert target.kind == "collection"
    assert target.list_path == "/collections/anonymous/works"
    assert target.start_page == 1


def test_parse_collection_works_url_with_filters():
    url = (
        "https://archiveofourown.org/collections/anonymous/works"
        "?work_search%5Bsort_column%5D=hits&page=3"
    )
    target = parse_work_list_url(url)
    assert target.kind == "collection"
    assert target.list_path == "/collections/anonymous/works"
    assert target.start_page == 3
    assert target.criteria.sort_column == "hits"


def test_parse_user_works_url():
    target = parse_work_list_url(
        "https://archiveofourown.org/users/QuickSilverFox3/works?page=2"
    )
    assert target.kind == "user_works"
    assert target.list_path == "/users/QuickSilverFox3/works"
    assert target.start_page == 2


def test_parse_user_bookmarks_url_passthrough():
    url = (
        "https://archiveofourown.org/users/QuickSilverFox3/bookmarks"
        "?bookmark_search%5Bsort_column%5D=created_at&page=4"
    )
    target = parse_work_list_url(url)
    assert target.kind == "bookmarks"
    assert target.start_page == 4
    assert target.passthrough_params is not None
    assert any(
        key == "bookmark_search[sort_column]" and value == "created_at"
        for key, value in target.passthrough_params
    )


def test_build_collection_work_list_url():
    target = WorkListTarget(
        kind="collection",
        list_path="/collections/anonymous/works",
        criteria=parse_work_list_url(
            "https://archiveofourown.org/collections/anonymous/works"
            "?work_search%5Bsort_column%5D=kudos_count"
        ).criteria,
        start_page=1,
    )
    url = target.build_url(page=2)
    assert "/collections/anonymous/works?" in url
    assert "work_search%5Bsort_column%5D=kudos_count" in url
    assert "page=2" in url


def test_parse_url_payload_collection():
    payload = parse_url_payload("https://archiveofourown.org/collections/anonymous")
    assert payload["kind"] == "collection"
    assert payload["collection"] == "anonymous"
    assert payload["list_path"] == "/collections/anonymous/works"
    assert "/collections/anonymous/works" in payload["search_url"]


def test_scrape_parse_only_collection(capsys):
    rc = scrape_main(
        [
            "--parse-only",
            "--url",
            "https://archiveofourown.org/collections/anonymous/works?page=2",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "collection"
    assert payload["start_page"] == 2


def test_parse_collection_works_page_fixture():
    html = (FIXTURES / "collection_works_page.html").read_text(encoding="utf-8")
    page = parse_search_page(html)
    assert len(page.works) >= 1
    assert page.works[0].work_id.isdigit()


def test_parse_bookmark_list_fixture():
    html = (FIXTURES / "bookmark_list_page.html").read_text(encoding="utf-8")
    page = parse_search_page(html)
    assert len(page.works) >= 1
    assert page.total_results == 225


def test_scrape_work_list_uses_target_builder(monkeypatch):
    target = WorkListTarget(
        kind="collection",
        list_path="/collections/anonymous/works",
        criteria=parse_work_list_url(
            "https://archiveofourown.org/collections/anonymous/works"
        ).criteria,
        start_page=1,
    )
    built: list[str] = []

    def fake_fetch(url, session=None):
        built.append(url)
        html = (FIXTURES / "collection_works_page.html").read_text(encoding="utf-8")
        return html.replace(
            "1 - 2 of 2 Works",
            "1 - 1 of 1 Works",
        )

    monkeypatch.setattr("ao3kit.work_lists.fetch_page", fake_fetch)
    works = scrape_work_list(target, max_results=1, session=object())
    assert len(works) == 1
    assert "/collections/anonymous/works?" in built[0]


def test_parse_work_page_url_still_rejected():
    with pytest.raises(ValueError, match="AO3"):
        parse_url_payload("https://archiveofourown.org/works/50448730")
