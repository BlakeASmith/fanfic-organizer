#!/usr/bin/env python3
"""Scrape AO3 search results to JSONL."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests
from bs4 import BeautifulSoup

from ao3kit.http import (
    AO3_BASE,
    DEFAULT_HEADERS,
    create_session,
    get_text,
)
from ao3kit.rate import configure_min_interval

RESULT_COUNT_RE = re.compile(
    r"(?P<start>\d+)\s*-\s*(?P<end>\d+)\s+of\s+(?P<total>[\d,]+)\s+Works?",
    re.IGNORECASE,
)


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchCriteria:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


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
    date: str | None = None
    metadata: WorkMetadata = field(default_factory=WorkMetadata)

    def to_dict(self, *, score_config: QualityScoreConfig | None = None) -> dict:
        return {
            "work_id": self.work_id,
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "fandoms": self.fandoms,
            "tags": self.tags,
            "date": self.date,
            "metadata": self.metadata.to_dict(score_config=score_config),
        }


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
    """Parse an AO3 /works search URL into criteria and a starting page number."""
    parsed = urlparse(url)
    if parsed.netloc and "archiveofourown.org" not in parsed.netloc:
        raise ValueError(f"Not an AO3 URL: {url}")

    path = parsed.path.rstrip("/")
    if path not in ("/works", ""):
        raise ValueError(f"Expected an AO3 works search URL, got path {parsed.path!r}")

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
    request_delay: float = 5.0,
    start_page: int = 1,
    score_config: QualityScoreConfig | None = None,
    session: requests.Session | None = None,
    on_page: Callable[[SearchPage, str], None] | None = None,
    on_work: Callable[[WorkRecord], None] | None = None,
) -> list[WorkRecord]:
    session = session or create_session()
    configure_min_interval(request_delay)
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
    if isinstance(output, (str, Path)):
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for work in works:
                handle.write(
                    json.dumps(
                        work.to_dict(score_config=score_config),
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        return

    for work in works:
        output.write(
            json.dumps(
                work.to_dict(score_config=score_config),
                ensure_ascii=False,
            )
            + "\n"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape AO3 search results to JSONL.")
    parser.add_argument("-o", "--output", required=True, help="Output JSONL path")
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
    parser.add_argument("--language-id", default="en")
    parser.add_argument("--relationship-ids", type=int, nargs="*")
    parser.add_argument("--freeform-ids", type=int, nargs="*")
    parser.add_argument("--character-ids", type=int, nargs="*")
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
        default=5.0,
        help="Minimum seconds between AO3 requests (app-wide; search/download paths use a heavier floor)",
    )
    parser.add_argument("--username", help="AO3 username (or set AO3_USERNAME)")
    parser.add_argument("--password", help="AO3 password (or set AO3_PASSWORD)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    start_page = 1
    if args.url:
        criteria, start_page = parse_search_url(args.url)
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
            relationship_ids=args.relationship_ids or [],
            freeform_ids=args.freeform_ids or [],
            character_ids=args.character_ids or [],
        )

    username = args.username or os.environ.get("AO3_USERNAME")
    password = args.password or os.environ.get("AO3_PASSWORD")
    if (username and not password) or (password and not username):
        parser.error("Both username and password are required to log in to AO3")

    session = create_session(username, password)
    score_config = QualityScoreConfig(min_kudos_to_score=args.min_kudos_for_score)

    def on_page(search_page: SearchPage, url: str) -> None:
        if args.verbose:
            total = search_page.total_results if search_page.total_results is not None else "?"
            print(f"Fetched {url} ({len(search_page.works)} works; total={total})", file=sys.stderr)

    if args.verbose and username:
        print("Logged in to AO3", file=sys.stderr)
    if args.verbose and args.url:
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
    )
    write_jsonl(works, args.output, score_config=score_config)

    if args.verbose:
        print(f"Wrote {len(works)} works to {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
