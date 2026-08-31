"""Phase 2: reduce HTML to ebook content and rewrite in-book hrefs."""

from __future__ import annotations

from urllib.parse import urldefrag, urljoin, urlparse

from ao3kit.htmlsoup import parse_html
from ao3kit.sources.extract import extract_page
from ao3kit.sources.web import normalize_url
from ao3kit.webcompile.crawl import page_url_key
from ao3kit.webcompile.models import CompiledChapter, CrawledPage


def chapter_filename(index: int) -> str:
    return f"chapter-{index:03d}.xhtml"


def build_url_map(pages: list[CrawledPage]) -> dict[str, str]:
    """Map normalized page URL → chapter href (e.g. chapter-001.xhtml)."""
    mapping: dict[str, str] = {}
    for index, page in enumerate(pages, start=1):
        href = chapter_filename(index)
        for candidate in (page.url, page.final_url):
            key = page_url_key(candidate)
            if key and key not in mapping:
                mapping[key] = href
    return mapping


def _resolve_in_book_href(
    href: str,
    *,
    base_url: str,
    url_map: dict[str, str],
) -> str | None:
    """Return a relative in-EPUB href if ``href`` targets a collected page."""
    raw = str(href or "").strip()
    if not raw or raw.startswith(("mailto:", "javascript:", "data:", "tel:")):
        return None
    if raw.startswith("#"):
        # Same-chapter fragment — keep as-is.
        return raw

    absolute = urljoin(base_url, raw)
    # Capture fragment before normalize_url (which drops it).
    _base, frag = urldefrag(absolute)
    absolute_norm = normalize_url(_base) or _base
    key = page_url_key(absolute_norm)
    chapter = url_map.get(key)
    if not chapter:
        # Try without query (some sites use tracking params on internal links).
        parsed = urlparse(absolute_norm)
        no_query = parsed._replace(query="", fragment="").geturl()
        chapter = url_map.get(page_url_key(no_query))
    if not chapter:
        return None
    if frag:
        return f"{chapter}#{frag}"
    return chapter


def rewrite_internal_links(
    html_body: str,
    *,
    base_url: str,
    url_map: dict[str, str],
) -> str:
    """Rewrite ``<a href>`` that point at collected pages to chapter paths."""
    soup = parse_html(f"<div id='__wc_root'>{html_body}</div>")
    root = soup.find(id="__wc_root") or soup
    for tag in root.find_all("a", href=True):
        href = str(tag.get("href") or "")
        rewritten = _resolve_in_book_href(href, base_url=base_url, url_map=url_map)
        if rewritten is not None:
            tag["href"] = rewritten
            continue
        # Leave external absolute URLs; absolutize relatives that escaped extract.
        if href and not href.startswith(("#", "http://", "https://", "mailto:")):
            tag["href"] = urljoin(base_url, href)
    if hasattr(root, "decode_contents"):
        return root.decode_contents().strip() or "<p></p>"
    return str(root)


def preprocess_pages(pages: list[CrawledPage]) -> list[CompiledChapter]:
    """Extract article content and rewrite cross-page links for each page."""
    url_map = build_url_map(pages)
    chapters: list[CompiledChapter] = []
    for index, page in enumerate(pages, start=1):
        extracted = extract_page(page.html, url=page.url)
        title = (page.title or extracted.title or f"Chapter {index}").strip()
        body = rewrite_internal_links(
            extracted.html_body,
            base_url=page.url,
            url_map=url_map,
        )
        chapters.append(
            CompiledChapter(
                url=page.url,
                title=title,
                html_body=body,
                word_count=int(extracted.word_count or 0),
                warnings=list(extracted.warnings),
                chapter_href=chapter_filename(index),
            )
        )
    return chapters
