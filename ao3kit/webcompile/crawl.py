"""Phase 1: identify links and collect source HTML.

Supports:
- Full URL list (no expansion)
- Seed URLs with free / same-domain / specific-domains expansion
- Loading a Tampermonkey / offline HTML bundle (skip network crawl)
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from typing import Callable
from urllib.parse import urldefrag, urljoin, urlparse

import requests

from ao3kit.htmlsoup import parse_html
from ao3kit.sources.web import (
    WebSourceError,
    fetch_html,
    normalize_url,
    read_html_file,
)
from ao3kit.webcompile.models import (
    CrawlOptions,
    CrawledPage,
    CrawlResult,
    ExpandMode,
)

StatusCallback = Callable[[str], None]

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SKIP_SCHEMES = frozenset(
    {
        "",
        "mailto",
        "javascript",
        "data",
        "tel",
        "sms",
        "ftp",
        "file",
        "about",
        "blob",
    }
)
_ASSET_SUFFIXES = (
    ".css",
    ".js",
    ".mjs",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp3",
    ".mp4",
    ".pdf",
    ".zip",
    ".xml",
    ".rss",
    ".atom",
)


def host_key(url: str) -> str:
    """Hostname used for domain matching (lowercase, no leading www.)."""
    host = (urlparse(url).hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_domain(value: str) -> str:
    text = str(value or "").strip().casefold()
    if "://" in text:
        text = host_key(text)
    else:
        text = text.split("/", 1)[0]
        if text.startswith("www."):
            text = text[4:]
    return text


def page_url_key(url: str) -> str:
    """Stable key for deduping pages (normalized URL without fragment)."""
    canon = normalize_url(url)
    if not canon:
        return ""
    base, _frag = urldefrag(canon)
    return base


def extract_links(html: str, *, base_url: str) -> list[str]:
    """Return absolute http(s) page links found in ``html``."""
    soup = parse_html(html)
    found: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = str(tag.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        scheme = (parsed.scheme or "").casefold()
        if scheme in _SKIP_SCHEMES or scheme not in {"http", "https"}:
            continue
        path = (parsed.path or "").casefold()
        if any(path.endswith(suf) for suf in _ASSET_SUFFIXES):
            continue
        key = page_url_key(absolute)
        if not key or key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


def link_allowed(
    url: str,
    *,
    expand: ExpandMode,
    seed_hosts: set[str],
    allowed_domains: list[str],
) -> bool:
    if expand == ExpandMode.NONE:
        return False
    if expand == ExpandMode.FREE:
        return True
    host = host_key(url)
    if not host:
        return False
    if expand == ExpandMode.SAME_DOMAIN:
        return host in seed_hosts
    if expand == ExpandMode.DOMAINS:
        allowed = {normalize_domain(d) for d in allowed_domains if normalize_domain(d)}
        if not allowed:
            return False
        if host in allowed:
            return True
        # Subdomain of an allowed domain (blog.example.com under example.com).
        return any(host.endswith("." + d) for d in allowed)
    return False


def _guess_title(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    if not match:
        return ""
    raw = re.sub(r"\s+", " ", match.group(1)).strip()
    return raw


def crawl_urls(
    options: CrawlOptions,
    *,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> CrawlResult:
    """Fetch HTML for an explicit URL list or expand from seeds."""
    result = CrawlResult()
    max_pages = max(1, int(options.max_pages or 1))
    max_depth = max(0, int(options.max_depth or 0))

    explicit = [page_url_key(u) for u in options.urls if page_url_key(u)]
    seeds = [page_url_key(u) for u in options.seeds if page_url_key(u)]

    if explicit:
        expand = ExpandMode.NONE
        queue: deque[tuple[str, int, str | None]] = deque(
            (url, 0, None) for url in explicit
        )
        seed_hosts: set[str] = {host_key(u) for u in explicit if host_key(u)}
    else:
        if not seeds:
            result.errors.append("No seed or explicit URLs to crawl")
            return result
        expand = options.expand
        if expand == ExpandMode.NONE:
            # Seeds only, no link following.
            queue = deque((url, 0, None) for url in seeds)
        else:
            queue = deque((url, 0, None) for url in seeds)
        seed_hosts = {host_key(u) for u in seeds if host_key(u)}

    seen: set[str] = set()
    own_session = session is None
    sess = session
    if sess is None:
        from ao3kit.sources.web import _session

        sess = _session()

    try:
        while queue and len(result.pages) < max_pages:
            url, depth, parent = queue.popleft()
            key = page_url_key(url)
            if not key or key in seen:
                continue
            seen.add(key)
            try:
                if on_status:
                    on_status(f"Fetching {key}…")
                final_url, html = fetch_html(
                    key, session=sess, on_status=on_status, timeout=options.timeout
                )
            except (WebSourceError, requests.RequestException, OSError, ValueError) as exc:
                msg = f"{key}: {exc}"
                result.errors.append(msg)
                continue

            final_key = page_url_key(final_url) or key
            if final_key != key and final_key in seen:
                result.skipped.append(f"redirect duplicate: {key} → {final_key}")
                continue
            seen.add(final_key)

            page = CrawledPage(
                url=final_key,
                final_url=final_key,
                html=html,
                title=_guess_title(html),
                depth=depth,
                discovered_from=parent,
                source="fetch",
            )
            result.pages.append(page)

            if expand == ExpandMode.NONE or depth >= max_depth:
                continue
            if len(result.pages) >= max_pages:
                break

            for link in extract_links(html, base_url=final_key):
                if link in seen:
                    continue
                if not link_allowed(
                    link,
                    expand=expand,
                    seed_hosts=seed_hosts,
                    allowed_domains=list(options.allowed_domains),
                ):
                    continue
                if any(link == q[0] for q in queue):
                    continue
                queue.append((link, depth + 1, final_key))
    finally:
        if own_session and sess is not None:
            sess.close()

    return result


def pages_from_html_files(
    paths: list[str | Path],
    *,
    urls: list[str] | None = None,
) -> CrawlResult:
    """Load local HTML files as crawled pages (offline / saved pages)."""
    result = CrawlResult()
    url_list = list(urls or [])
    for index, path in enumerate(paths):
        file_path = Path(path)
        try:
            html = read_html_file(file_path)
        except WebSourceError as exc:
            result.errors.append(str(exc))
            continue
        url = ""
        if index < len(url_list):
            url = page_url_key(url_list[index])
        if not url:
            url = f"file://{file_path.resolve().as_posix()}"
        result.pages.append(
            CrawledPage(
                url=url,
                final_url=url,
                html=html,
                title=_guess_title(html) or file_path.stem,
                depth=0,
                source="file",
            )
        )
    return result


def crawl_from_bundle(bundle: dict) -> CrawlResult:
    """Build a CrawlResult from a Tampermonkey / exported JSON bundle."""
    from ao3kit.webcompile.bundle import pages_from_bundle

    return pages_from_bundle(bundle)
