#!/usr/bin/env python3
"""Scrape AO3 search results to JSONL."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import requests
from bs4 import BeautifulSoup

from ao3kit.http import (
    AO3_BASE,
    DEFAULT_HEADERS,
    create_session,
    get_text,
)
from ao3kit.rate import apply_request_delay

RESULT_COUNT_RE = re.compile(
    r"(?P<start>\d+)\s*-\s*(?P<end>\d+)\s+of\s+(?P<total>[\d,]+)\s+Works?",
    re.IGNORECASE,
)

# Keep plugin scrape_run.SORT_OPTIONS in sync with this list.
SORT_OPTIONS = [
    ("kudos_count", "Kudos"),
    ("hits", "Hits"),
    ("comments_count", "Comments"),
    ("bookmarks_count", "Bookmarks"),
    ("word_count", "Word count"),
    ("date_to_sort_on", "Date updated"),
    ("created_at", "Date posted"),
    ("title_to_sort_on", "Title"),
]


@dataclass
class QualityScoreConfig:
    """Quality score settings."""

    min_kudos_to_score: int = 50


def calculate_word_based_score(kudos: int, hits: int, words: int) -> float | None:
    """Word-length-adjusted kudos/hits ratio from the quality score userscript."""
    if not kudos or not hits or not words:
        return None

    eff = max(1, words / 5000)
    adjusted_hits = hits / (eff**0.4)
    return (100 * kudos) / adjusted_hits


def calculate_quality_score(
    kudos: int | None,
    hits: int | None,
    words: int | None,
    *,
    config: QualityScoreConfig | None = None,
) -> float | None:
    """Return raw word-adjusted quality score."""
    config = config or QualityScoreConfig()

    if kudos is None or hits is None or words is None or hits == 0 or words == 0:
        return None
    if kudos < config.min_kudos_to_score:
        return None

    raw_score = calculate_word_based_score(kudos, hits, words)
    if raw_score is None:
        return None

    return round(raw_score * 10) / 10


CHAPTERS_RE = re.compile(r"^(\d+)\s*/\s*(\d+|\?)$")
SERIES_ID_RE = re.compile(r"/series/(\d+)", re.IGNORECASE)
SERIES_PATH_RE = re.compile(r"^/series/(?P<id>\d+)/?$", re.IGNORECASE)
SERIES_PART_RE = re.compile(r"Part\s+(\d+)\s+of\s+", re.IGNORECASE)
PAGINATION_PAGE_RE = re.compile(r"[?&]page=(\d+)")


@dataclass
class ChapterInfo:
    display: str
    posted: int
    expected: int | None = None
    total_unknown: bool = False

    @property
    def is_complete(self) -> bool:
        if self.total_unknown or self.expected is None:
            return False
        return self.posted >= self.expected

    def to_dict(self) -> dict[str, int | str | bool | None]:
        return {
            "display": self.display,
            "posted": self.posted,
            "expected": self.expected,
            "total_unknown": self.total_unknown,
            "is_complete": self.is_complete,
        }


@dataclass
class SeriesMembership:
    """One AO3 series a work belongs to (a work may be in several)."""

    series_id: str
    name: str
    url: str
    position: int | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "series_id": self.series_id,
            "name": self.name,
            "url": self.url,
            "position": self.position,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SeriesMembership | None:
        if not isinstance(data, dict):
            return None
        series_id = str(data.get("series_id") or "").strip()
        url = str(data.get("url") or "").strip()
        if not series_id:
            match = SERIES_ID_RE.search(url)
            if match:
                series_id = match.group(1)
        if not series_id:
            return None
        name = str(data.get("name") or "").strip()
        position = data.get("position")
        if position is not None and position != "":
            try:
                position = int(position)
            except (TypeError, ValueError):
                position = None
        else:
            position = None
        return cls(
            series_id=series_id,
            name=name or f"AO3 series {series_id}",
            url=url or f"{AO3_BASE}/series/{series_id}",
            position=position,
        )


def parse_series_memberships(container: BeautifulSoup) -> list[SeriesMembership]:
    """Parse ``ul.series`` (blurbs) or ``dd.series`` (work pages)."""
    memberships: list[SeriesMembership] = []
    seen: set[str] = set()
    scopes = container.select("ul.series, dd.series")
    links: list[Any] = []
    for scope in scopes:
        links.extend(scope.select("a[href*='/series/']"))
    if not links:
        links = list(container.select("a[href*='/series/']"))

    for link in links:
        href = str(link.get("href") or "")
        match = SERIES_ID_RE.search(href)
        if not match:
            continue
        series_id = match.group(1)
        if series_id in seen:
            continue
        name = link.get_text(" ", strip=True)
        position: int | None = None
        part_in_name = SERIES_PART_RE.match(name)
        if part_in_name:
            position = int(part_in_name.group(1))
            name = name[part_in_name.end() :].strip()
            name = re.sub(r"^the\s+", "", name, flags=re.IGNORECASE)
            name = re.sub(r"\s+series$", "", name, flags=re.IGNORECASE)
        else:
            parent = getattr(link, "parent", None)
            blob = parent.get_text(" ", strip=True) if parent is not None else ""
            part = SERIES_PART_RE.search(blob)
            if part:
                position = int(part.group(1))
        name = name.strip()
        if not name:
            continue
        seen.add(series_id)
        memberships.append(
            SeriesMembership(
                series_id=series_id,
                name=name,
                url=f"{AO3_BASE}/series/{series_id}",
                position=position,
            )
        )
    return memberships


def series_id_from_url(url: str) -> str | None:
    match = SERIES_ID_RE.search(url or "")
    return match.group(1) if match else None


def parse_series_url(url: str) -> tuple[str, int]:
    """Parse an AO3 series URL into ``(series_id, page)``."""
    parsed = urlparse(url)
    if parsed.netloc and "archiveofourown.org" not in parsed.netloc:
        raise ValueError(f"Not an AO3 URL: {url}")
    path = unquote(parsed.path).rstrip("/")
    match = SERIES_PATH_RE.match(path)
    if not match:
        raise ValueError(f"Expected an AO3 series URL (/series/ID), got path {parsed.path!r}")
    params = parse_qs(parsed.query, keep_blank_values=True)
    page = _query_int(params, "page") or 1
    return match.group("id"), page


def build_series_url(series_id: str, page: int = 1) -> str:
    series_id = str(series_id).strip()
    if page > 1:
        return f"{AO3_BASE}/series/{series_id}?page={page}"
    return f"{AO3_BASE}/series/{series_id}"


def parse_pagination_max(html: str) -> int | None:
    """Largest page number linked in AO3 ``ol.pagination``, if any."""
    soup = BeautifulSoup(html, "lxml")
    nums: list[int] = []
    for anchor in soup.select("ol.pagination a"):
        href = str(anchor.get("href") or "")
        match = PAGINATION_PAGE_RE.search(href)
        if match:
            nums.append(int(match.group(1)))
        text = anchor.get_text(strip=True)
        if text.isdigit():
            nums.append(int(text))
    return max(nums) if nums else None


def parse_chapters(value: str | None) -> ChapterInfo | None:
    """Parse AO3 chapter counts like 7/7, 7/10, or 7/?."""
    if not value:
        return None

    display = value.strip()
    match = CHAPTERS_RE.match(display)
    if not match:
        return None

    posted = int(match.group(1))
    total_raw = match.group(2)
    if total_raw == "?":
        return ChapterInfo(
            display=display,
            posted=posted,
            expected=None,
            total_unknown=True,
        )

    expected = int(total_raw)
    return ChapterInfo(
        display=display,
        posted=posted,
        expected=expected,
        total_unknown=False,
    )


@dataclass
class SearchCriteria:
    tag_id: str | None = None
    sort_column: str = "kudos_count"
    complete: bool | None = None
    words_from: int | None = None
    words_to: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    query: str | None = None
    language_id: str | None = "en"
    other_tag_names: str = ""
    excluded_tag_names: str = ""
    crossover: str = ""
    relationship_ids: list[int] = field(default_factory=list)
    freeform_ids: list[int] = field(default_factory=list)
    character_ids: list[int] = field(default_factory=list)
    creators: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchCriteria:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})

    def is_usable(self) -> bool:
        return bool(
            self.tag_id
            or self.query
            or self.creators
            or self.other_tag_names
            or self.relationship_ids
            or self.freeform_ids
            or self.character_ids
        )


@dataclass
class WorkMetadata:
    language: str | None = None
    words: int | None = None
    chapters: ChapterInfo | None = None
    comments: int | None = None
    kudos: int | None = None
    bookmarks: int | None = None
    hits: int | None = None

    def quality_score(self, config: QualityScoreConfig | None = None) -> float | None:
        return calculate_quality_score(
            self.kudos, self.hits, self.words, config=config
        )

    def to_dict(self, *, score_config: QualityScoreConfig | None = None) -> dict:
        return {
            "language": self.language,
            "words": self.words,
            "chapters": self.chapters.to_dict() if self.chapters else None,
            "comments": self.comments,
            "kudos": self.kudos,
            "bookmarks": self.bookmarks,
            "hits": self.hits,
            "quality_score": self.quality_score(score_config),
        }


@dataclass
class WorkRecord:
    work_id: str
    url: str
    title: str
    author: str | None = None
    fandoms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    date: str | None = None
    metadata: WorkMetadata = field(default_factory=WorkMetadata)
    series: list[SeriesMembership] = field(default_factory=list)

    def to_dict(self, *, score_config: QualityScoreConfig | None = None) -> dict:
        data = {
            "work_id": self.work_id,
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "fandoms": self.fandoms,
            "tags": self.tags,
            "date": self.date,
            "metadata": self.metadata.to_dict(score_config=score_config),
        }
        if self.relationships:
            data["relationships"] = list(self.relationships)
        if self.series:
            data["series"] = [item.to_dict() for item in self.series]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> WorkRecord | None:
        if not isinstance(data, dict):
            return None
        work_id = str(data.get("work_id") or "").strip()
        url = str(data.get("url") or "").strip()
        if not work_id:
            match = re.search(r"/works/(\d+)", url)
            if match:
                work_id = match.group(1)
        if not work_id:
            return None
        if not url:
            url = f"{AO3_BASE}/works/{work_id}"
        tags = data.get("tags")
        tag_list = [str(t) for t in tags] if isinstance(tags, list) else []
        fandoms = data.get("fandoms")
        fandom_list = (
            [str(item) for item in fandoms] if isinstance(fandoms, list) else []
        )
        relationships = data.get("relationships")
        relationship_list = (
            [str(item) for item in relationships]
            if isinstance(relationships, list)
            else []
        )
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        chapters = None
        raw_chapters = meta.get("chapters") if isinstance(meta, dict) else None
        if isinstance(raw_chapters, dict):
            chapters = parse_chapters(str(raw_chapters.get("display") or "") or None)
        series: list[SeriesMembership] = []
        for item in data.get("series") or []:
            membership = SeriesMembership.from_dict(
                item if isinstance(item, dict) else None
            )
            if membership is not None:
                series.append(membership)
        return cls(
            work_id=work_id,
            url=url,
            title=str(data.get("title") or f"AO3 work {work_id}"),
            author=str(data["author"]) if data.get("author") else None,
            fandoms=fandom_list,
            tags=tag_list,
            relationships=relationship_list,
            date=str(data["date"]) if data.get("date") else None,
            metadata=WorkMetadata(
                language=meta.get("language"),
                words=meta.get("words") if isinstance(meta.get("words"), int) else None,
                chapters=chapters,
                comments=meta.get("comments")
                if isinstance(meta.get("comments"), int)
                else None,
                kudos=meta.get("kudos") if isinstance(meta.get("kudos"), int) else None,
                bookmarks=meta.get("bookmarks")
                if isinstance(meta.get("bookmarks"), int)
                else None,
                hits=meta.get("hits") if isinstance(meta.get("hits"), int) else None,
            ),
            series=series,
        )


@dataclass
class SearchPage:
    works: list[WorkRecord]
    total_results: int | None = None
    page_start: int | None = None
    page_end: int | None = None


def encode_tag_id(tag_name: str) -> str:
    return tag_name.replace(".", "*d*")


def decode_tag_id(tag_id: str) -> str:
    return tag_id.replace("*d*", ".")


TAG_WORKS_PATH_RE = re.compile(r"^/tags/(?P<tag>.+)/works$", re.IGNORECASE)


def _tag_id_from_works_path(path: str) -> str | None:
    """Return the fandom/tag name from ``/tags/{name}/works``, if that is the path."""
    match = TAG_WORKS_PATH_RE.match(path)
    if not match:
        return None
    tag = unquote(match.group("tag")).strip()
    return decode_tag_id(tag) if tag else None


def _query_value(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    return values[0]


def _query_int(params: dict[str, list[str]], key: str) -> int | None:
    value = _query_value(params, key)
    if value is None or value == "":
        return None
    return int(value.replace(",", ""))


def _query_int_list(params: dict[str, list[str]], key: str) -> list[int]:
    values: list[int] = []
    for value in params.get(key, []):
        if value.isdigit():
            values.append(int(value))
    return values


def parse_search_url(url: str) -> tuple[SearchCriteria, int]:
    """Parse an AO3 works search URL into criteria and a starting page number.

    Accepts the filtered ``/works?...`` form and tag listing URLs
    (``/tags/{name}/works``), including query-string filters on either.
    """
    parsed = urlparse(url)
    if parsed.netloc and "archiveofourown.org" not in parsed.netloc:
        raise ValueError(f"Not an AO3 URL: {url}")

    path = unquote(parsed.path).rstrip("/")
    path_tag_id = _tag_id_from_works_path(path)
    if path not in ("/works", "") and path_tag_id is None:
        raise ValueError(
            "Expected an AO3 works search URL (/works or /tags/.../works), "
            f"got path {parsed.path!r}"
        )

    params = parse_qs(parsed.query, keep_blank_values=True)

    complete_raw = _query_value(params, "work_search[complete]")
    complete: bool | None = None
    if complete_raw == "T":
        complete = True
    elif complete_raw == "F":
        complete = False

    tag_id = _query_value(params, "tag_id")
    if tag_id is not None:
        tag_id = decode_tag_id(tag_id)
    elif path_tag_id:
        tag_id = path_tag_id

    page = _query_int(params, "page") or 1

    criteria = SearchCriteria(
        tag_id=tag_id,
        sort_column=_query_value(params, "work_search[sort_column]") or "kudos_count",
        complete=complete,
        words_from=_query_int(params, "work_search[words_from]"),
        words_to=_query_int(params, "work_search[words_to]"),
        date_from=_query_value(params, "work_search[date_from]"),
        date_to=_query_value(params, "work_search[date_to]"),
        query=_query_value(params, "work_search[query]"),
        language_id=_query_value(params, "work_search[language_id]"),
        other_tag_names=_query_value(params, "work_search[other_tag_names]") or "",
        excluded_tag_names=_query_value(params, "work_search[excluded_tag_names]") or "",
        crossover=_query_value(params, "work_search[crossover]") or "",
        relationship_ids=_query_int_list(
            params, "include_work_search[relationship_ids][]"
        ),
        freeform_ids=_query_int_list(params, "include_work_search[freeform_ids][]"),
        character_ids=_query_int_list(params, "include_work_search[character_ids][]"),
        creators=_query_value(params, "work_search[creators]") or "",
    )
    return criteria, page


def build_search_url(criteria: SearchCriteria, page: int = 1) -> str:
    params: list[tuple[str, str]] = [
        ("commit", "Sort and Filter"),
        ("work_search[sort_column]", criteria.sort_column),
        ("work_search[other_tag_names]", criteria.other_tag_names),
        ("work_search[excluded_tag_names]", criteria.excluded_tag_names),
        ("work_search[crossover]", criteria.crossover),
        ("work_search[words_from]", str(criteria.words_from or "")),
        ("work_search[words_to]", str(criteria.words_to or "")),
        ("work_search[date_from]", criteria.date_from or ""),
        ("work_search[date_to]", criteria.date_to or ""),
        ("work_search[query]", criteria.query or ""),
        ("work_search[language_id]", criteria.language_id or ""),
        ("work_search[creators]", criteria.creators or ""),
    ]

    if criteria.complete is True:
        params.append(("work_search[complete]", "T"))
    elif criteria.complete is False:
        params.append(("work_search[complete]", "F"))

    for relationship_id in criteria.relationship_ids:
        params.append(
            ("include_work_search[relationship_ids][]", str(relationship_id))
        )
    for freeform_id in criteria.freeform_ids:
        params.append(("include_work_search[freeform_ids][]", str(freeform_id)))
    for character_id in criteria.character_ids:
        params.append(("include_work_search[character_ids][]", str(character_id)))

    if criteria.tag_id:
        params.append(("tag_id", encode_tag_id(criteria.tag_id)))

    if page > 1:
        params.append(("page", str(page)))

    return f"{AO3_BASE}/works?{urlencode(params, quote_via=quote)}"


def parse_url_payload(url: str) -> dict[str, Any]:
    """Parse an AO3 search or series URL into JSON (``POST /scrape/parse-url``)."""
    try:
        series_id, start_page = parse_series_url(url)
    except ValueError:
        series_id = None
        start_page = 1
    if series_id:
        series_url = build_series_url(series_id, page=start_page)
        return {
            "kind": "series",
            "series_id": series_id,
            "start_page": start_page,
            "series_url": series_url,
            "search_url": series_url,
        }
    criteria, start_page = parse_search_url(url)
    return {
        "kind": "search",
        "criteria": asdict(criteria),
        "start_page": start_page,
        "search_url": build_search_url(criteria, page=start_page),
    }


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = value.strip().replace(",", "")
    return int(cleaned) if cleaned else None


def parse_result_count(soup: BeautifulSoup) -> tuple[int | None, int | None, int | None]:
    main = soup.select_one("#main") or soup
    heading = main.find("h2")
    if not heading:
        return None, None, None

    match = RESULT_COUNT_RE.search(heading.get_text(" ", strip=True))
    if not match:
        return None, None, None

    return (
        int(match.group("start")),
        int(match.group("end")),
        parse_int(match.group("total")),
    )


def _stat_value(blurb: BeautifulSoup, stat_class: str) -> str | None:
    node = blurb.select_one(f"dl.stats dd.{stat_class}")
    return node.get_text(strip=True) if node else None


def parse_work_blurb(blurb: BeautifulSoup) -> WorkRecord | None:
    work_id = blurb.get("id", "").removeprefix("work_")
    title_link = blurb.select_one("h4.heading a[href^='/works/']")
    if not work_id and title_link:
        work_id = title_link.get("href", "").rstrip("/").split("/")[-1]
    if not work_id or not title_link:
        return None

    author_link = blurb.select_one("h4.heading a[rel='author']")

    return WorkRecord(
        work_id=work_id,
        url=f"{AO3_BASE}/works/{work_id}",
        title=title_link.get_text(strip=True),
        author=author_link.get_text(strip=True) if author_link else None,
        fandoms=[
            link.get_text(strip=True)
            for link in blurb.select("h5.fandoms a.tag")
        ],
        tags=[
            link.get_text(strip=True)
            for link in blurb.select("ul.tags.commas a.tag")
        ],
        relationships=[
            link.get_text(strip=True)
            for link in blurb.select("ul.tags.commas li.relationships a.tag")
        ],
        date=(
            blurb.select_one("p.datetime").get_text(strip=True)
            if blurb.select_one("p.datetime")
            else None
        ),
        metadata=WorkMetadata(
            language=_stat_value(blurb, "language"),
            words=parse_int(_stat_value(blurb, "words")),
            chapters=parse_chapters(_stat_value(blurb, "chapters")),
            comments=parse_int(_stat_value(blurb, "comments")),
            kudos=parse_int(_stat_value(blurb, "kudos")),
            bookmarks=parse_int(_stat_value(blurb, "bookmarks")),
            hits=parse_int(_stat_value(blurb, "hits")),
        ),
        series=parse_series_memberships(blurb),
    )


def parse_search_page(html: str) -> SearchPage:
    soup = BeautifulSoup(html, "lxml")
    page_start, page_end, total_results = parse_result_count(soup)
    works = [
        record
        for blurb in soup.select("li.work.blurb")
        if (record := parse_work_blurb(blurb))
    ]
    return SearchPage(
        works=works,
        total_results=total_results,
        page_start=page_start,
        page_end=page_end,
    )


def fetch_page(url: str, session: requests.Session | None = None) -> str:
    """Fetch an AO3 page with shared retry / Cloudflare / adult-gate handling."""
    client = session or create_session()
    return get_text(client, url, view_adult=True, timeout=60)


def work_matches_filters(
    work: WorkRecord,
    *,
    min_score: float | None = None,
    min_kudos: int | None = None,
    min_words: int | None = None,
    complete_only: bool = False,
    score_config: QualityScoreConfig | None = None,
) -> bool:
    if complete_only:
        chapters = work.metadata.chapters
        if chapters is None or not chapters.is_complete:
            return False
    if min_score is not None:
        quality_score = work.metadata.quality_score(score_config)
        if quality_score is None or quality_score <= min_score:
            return False
    if min_kudos is not None:
        kudos = work.metadata.kudos
        if kudos is None or kudos < min_kudos:
            return False
    if min_words is not None:
        words = work.metadata.words
        if words is None or words < min_words:
            return False
    return True


def scrape_search(
    criteria: SearchCriteria,
    *,
    max_results: int | None = None,
    min_score: float | None = None,
    min_kudos: int | None = None,
    min_words: int | None = None,
    complete_only: bool = False,
    request_delay: float | None = None,
    start_page: int = 1,
    score_config: QualityScoreConfig | None = None,
    session: requests.Session | None = None,
    on_page: Callable[[SearchPage, str], None] | None = None,
    on_work: Callable[[WorkRecord], None] | None = None,
) -> list[WorkRecord]:
    session = session or create_session()
    apply_request_delay(request_delay)
    matched: list[WorkRecord] = []
    page = start_page

    while True:
        url = build_search_url(criteria, page=page)
        search_page = parse_search_page(fetch_page(url, session=session))

        if on_page:
            on_page(search_page, url)

        for work in search_page.works:
            if not work_matches_filters(
                work,
                min_score=min_score,
                min_kudos=min_kudos,
                min_words=min_words,
                complete_only=complete_only,
                score_config=score_config,
            ):
                continue
            matched.append(work)
            if on_work:
                on_work(work)
            if max_results is not None and len(matched) >= max_results:
                return matched

        if not search_page.works:
            break
        if search_page.page_end is None or search_page.total_results is None:
            break
        if search_page.page_end >= search_page.total_results:
            break

        page += 1

    return matched


def write_jsonl(
    works: list[WorkRecord],
    output: TextIO | Path | str,
    *,
    score_config: QualityScoreConfig | None = None,
) -> None:
    write_jsonl_dicts(
        [work.to_dict(score_config=score_config) for work in works],
        output,
    )


def write_jsonl_dicts(records: list[dict[str, Any]], output: TextIO | Path | str) -> None:
    if isinstance(output, (str, Path)):
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return

    for record in records:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")


def download_scraped_works(
    works: list[WorkRecord],
    dest_dir: str | Path,
    session: requests.Session,
    *,
    score_config: QualityScoreConfig | None = None,
    request_delay: float | None = None,
    make_zip: bool = False,
    zip_path: str | Path | None = None,
    simplify_tags: bool = False,
    verbose: bool = False,
):
    """Download native EPUBs for scrape matches using the same session."""
    from ao3kit.epubs import (
        ZIP_NAME,
        download_records,
        format_download_outcome_line,
        format_download_report_line,
    )

    dest = Path(dest_dir)
    records = [work.to_dict(score_config=score_config) for work in works]
    resolved_zip: Path | None = None
    if make_zip:
        if zip_path is None:
            resolved_zip = dest / ZIP_NAME
        else:
            zip_arg = Path(zip_path)
            resolved_zip = zip_arg if zip_arg.is_absolute() else dest / zip_arg

    def on_outcome(outcome, index: int, total: int) -> None:
        if not verbose:
            return
        print(format_download_outcome_line(outcome, index, total), file=sys.stderr)

    on_status = (lambda msg: print(msg, file=sys.stderr)) if verbose else None
    if verbose:
        noun = "EPUB" if len(records) == 1 else "EPUBs"
        print(f"Downloading {len(records)} {noun}…", file=sys.stderr)

    report = download_records(
        records,
        dest,
        session,
        request_delay=request_delay,
        make_zip=make_zip,
        zip_path=resolved_zip,
        on_outcome=on_outcome,
        simplify_tags=simplify_tags,
        on_status=on_status,
    )
    if verbose:
        print(format_download_report_line(report, dest), file=sys.stderr)
        if make_zip:
            print(f"Import zip: {resolved_zip or dest / ZIP_NAME}", file=sys.stderr)
    return report


def _similar_payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    from ao3kit.similar import (
        SimilarSelect,
        filter_records,
        load_similar_records,
        similar_payload,
    )

    records = filter_records(
        load_similar_records(args.similar_from),
        args.similar_work_id,
    )
    if not records:
        raise ValueError("No seed works found in --similar-from")
    include = [
        part.strip()
        for part in str(args.similar_include or "").split(",")
        if part.strip()
    ]
    picks = SimilarSelect(
        authors=list(args.similar_author or []),
        fandoms=list(args.similar_fandom or []),
        relationships=list(args.similar_relationship or []),
        characters=list(args.similar_character or []),
        tags=list(args.similar_tag or []),
        excluded_tags=list(args.similar_exclude or []),
        extra_query=str(args.query or "").strip(),
    )
    return similar_payload(
        records,
        picks,
        include_all=include,
        sort_column=args.sort_column,
        complete=args.complete,
        language_id=args.language_id,
        words_from=args.words_from,
        words_to=args.words_to,
        date_from=args.date_from,
        date_to=args.date_to,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape AO3 search results to JSONL.")
    parser.add_argument("-o", "--output", help="Output JSONL path")
    parser.add_argument(
        "--parse-only",
        action="store_true",
        help="Parse --url into criteria JSON on stdout; do not scrape",
    )
    parser.add_argument(
        "--url",
        help="AO3 search URL; criteria and starting page are parsed from the query string",
    )
    parser.add_argument("--criteria-file", help="JSON file with search criteria")
    parser.add_argument("--tag-id", help="Fandom tag, e.g. 'Harry Potter - J. K. Rowling'")
    parser.add_argument("--sort-column", default="kudos_count")
    parser.add_argument("--complete", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--words-from", type=int)
    parser.add_argument("--words-to", type=int)
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--query")
    parser.add_argument(
        "--creators",
        help="AO3 creator/author search (work_search[creators])",
    )
    parser.add_argument(
        "--other-tag-names",
        default="",
        help="Comma-separated extra tags the work must include",
    )
    parser.add_argument(
        "--excluded-tag-names",
        default="",
        help="Comma-separated tags to exclude",
    )
    parser.add_argument("--language-id", default="en")
    parser.add_argument("--relationship-ids", type=int, nargs="*")
    parser.add_argument("--freeform-ids", type=int, nargs="*")
    parser.add_argument("--character-ids", type=int, nargs="*")
    parser.add_argument(
        "--parse-similar",
        action="store_true",
        help="Print similar-search facets/criteria JSON from --similar-from; do not scrape",
    )
    parser.add_argument(
        "--similar-from",
        help="JSONL of seed works to search similar to (merged if multiple)",
    )
    parser.add_argument(
        "--similar-work-id",
        action="append",
        default=None,
        metavar="ID",
        help="Only use this work id from --similar-from (repeatable)",
    )
    parser.add_argument(
        "--similar-include",
        default="",
        help="Facet types to include in full: fandoms,authors,relationships,characters,tags",
    )
    parser.add_argument("--similar-fandom", action="append", default=None)
    parser.add_argument("--similar-author", action="append", default=None)
    parser.add_argument("--similar-relationship", action="append", default=None)
    parser.add_argument("--similar-character", action="append", default=None)
    parser.add_argument("--similar-tag", action="append", default=None)
    parser.add_argument("--similar-exclude", action="append", default=None)
    parser.add_argument(
        "--start-page",
        type=int,
        help="First AO3 results page (default: 1, or the page in --url)",
    )
    parser.add_argument("--max-results", type=int)
    parser.add_argument(
        "--min-score",
        type=float,
        help="Minimum raw quality score",
    )
    parser.add_argument("--min-kudos", type=int)
    parser.add_argument("--min-words", type=int)
    parser.add_argument(
        "--complete-only",
        action="store_true",
        help="Only include works where all planned chapters are posted",
    )
    parser.add_argument(
        "--min-kudos-for-score",
        type=int,
        default=50,
        help="Minimum kudos required to compute a quality score (default: 50)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help=(
            "Seconds between AO3 requests (default: config request_delay, 1.5). "
            "Tag profiles use a faster adaptive lane."
        ),
    )
    parser.add_argument("--username", help="AO3 username (or set AO3_USERNAME)")
    parser.add_argument("--password", help="AO3 password (or set AO3_PASSWORD)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download native EPUBs for matched works in this same run",
    )
    parser.add_argument(
        "--series-id",
        help="Scrape every work in this AO3 series id (from /series/ID)",
    )
    parser.add_argument(
        "--series-from",
        help="JSONL of seed works; fetch each work's series and write all parts",
    )
    parser.add_argument(
        "--fill-series-from",
        help="JSONL of seed works; look up series membership without adding other parts",
    )
    parser.add_argument(
        "--include-series",
        action="store_true",
        help="After a search, also include every other work in the same series",
    )
    parser.add_argument(
        "--epub-dir",
        help="With --download, EPUB output directory (default: folder of -o)",
    )
    parser.add_argument(
        "--zip",
        nargs="?",
        const="ao3-import.zip",
        default=None,
        metavar="PATH",
        help="With --download, also write an import zip (off by default)",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="With --download, do not write an import zip (default)",
    )
    parser.add_argument(
        "--simplify",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="With --download, run tag enrich (default: no). Pass --simplify to enable.",
    )
    args = parser.parse_args(argv)

    if args.parse_only and args.parse_similar:
        parser.error("Use only one of --parse-only or --parse-similar")
    if args.download and (args.parse_only or args.parse_similar):
        parser.error("--download cannot be combined with --parse-only/--parse-similar")

    if args.parse_only:
        if not args.url:
            parser.error("--parse-only requires --url")
        try:
            payload = parse_url_payload(args.url)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if args.parse_similar:
        if not args.similar_from:
            parser.error("--parse-similar requires --similar-from")
        try:
            payload = _similar_payload_from_args(args)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not args.output:
        parser.error("--output is required unless --parse-only/--parse-similar is set")
    if args.series_from and args.similar_from:
        parser.error("Use only one of --series-from or --similar-from")
    if args.fill_series_from and args.series_from:
        parser.error("Use only one of --fill-series-from or --series-from")
    if args.fill_series_from and args.similar_from:
        parser.error("Use only one of --fill-series-from or --similar-from")
    if args.fill_series_from and args.series_id:
        parser.error("--fill-series-from cannot be combined with --series-id")
    if args.fill_series_from and args.include_series:
        parser.error("--fill-series-from cannot be combined with --include-series")
    if args.fill_series_from and args.download:
        parser.error("--fill-series-from cannot be combined with --download")
    if args.series_id and args.similar_from:
        parser.error("--series-id cannot be combined with --similar-from")

    username = args.username or os.environ.get("AO3_USERNAME")
    password = args.password or os.environ.get("AO3_PASSWORD")
    if (username and not password) or (password and not username):
        parser.error("Both username and password are required to log in to AO3")

    session = create_session(
        username,
        password,
        on_status=(lambda msg: print(msg, file=sys.stderr)) if args.verbose else None,
    )
    score_config = QualityScoreConfig(min_kudos_to_score=args.min_kudos_for_score)

    def on_page(search_page: SearchPage, url: str) -> None:
        if args.verbose:
            total = (
                search_page.total_results
                if search_page.total_results is not None
                else "?"
            )
            page_n = _query_value(parse_qs(urlparse(url).query), "page") or "1"
            print(
                f"Fetched page {page_n} ({len(search_page.works)} listings; "
                f"site total={total})",
                file=sys.stderr,
            )

    matched_count = 0

    def on_work(work: WorkRecord) -> None:
        nonlocal matched_count
        matched_count += 1
        if not args.verbose:
            return
        if args.max_results:
            print(
                f"[{matched_count}/{args.max_results}] matched "
                f"{work.work_id} {work.title}",
                file=sys.stderr,
            )
        else:
            print(f"matched {work.work_id} {work.title}", file=sys.stderr)

    def on_status(message: str) -> None:
        if args.verbose:
            print(message, file=sys.stderr)

    start_page = args.start_page or 1
    series_id = str(args.series_id or "").strip() or None
    if args.url and not series_id:
        try:
            series_id, url_page = parse_series_url(args.url)
            if args.start_page is None:
                start_page = url_page
        except ValueError:
            series_id = None

    works: list[WorkRecord] = []

    if args.fill_series_from:
        from ao3kit.epubs import load_jsonl_records
        from ao3kit.series import fill_record_dicts

        try:
            seed_records = load_jsonl_records(args.fill_series_from)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        if not seed_records:
            parser.error("No works found in --fill-series-from")
        if args.verbose:
            print(
                f"Looking up series for {len(seed_records)} work(s).",
                file=sys.stderr,
            )
        records = fill_record_dicts(
            seed_records,
            session=session,
            request_delay=args.delay,
            on_status=on_status,
            score_config=score_config,
        )
        write_jsonl_dicts(records, args.output)
        works = [work for work in (WorkRecord.from_dict(row) for row in records) if work]
    elif args.series_from:
        from ao3kit.epubs import load_jsonl_records
        from ao3kit.series import expand_record_dicts

        try:
            seed_records = load_jsonl_records(args.series_from)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        if not seed_records:
            parser.error("No works found in --series-from")
        if args.verbose:
            print(
                f"Expanding {len(seed_records)} seed work(s) into full series.",
                file=sys.stderr,
            )
        records = expand_record_dicts(
            seed_records,
            session=session,
            request_delay=args.delay,
            fetch_missing=True,
            on_status=on_status,
            on_work=on_work,
            on_page=on_page,
            score_config=score_config,
        )
        write_jsonl_dicts(records, args.output)
        works = [work for work in (WorkRecord.from_dict(row) for row in records) if work]
    elif series_id:
        from ao3kit.series import scrape_series

        if args.verbose:
            print(f"Fetching AO3 series {series_id}…", file=sys.stderr)
            if start_page > 1:
                print(f"Starting from page {start_page}", file=sys.stderr)
        works = scrape_series(
            series_id,
            session=session,
            start_page=start_page,
            request_delay=args.delay,
            on_page=on_page,
            on_work=on_work,
        )
        write_jsonl(works, args.output, score_config=score_config)
    else:
        if args.similar_from:
            try:
                payload = _similar_payload_from_args(args)
            except (OSError, ValueError) as exc:
                parser.error(str(exc))
            criteria = SearchCriteria.from_dict(payload["criteria"])
            if args.verbose:
                n = len(payload.get("works") or [])
                print(f"Similar search from {n} seed work(s).", file=sys.stderr)
        elif args.url:
            criteria, url_page = parse_search_url(args.url)
            if args.start_page is None:
                start_page = url_page
        elif args.criteria_file:
            criteria = SearchCriteria.from_dict(
                json.loads(Path(args.criteria_file).read_text(encoding="utf-8"))
            )
        else:
            criteria = SearchCriteria(
                tag_id=args.tag_id,
                sort_column=args.sort_column,
                complete=args.complete,
                words_from=args.words_from,
                words_to=args.words_to,
                date_from=args.date_from,
                date_to=args.date_to,
                query=args.query,
                language_id=args.language_id,
                other_tag_names=args.other_tag_names or "",
                excluded_tag_names=args.excluded_tag_names or "",
                relationship_ids=args.relationship_ids or [],
                freeform_ids=args.freeform_ids or [],
                character_ids=args.character_ids or [],
                creators=args.creators or "",
            )

        if args.verbose and args.url and start_page > 1:
            print(f"Starting from page {start_page}", file=sys.stderr)

        works = scrape_search(
            criteria,
            max_results=args.max_results,
            min_score=args.min_score,
            min_kudos=args.min_kudos,
            min_words=args.min_words,
            complete_only=args.complete_only,
            request_delay=args.delay,
            start_page=start_page,
            score_config=score_config,
            session=session,
            on_page=on_page,
            on_work=on_work,
        )
        if args.include_series and works:
            from ao3kit.series import expand_with_series

            if args.verbose:
                print("Including other works from the same series…", file=sys.stderr)
            works = expand_with_series(
                works,
                session=session,
                request_delay=args.delay,
                fetch_missing=False,
                on_status=on_status,
                on_work=on_work,
                on_page=on_page,
            )
        write_jsonl(works, args.output, score_config=score_config)

    if args.verbose:
        noun = "work" if len(works) == 1 else "works"
        print(f"Wrote {len(works)} matching {noun}.", file=sys.stderr)

    if not args.download:
        return 0
    if not works:
        if args.verbose:
            print("No matching works; skipping EPUB download.", file=sys.stderr)
        return 0

    dest = Path(args.epub_dir) if args.epub_dir else Path(args.output).parent
    make_zip = bool(args.zip) and not args.no_zip
    report = download_scraped_works(
        works,
        dest,
        session,
        score_config=score_config,
        request_delay=args.delay,
        make_zip=make_zip,
        zip_path=args.zip if make_zip else None,
        simplify_tags=bool(args.simplify),
        verbose=args.verbose,
    )
    return 1 if report.failed and not report.downloaded and not report.skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
