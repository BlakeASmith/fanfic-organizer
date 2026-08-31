"""Generic URL / saved-HTML source: extract article → JSONL (+ optional EPUB).

Fetches a remote HTML URL (or reads a browser-exported HTML file), extracts
metadata and main content best-effort, and writes the shared work-record JSONL
shape with ``source="web"``. Pacing for remote fetches goes through
``ao3kit.rate.wait_for_request`` (same host-wide limiter as other jobs).

This path does **not** run JavaScript. Dynamic sites need a saved HTML file
from the browser (Save Page / Download page).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, TextIO
from urllib.parse import urlparse, urlunparse

import requests

from ao3kit.rate import USER_AGENT, wait_for_request
from ao3kit.sources.base import SOURCE_WEB
from ao3kit.sources.extract import ExtractedPage, extract_page

StatusCallback = Callable[[str], None]

_HTML_CT_RE = re.compile(r"text/html|application/xhtml\+xml", re.IGNORECASE)


class WebSourceError(RuntimeError):
    """Fetch or extract failure for the generic web source."""


def normalize_url(url: str) -> str:
    """Normalize a URL for stable work ids (strip fragment, default scheme)."""
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        # Keep www — sites differ; only lowercase host.
        pass
    path = parsed.path or "/"
    # Drop trailing slash except for root.
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = parsed.query  # keep query (often selects the article)
    return urlunparse((scheme, netloc, path, "", query, ""))


def work_id_for_url(url: str) -> str:
    key = normalize_url(url) or str(url or "").strip()
    if not key:
        key = "empty"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def work_id_for_html_file(path: str | Path, *, url: str = "") -> str:
    if url:
        return work_id_for_url(url)
    resolved = str(Path(path).resolve())
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }
    )
    return session


def fetch_html(
    url: str,
    *,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
    timeout: float = 45.0,
) -> tuple[str, str]:
    """GET ``url``; return ``(final_url, html_text)``.

    Raises ``WebSourceError`` when the response is not HTML.
    """
    target = normalize_url(url)
    if not target:
        raise WebSourceError("URL is empty")
    own = session is None
    sess = session or _session()
    try:
        wait_for_request(target, on_status=on_status)
        if on_status:
            on_status(f"Fetching {target}…")
        response = sess.get(target, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        final = normalize_url(response.url) or target
        ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        # Some hosts omit Content-Type; sniff when body looks like HTML.
        body = response.content
        text: str
        encoding = response.encoding or response.apparent_encoding or "utf-8"
        try:
            text = body.decode(encoding, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        looks_html = bool(
            _HTML_CT_RE.search(ctype)
            or re.search(r"<html\b|<body\b|<article\b", text[:4000], re.I)
        )
        if not looks_html:
            raise WebSourceError(
                f"URL did not return HTML (Content-Type: {ctype or 'unknown'}): {final}"
            )
        return final, text
    finally:
        if own:
            sess.close()


def read_html_file(path: str | Path) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        raise WebSourceError(f"HTML file not found: {file_path}")
    raw = file_path.read_bytes()
    # UTF-8 with BOM / charset sniff via HTML meta is best-effort.
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _record_from_extract(
    extracted: ExtractedPage,
    *,
    url: str,
    work_id: str,
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if extracted.language:
        meta["language"] = extracted.language
    if extracted.word_count:
        meta["words"] = int(extracted.word_count)
    record: dict[str, Any] = {
        "source": SOURCE_WEB,
        "work_id": work_id,
        "url": url or f"web:{work_id}",
        "title": extracted.title,
        "author": extracted.author,
        "summary": extracted.summary,
        "fandoms": [],
        "tags": list(extracted.tags),
        "date": extracted.date,
        "metadata": meta,
        "html_body": extracted.html_body,
    }
    if extracted.warnings:
        record["extract_warnings"] = list(extracted.warnings)
    if extracted.site_name:
        record["site_name"] = extracted.site_name
    return record


def record_from_html(
    html: str,
    *,
    url: str = "",
    work_id: str | None = None,
) -> dict[str, Any]:
    canon = normalize_url(url) if url else ""
    wid = work_id or (
        work_id_for_url(canon) if canon else hashlib.sha1(html.encode("utf-8")).hexdigest()[:16]
    )
    extracted = extract_page(html, url=canon)
    return _record_from_extract(extracted, url=canon or f"web:{wid}", work_id=wid)


def fetch_record(
    url: str,
    *,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    final_url, html = fetch_html(url, session=session, on_status=on_status)
    return record_from_html(html, url=final_url)


def record_from_file(
    path: str | Path,
    *,
    url: str = "",
) -> dict[str, Any]:
    html = read_html_file(path)
    canon = normalize_url(url) if url else ""
    wid = work_id_for_html_file(path, url=canon)
    return record_from_html(html, url=canon, work_id=wid)


def build_epub_for_record(
    record: dict[str, Any],
    dest_dir: str | Path,
    *,
    cover: bool = True,
) -> dict[str, Any]:
    from ao3kit.sources.web_epub import attach_epub_to_record

    html_body = str(record.get("html_body") or "")
    if not html_body.strip():
        updated = dict(record)
        updated["epub_error"] = "no extracted HTML body"
        return updated
    try:
        updated = attach_epub_to_record(
            record, dest_dir, html_body, cover=cover
        )
    except Exception as exc:
        updated = dict(record)
        updated["epub_error"] = str(exc)
        return updated
    # Drop bulky body from JSONL after EPUB is written.
    updated.pop("html_body", None)
    return updated


def strip_html_bodies(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records:
        cleaned = dict(record)
        cleaned.pop("html_body", None)
        out.append(cleaned)
    return out


def write_jsonl(records: list[dict[str, Any]], output: TextIO | Path | str) -> None:
    rows = strip_html_bodies(records)
    if isinstance(output, (str, Path)):
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in rows:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return
    for record in rows:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")


def copy_html_into_work(src: str | Path, work_dir: str | Path) -> Path:
    """Copy a user HTML file into the job work dir; return the new path."""
    source = Path(src)
    if not source.is_file():
        raise WebSourceError(f"HTML file not found: {source}")
    dest_dir = Path(work_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "input.html"
    if source.resolve() != dest.resolve():
        shutil.copy2(source, dest)
    return dest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ao3kit web",
        description=(
            "Fetch a URL or read saved HTML, extract article content best-effort, "
            "and write shared JSONL (source=web). Does not run JavaScript — for "
            "dynamic sites, export HTML from the browser and pass --html."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output JSONL path (default: stdout)",
    )
    parser.add_argument(
        "--url",
        action="append",
        default=None,
        help="Page URL to fetch (repeatable)",
    )
    parser.add_argument(
        "--html",
        action="append",
        default=None,
        dest="html_files",
        help="Path to a browser-exported HTML file (repeatable)",
    )
    parser.add_argument(
        "--page-url",
        default="",
        help="Canonical URL when importing --html (stored as identifier)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Progress and extraction warnings on stderr",
    )
    parser.add_argument(
        "--epub",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Build an EPUB from extracted HTML (default: on)",
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

    urls = [str(u).strip() for u in (args.url or []) if str(u).strip()]
    html_files = [str(p).strip() for p in (args.html_files or []) if str(p).strip()]
    page_url = str(args.page_url or "").strip()

    if not urls and not html_files:
        parser.error("Provide --url and/or --html")

    on_status = (lambda msg: print(msg, file=sys.stderr)) if args.verbose else None
    # Always print the capability warning once.
    print(
        "warning: generic URL/HTML import is best-effort and does not work for "
        "JavaScript-rendered sites — use --html with a browser-exported page "
        "for those.",
        file=sys.stderr,
    )

    records: list[dict[str, Any]] = []
    try:
        for url in urls:
            if args.verbose:
                print(f"Fetching {url}…", file=sys.stderr)
            record = fetch_record(url, on_status=on_status)
            for warn in record.get("extract_warnings") or []:
                print(f"warning: {warn}", file=sys.stderr)
            records.append(record)

        for path in html_files:
            if args.verbose:
                print(f"Reading HTML file {path}…", file=sys.stderr)
            # When both --url and --html are given with a single file, prefer
            # --page-url, else the first --url as canonical for the file.
            file_url = page_url
            if not file_url and len(html_files) == 1 and len(urls) == 1:
                file_url = urls[0]
            record = record_from_file(path, url=file_url)
            for warn in record.get("extract_warnings") or []:
                print(f"warning: {warn}", file=sys.stderr)
            records.append(record)

        # If user passed both url and html for the same page, we may have
        # duplicates; leave as-is (explicit inputs).

        if args.epub and records:
            if args.epub_dir:
                epub_root = Path(args.epub_dir)
            elif args.output:
                epub_root = Path(args.output).resolve().parent
            else:
                epub_root = Path.cwd()
            if args.verbose:
                print(f"Building EPUBs under {epub_root / 'epubs'}…", file=sys.stderr)
            built: list[dict[str, Any]] = []
            for record in records:
                built.append(
                    build_epub_for_record(record, epub_root, cover=bool(args.cover))
                )
            records = built
        else:
            records = strip_html_bodies(records)
    except (WebSourceError, requests.RequestException, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.output:
        write_jsonl(records, args.output)
    else:
        write_jsonl(records, sys.stdout)

    noun = "page" if len(records) == 1 else "pages"
    print(f"Wrote {len(records)} web {noun}", file=sys.stderr)
    if args.epub:
        ok = sum(1 for r in records if r.get("epub_file"))
        print(f"Built {ok}/{len(records)} EPUB(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
