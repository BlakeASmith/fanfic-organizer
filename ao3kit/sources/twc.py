"""Transformative Works and Cultures (OJS 3) journal source.

Fetches article landing pages on ``journal.transformativeworks.org``, follows
HTML galleys (inline download), and writes shared JSONL with ``source="twc"``.
Issue URLs import every article in that issue.

Pacing uses ``ao3kit.rate.wait_for_request`` (host-wide limiter).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO
from urllib.parse import urljoin, urlparse

import requests

from ao3kit.rate import USER_AGENT
from ao3kit.sources.base import SOURCE_TWC
from ao3kit.sources.extract import extract_page
from ao3kit.sources.web import (
    build_epub_for_record,
    fetch_html,
    normalize_url,
    strip_html_bodies,
    write_jsonl,
)

StatusCallback = Callable[[str], None]

TWC_HOST = "journal.transformativeworks.org"
TWC_JOURNAL_PATH = "/index.php/twc"

ARTICLE_LANDING_RE = re.compile(
    rf"^https?://{re.escape(TWC_HOST)}{re.escape(TWC_JOURNAL_PATH)}/article/view/(?P<id>\d+)/?$",
    re.I,
)
ISSUE_RE = re.compile(
    rf"^https?://{re.escape(TWC_HOST)}{re.escape(TWC_JOURNAL_PATH)}/issue/view/(?P<id>\d+)/?$",
    re.I,
)
ARTICLE_ID_IN_HTML_RE = re.compile(
    rf"{re.escape(TWC_JOURNAL_PATH)}/article/view/(?P<id>\d+)(?:/|\?|$)",
    re.I,
)
HTML_GALLEY_LINK_RE = re.compile(
    rf"/article/view/(?P<article>\d+)/(?P<galley>\d+)",
    re.I,
)
INLINE_GALLEY_RE = re.compile(
    rf"/article/download/(?P<article>\d+)/(?P<galley>\d+)",
    re.I,
)


class TwcError(RuntimeError):
    """TWC / OJS fetch or parse failure."""


@dataclass(frozen=True)
class TwcArticleRef:
    article_id: str
    url: str


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }
    )
    return session


def parse_twc_url(url: str) -> tuple[str, str] | None:
    """Return ``('issue', id)`` or ``('article', id)`` for a TWC URL."""
    text = normalize_url(url)
    if not text:
        return None
    parsed = urlparse(text)
    if (parsed.netloc or "").lower() not in {TWC_HOST, f"www.{TWC_HOST}"}:
        return None
    issue = ISSUE_RE.match(text)
    if issue:
        return "issue", issue.group("id")
    article = ARTICLE_LANDING_RE.match(text)
    if article:
        return "article", article.group("id")
    return None


def article_landing_url(article_id: str | int) -> str:
    aid = str(article_id).strip()
    return f"https://{TWC_HOST}{TWC_JOURNAL_PATH}/article/view/{aid}"


def issue_url(issue_id: str | int) -> str:
    iid = str(issue_id).strip()
    return f"https://{TWC_HOST}{TWC_JOURNAL_PATH}/issue/view/{iid}"


def work_id_for_article(article_id: str | int) -> str:
    return str(int(str(article_id).strip()))


def _meta_values(soup, name: str) -> list[str]:
    out: list[str] = []
    for tag in soup.find_all("meta", attrs={"name": name}):
        content = str(tag.get("content") or "").strip()
        if content:
            out.append(content)
    return out


def _first_meta(soup, name: str) -> str:
    values = _meta_values(soup, name)
    return values[0] if values else ""


def _abstract_from_landing(soup) -> str | None:
    section = soup.select_one("section.item.abstract")
    if section is None:
        return None
    parts: list[str] = []
    for node in section.find_all("p"):
        text = " ".join(node.get_text(" ", strip=True).split())
        if not text:
            continue
        if "Creative Commons" in text and "license" in text.casefold():
            continue
        if text.startswith("TWC Nos."):
            continue
        parts.append(text)
    joined = " ".join(parts).strip()
    return joined or None


def _html_galley_from_landing(soup, *, article_id: str) -> tuple[str, str] | None:
    """Return ``(galley_id, galley_view_url)`` for the HTML galley, if any."""
    for block in soup.select("div.item.galleys"):
        link = block.find("a", href=True)
        if link is None:
            continue
        label = link.get_text(" ", strip=True).casefold()
        if label != "html":
            continue
        href = urljoin(article_landing_url(article_id), link["href"])
        match = HTML_GALLEY_LINK_RE.search(href)
        if match and match.group("article") == article_id:
            return match.group("galley"), href
    return None


def inline_html_galley_url(article_id: str, galley_id: str) -> str:
    return (
        f"https://{TWC_HOST}{TWC_JOURNAL_PATH}/article/download/"
        f"{article_id}/{galley_id}?inline=1"
    )


def parse_issue_article_ids(html: str) -> list[str]:
    """Article ids listed on an OJS issue TOC (landing URLs only)."""
    from ao3kit.htmlsoup import parse_html

    soup = parse_html(html)
    seen: set[str] = set()
    ordered: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        match = ARTICLE_ID_IN_HTML_RE.search(href)
        if not match:
            continue
        aid = match.group("id")
        # Skip galley subpaths (article/view/ID/GALLEY).
        if re.search(rf"/article/view/{aid}/\d+", href, re.I):
            continue
        label = tag.get_text(" ", strip=True).casefold()
        if label in {"html", "pdf", "appendix"}:
            continue
        if aid in seen:
            continue
        seen.add(aid)
        ordered.append(aid)
    return ordered


def parse_landing_metadata(html: str, *, article_id: str, url: str) -> dict[str, Any]:
    from ao3kit.htmlsoup import parse_html

    soup = parse_html(html)
    title = _first_meta(soup, "citation_title") or _first_meta(soup, "DC.Title")
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 is not None else ""
    title = title.strip() or f"TWC article {article_id}"

    authors = _meta_values(soup, "citation_author")
    if not authors:
        authors = _meta_values(soup, "DC.Creator.PersonalName")
    author = ", ".join(authors) if authors else None

    summary = _abstract_from_landing(soup)
    tags = _meta_values(soup, "citation_keywords")
    doi = _first_meta(soup, "citation_doi") or _first_meta(soup, "DC.Identifier.DOI")
    date = _first_meta(soup, "citation_publication_date") or _first_meta(
        soup, "DC.Date.dateSubmitted"
    )
    if date and len(date) >= 10:
        date = date[:10]

    galley = _html_galley_from_landing(soup, article_id=article_id)
    return {
        "title": title,
        "author": author,
        "summary": summary,
        "tags": tags,
        "date": date or None,
        "doi": doi or None,
        "html_galley": galley,
    }


def _record_from_galley_html(
    html: str,
    *,
    meta: dict[str, Any],
    article_id: str,
    url: str,
) -> dict[str, Any]:
    extracted = extract_page(html, url=url)
    title = str(meta.get("title") or extracted.title or "").strip()
    if "| Transformative Works" in title:
        title = title.split("|", 1)[0].strip()
    author = meta.get("author") or extracted.author
    summary = meta.get("summary") or extracted.summary
    tags = list(meta.get("tags") or [])
    for tag in extracted.tags:
        if tag not in tags:
            tags.append(tag)
    record: dict[str, Any] = {
        "source": SOURCE_TWC,
        "work_id": work_id_for_article(article_id),
        "url": url,
        "title": title,
        "author": author,
        "summary": summary,
        "fandoms": [],
        "tags": tags,
        "date": meta.get("date"),
        "metadata": {
            "language": extracted.language or "en",
            "words": int(extracted.word_count or 0),
        },
        "html_body": extracted.html_body,
        "publisher": "Transformative Works and Cultures",
    }
    doi = meta.get("doi")
    if doi:
        record["metadata"]["doi"] = doi
    if extracted.warnings:
        record["extract_warnings"] = list(extracted.warnings)
    return record


def fetch_article_record(
    article_id: str | int,
    *,
    landing_html: str | None = None,
    galley_html: str | None = None,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    aid = work_id_for_article(article_id)
    landing = article_landing_url(aid)
    own = session is None
    sess = session or _session()
    try:
        if landing_html is None:
            if on_status:
                on_status(f"TWC article {aid}: landing page…")
            _, landing_html = fetch_html(landing, session=sess, on_status=on_status)
        meta = parse_landing_metadata(landing_html, article_id=aid, url=landing)
        galley_info = meta.get("html_galley")
        if galley_html is None:
            if not galley_info:
                raise TwcError(f"TWC article {aid} has no HTML galley link")
            _galley_id, _galley_view = galley_info
            inline = inline_html_galley_url(aid, _galley_id)
            if on_status:
                on_status(f"TWC article {aid}: HTML galley…")
            _, galley_html = fetch_html(inline, session=sess, on_status=on_status)
        return _record_from_galley_html(
            galley_html, meta=meta, article_id=aid, url=landing
        )
    finally:
        if own:
            sess.close()


def fetch_issue_records(
    issue_id: str | int,
    *,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> list[dict[str, Any]]:
    iid = str(issue_id).strip()
    url = issue_url(iid)
    own = session is None
    sess = session or _session()
    try:
        if on_status:
            on_status(f"TWC issue {iid}: table of contents…")
        _, html = fetch_html(url, session=sess, on_status=on_status)
        article_ids = parse_issue_article_ids(html)
        if not article_ids:
            raise TwcError(f"No articles found on TWC issue {iid}")
        records: list[dict[str, Any]] = []
        for aid in article_ids:
            records.append(
                fetch_article_record(aid, session=sess, on_status=on_status)
            )
        return records
    finally:
        if own:
            sess.close()


def fetch_from_url(
    url: str,
    *,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> list[dict[str, Any]]:
    parsed = parse_twc_url(url)
    if parsed is None:
        raise TwcError(f"Not a TWC issue or article URL: {url}")
    kind, obj_id = parsed
    if kind == "issue":
        return fetch_issue_records(obj_id, session=session, on_status=on_status)
    return [
        fetch_article_record(obj_id, session=session, on_status=on_status)
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ao3kit twc",
        description=(
            "Import Transformative Works and Cultures (OJS) articles or whole "
            "issues to JSONL (source=twc), optionally building EPUBs from HTML galleys."
        ),
    )
    parser.add_argument("-o", "--output", help="Output JSONL path (default: stdout)")
    parser.add_argument(
        "--url",
        action="append",
        default=None,
        help="TWC issue or article URL (repeatable)",
    )
    parser.add_argument(
        "--issue-id",
        action="append",
        default=None,
        dest="issue_ids",
        help="TWC issue id (repeatable)",
    )
    parser.add_argument(
        "--article-id",
        action="append",
        default=None,
        dest="article_ids",
        help="TWC article id (repeatable)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Progress on stderr",
    )
    parser.add_argument(
        "--epub",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Build EPUB from HTML galleys (default: on)",
    )
    parser.add_argument(
        "--epub-dir",
        help="Directory for epubs/ (default: same dir as --output, or cwd)",
    )
    parser.add_argument(
        "--cover",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stamp generated covers into EPUBs (default: on)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    on_status: StatusCallback | None = None
    if args.verbose:
        on_status = lambda msg: print(msg, file=sys.stderr)

    urls: list[str] = list(args.url or [])
    for iid in args.issue_ids or []:
        urls.append(issue_url(iid))
    for aid in args.article_ids or []:
        urls.append(article_landing_url(aid))
    if not urls:
        parser.error("Pass --url, --issue-id, and/or --article-id")

    records: list[dict[str, Any]] = []
    sess = _session()
    try:
        for url in urls:
            records.extend(fetch_from_url(url, session=sess, on_status=on_status))
    finally:
        sess.close()

    if args.epub and records:
        if args.output:
            epub_dir = Path(args.epub_dir or Path(args.output).parent)
        else:
            epub_dir = Path(args.epub_dir or Path.cwd())
        epub_root = epub_dir / "epubs"
        epub_root.mkdir(parents=True, exist_ok=True)
        if on_status:
            on_status(f"Building EPUBs under {epub_root}…")
        built = 0
        for index, record in enumerate(records):
            updated = build_epub_for_record(
                record, epub_root, cover=bool(args.cover)
            )
            records[index] = updated
            if updated.get("epub_file"):
                built += 1
        if on_status:
            on_status(f"Built {built}/{len(records)} EPUB(s)")

    if args.output:
        write_jsonl(records, args.output)
        if on_status:
            on_status(f"Wrote {len(records)} TWC record(s)")
    else:
        for row in strip_html_bodies(records):
            print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
