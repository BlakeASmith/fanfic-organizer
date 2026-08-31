"""Wikipedia (MediaWiki) source: search / fetch articles to JSONL.

Uses the public MediaWiki API (no login). Pacing goes through
``ao3kit.rate.wait_for_request`` so Wikipedia shares the host-wide limiter
with AO3 jobs. Records use ``source="wikipedia"`` and keep the shared
``work_id`` / ``url`` / ``title`` / ``summary`` / ``tags`` JSONL shape so the
Calibre importer can load them without a separate path.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, TextIO
from urllib.parse import quote, unquote, urlparse

import requests

from ao3kit.rate import USER_AGENT, wait_for_request
from ao3kit.sources.base import SOURCE_WIKIPEDIA

StatusCallback = Callable[[str], None]

DEFAULT_LANG = "en"
DEFAULT_API_PATH = "/w/api.php"
WIKIPEDIA_HOST_RE = re.compile(
    r"^(?:www\.)?(?P<lang>[a-z]{2,12})\.wikipedia\.org$",
    re.IGNORECASE,
)
WIKI_PATH_RE = re.compile(r"^/wiki/(?P<title>[^?#]+)", re.IGNORECASE)
PAGE_ID_RE = re.compile(r"^\d+$")


class WikipediaError(RuntimeError):
    """MediaWiki API or URL parse failure."""


def wiki_api_base(lang: str = DEFAULT_LANG) -> str:
    code = str(lang or DEFAULT_LANG).strip().lower() or DEFAULT_LANG
    return f"https://{code}.wikipedia.org{DEFAULT_API_PATH}"


def wiki_article_url(title: str, *, lang: str = DEFAULT_LANG) -> str:
    slug = quote(str(title).replace(" ", "_"), safe="()'_!:*,@/$")
    code = str(lang or DEFAULT_LANG).strip().lower() or DEFAULT_LANG
    return f"https://{code}.wikipedia.org/wiki/{slug}"


def parse_wikipedia_url(url: str) -> tuple[str, str] | None:
    """Return ``(lang, title)`` from an article URL, or None."""
    text = str(url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    host = (parsed.netloc or "").lower()
    match = WIKIPEDIA_HOST_RE.match(host)
    if not match:
        return None
    lang = match.group("lang").lower()
    path_match = WIKI_PATH_RE.match(parsed.path or "")
    if not path_match:
        return None
    title = unquote(path_match.group("title")).replace("_", " ").strip()
    if not title:
        return None
    return lang, title


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _api_get(
    session: requests.Session,
    api_url: str,
    params: dict[str, Any],
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    wait_for_request(api_url, on_status=on_status)
    response = session.get(api_url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise WikipediaError("MediaWiki API returned a non-object response")
    if data.get("error"):
        err = data["error"]
        if isinstance(err, dict):
            code = err.get("code") or "error"
            info = err.get("info") or str(err)
            raise WikipediaError(f"MediaWiki API {code}: {info}")
        raise WikipediaError(f"MediaWiki API error: {err}")
    return data


def search_titles(
    query: str,
    *,
    lang: str = DEFAULT_LANG,
    limit: int = 25,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> list[dict[str, Any]]:
    """Return MediaWiki search hits (title, pageid, snippet)."""
    text = str(query or "").strip()
    if not text:
        return []
    cap = max(1, min(int(limit or 25), 50))
    own = session is None
    sess = session or _session()
    try:
        data = _api_get(
            sess,
            wiki_api_base(lang),
            {
                "action": "query",
                "list": "search",
                "srsearch": text,
                "srlimit": cap,
                "srprop": "snippet|timestamp|wordcount",
                "format": "json",
                "formatversion": "2",
            },
            on_status=on_status,
        )
    finally:
        if own:
            sess.close()
    hits = ((data.get("query") or {}).get("search")) or []
    out: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        title = str(hit.get("title") or "").strip()
        pageid = hit.get("pageid")
        if not title or pageid is None:
            continue
        out.append(
            {
                "title": title,
                "pageid": int(pageid),
                "snippet": str(hit.get("snippet") or ""),
                "wordcount": hit.get("wordcount"),
                "timestamp": hit.get("timestamp"),
            }
        )
    return out


def _clean_categories(raw: list[Any] | None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
        else:
            title = str(item or "").strip()
        if not title:
            continue
        if title.casefold().startswith("category:"):
            title = title.split(":", 1)[1].strip()
        # Skip maintenance / hidden-style categories by prefix.
        low = title.casefold()
        if low.startswith("articles ") or low.startswith("cs1 ") or low.startswith(
            "wikipedia:"
        ):
            continue
        if low.startswith("all articles") or low.startswith("webarchive "):
            continue
        key = low
        if key in seen:
            continue
        seen.add(key)
        names.append(title)
    return names


def _timestamp_to_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    # MediaWiki: 2024-01-15T12:34:56Z
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def _page_to_record(page: dict[str, Any], *, lang: str) -> dict[str, Any] | None:
    if not isinstance(page, dict) or page.get("missing"):
        return None
    pageid = page.get("pageid")
    title = str(page.get("title") or "").strip()
    if pageid is None or not title:
        return None
    work_id = str(int(pageid))
    url = str(page.get("fullurl") or "").strip() or wiki_article_url(title, lang=lang)
    extract = str(page.get("extract") or "").strip()
    categories = _clean_categories(page.get("categories"))
    # Prefer prop=info ``touched`` (works for multi-page queries). Revisions
    # are only valid with rvlimit on a single page, so search batches omit them.
    timestamp = page.get("touched")
    if not timestamp:
        revisions = page.get("revisions") or []
        if isinstance(revisions, list) and revisions:
            first = revisions[0]
            if isinstance(first, dict):
                timestamp = first.get("timestamp")
    words = page.get("length")
    # MediaWiki length is bytes; prefer search wordcount when present.
    wordcount = page.get("wordcount")
    if wordcount is None and isinstance(words, int) and words > 0:
        # Rough word estimate from bytes when search did not supply one.
        wordcount = max(1, words // 6)
    meta: dict[str, Any] = {"language": lang}
    if isinstance(wordcount, int) and wordcount > 0:
        meta["words"] = int(wordcount)
    record: dict[str, Any] = {
        "source": SOURCE_WIKIPEDIA,
        "work_id": work_id,
        "url": url,
        "title": title,
        "author": "Wikipedia contributors",
        "fandoms": [],
        "tags": categories,
        "date": _timestamp_to_date(timestamp),
        "metadata": meta,
    }
    if extract:
        record["summary"] = extract
    return record


def fetch_pages(
    *,
    pageids: list[int] | None = None,
    titles: list[str] | None = None,
    lang: str = DEFAULT_LANG,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> list[dict[str, Any]]:
    """Fetch full article records for page ids and/or titles."""
    ids = [int(x) for x in (pageids or []) if x is not None]
    title_list = [str(t).strip() for t in (titles or []) if str(t).strip()]
    if not ids and not title_list:
        return []
    own = session is None
    sess = session or _session()
    records: list[dict[str, Any]] = []
    try:
        # MediaWiki accepts up to ~50 titles/ids per query.
        chunks: list[dict[str, Any]] = []
        for i in range(0, max(len(ids), 1) if ids else 0, 40):
            chunks.append({"pageids": "|".join(str(x) for x in ids[i : i + 40])})
        for i in range(0, max(len(title_list), 1) if title_list else 0, 40):
            chunks.append({"titles": "|".join(title_list[i : i + 40])})
        if not chunks:
            return []
        for chunk in chunks:
            # Do not use prop=revisions + rvlimit here: MediaWiki rejects
            # rvlimit / rvstart / … when titles or pageids list more than one
            # page (invalidparammix). ``info`` already returns ``touched``.
            params: dict[str, Any] = {
                "action": "query",
                "prop": "extracts|info|categories",
                "exintro": "1",
                "explaintext": "1",
                "inprop": "url",
                "cllimit": "50",
                "clshow": "!hidden",
                "format": "json",
                "formatversion": "2",
                **chunk,
            }
            data = _api_get(
                sess, wiki_api_base(lang), params, on_status=on_status
            )
            pages = ((data.get("query") or {}).get("pages")) or []
            for page in pages:
                record = _page_to_record(page, lang=lang)
                if record is not None:
                    records.append(record)
    finally:
        if own:
            sess.close()
    return records


def fetch_article_html(
    *,
    pageid: int | None = None,
    title: str | None = None,
    lang: str = DEFAULT_LANG,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> str:
    """Return rendered article HTML from MediaWiki ``action=parse``."""
    if pageid is None and not str(title or "").strip():
        raise WikipediaError("pageid or title required for article HTML")
    own = session is None
    sess = session or _session()
    try:
        params: dict[str, Any] = {
            "action": "parse",
            "prop": "text",
            "disableeditsection": "1",
            "format": "json",
            "formatversion": "2",
        }
        if pageid is not None:
            params["pageid"] = int(pageid)
        else:
            params["page"] = str(title).strip()
        data = _api_get(sess, wiki_api_base(lang), params, on_status=on_status)
        parsed = data.get("parse") or {}
        text = parsed.get("text")
        if isinstance(text, dict):
            html = str(text.get("*") or "")
        else:
            html = str(text or "")
        if not html.strip():
            raise WikipediaError("MediaWiki parse returned empty HTML")
        return html
    finally:
        if own:
            sess.close()


def build_epubs_for_records(
    records: list[dict[str, Any]],
    dest_dir: str | Path,
    *,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
    cover: bool = True,
) -> list[dict[str, Any]]:
    """Fetch article HTML and write EPUB files; returns updated records."""
    from ao3kit.sources.wikipedia_epub import attach_epub_to_record

    own = session is None
    sess = session or _session()
    out: list[dict[str, Any]] = []
    try:
        for record in records:
            lang = str((record.get("metadata") or {}).get("language") or DEFAULT_LANG)
            work_id = str(record.get("work_id") or "").strip()
            title = str(record.get("title") or "").strip()
            try:
                html_body = fetch_article_html(
                    pageid=int(work_id) if work_id.isdigit() else None,
                    title=None if work_id.isdigit() else title,
                    lang=lang,
                    session=sess,
                    on_status=on_status,
                )
                updated = attach_epub_to_record(
                    record, dest_dir, html_body, cover=cover
                )
                out.append(updated)
            except (WikipediaError, requests.RequestException, OSError, ValueError) as exc:
                failed = dict(record)
                failed["epub_error"] = str(exc)
                failed.pop("epub_file", None)
                out.append(failed)
                if on_status:
                    on_status(f"EPUB failed for {title or work_id}: {exc}")
    finally:
        if own:
            sess.close()
    return out


def search_records(
    query: str,
    *,
    lang: str = DEFAULT_LANG,
    max_results: int = 25,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> list[dict[str, Any]]:
    """Search Wikipedia and return enriched work-record dicts."""
    own = session is None
    sess = session or _session()
    try:
        hits = search_titles(
            query,
            lang=lang,
            limit=max_results,
            session=sess,
            on_status=on_status,
        )
        if not hits:
            return []
        pageids = [int(h["pageid"]) for h in hits]
        records = fetch_pages(
            pageids=pageids, lang=lang, session=sess, on_status=on_status
        )
        # Preserve search order.
        by_id = {str(r["work_id"]): r for r in records}
        ordered: list[dict[str, Any]] = []
        for hit in hits:
            key = str(int(hit["pageid"]))
            record = by_id.get(key)
            if record is None:
                continue
            # Prefer search wordcount when the page fetch lacked one.
            wc = hit.get("wordcount")
            if isinstance(wc, int) and wc > 0:
                meta = dict(record.get("metadata") or {})
                meta.setdefault("words", int(wc))
                record["metadata"] = meta
            if not record.get("date"):
                record["date"] = _timestamp_to_date(hit.get("timestamp"))
            ordered.append(record)
        return ordered
    finally:
        if own:
            sess.close()


def fetch_from_url(
    url: str,
    *,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> list[dict[str, Any]]:
    parsed = parse_wikipedia_url(url)
    if parsed is None:
        raise WikipediaError(f"Not a Wikipedia article URL: {url}")
    lang, title = parsed
    return fetch_pages(
        titles=[title], lang=lang, session=session, on_status=on_status
    )


def write_jsonl(records: list[dict[str, Any]], output: TextIO | Path | str) -> None:
    if isinstance(output, (str, Path)):
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return
    for record in records:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ao3kit wikipedia",
        description=(
            "Search or fetch Wikipedia articles into the shared JSONL work "
            "record format (source=wikipedia)."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output JSONL path (default: stdout)",
    )
    parser.add_argument(
        "--query",
        "-q",
        help="Search query (MediaWiki fulltext search)",
    )
    parser.add_argument(
        "--url",
        help="Wikipedia article URL to fetch",
    )
    parser.add_argument(
        "--title",
        action="append",
        default=None,
        help="Article title to fetch (repeatable)",
    )
    parser.add_argument(
        "--page-id",
        type=int,
        action="append",
        default=None,
        dest="page_ids",
        help="Wikipedia page id to fetch (repeatable)",
    )
    parser.add_argument(
        "--lang",
        default=DEFAULT_LANG,
        help=f"Wikipedia language subdomain (default: {DEFAULT_LANG})",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=25,
        help="Max search results (default: 25, cap 50)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Progress messages on stderr",
    )
    parser.add_argument(
        "--epub",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Build an EPUB from each article's HTML (MediaWiki parse)",
    )
    parser.add_argument(
        "--epub-dir",
        help="Directory for epubs/ (default: same dir as --output, or cwd)",
    )
    parser.add_argument(
        "--cover",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stamp a generated cover into built EPUBs (default: on)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    on_status = (lambda msg: print(msg, file=sys.stderr)) if args.verbose else None
    records: list[dict[str, Any]] = []
    try:
        if args.url:
            if args.verbose:
                print(f"Fetching Wikipedia URL {args.url}…", file=sys.stderr)
            records = fetch_from_url(args.url, on_status=on_status)
        elif args.page_ids or args.title:
            if args.verbose:
                print("Fetching Wikipedia page(s)…", file=sys.stderr)
            records = fetch_pages(
                pageids=list(args.page_ids or []),
                titles=list(args.title or []),
                lang=args.lang,
                on_status=on_status,
            )
        elif args.query:
            if args.verbose:
                print(
                    f"Searching Wikipedia ({args.lang}) for {args.query!r}…",
                    file=sys.stderr,
                )
            records = search_records(
                args.query,
                lang=args.lang,
                max_results=args.max_results,
                on_status=on_status,
            )
        else:
            parser.error("Provide --query, --url, --title, and/or --page-id")

        if args.epub and records:
            if args.epub_dir:
                epub_root = Path(args.epub_dir)
            elif args.output:
                epub_root = Path(args.output).resolve().parent
            else:
                epub_root = Path.cwd()
            if args.verbose:
                print(f"Building EPUBs under {epub_root / 'epubs'}…", file=sys.stderr)
            records = build_epubs_for_records(
                records,
                epub_root,
                on_status=on_status,
                cover=bool(args.cover),
            )
    except (WikipediaError, requests.RequestException, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.output:
        write_jsonl(records, args.output)
    else:
        write_jsonl(records, sys.stdout)

    noun = "article" if len(records) == 1 else "articles"
    print(f"Wrote {len(records)} Wikipedia {noun}", file=sys.stderr)
    if args.epub:
        ok = sum(1 for r in records if r.get("epub_file"))
        print(f"Built {ok}/{len(records)} EPUB(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
