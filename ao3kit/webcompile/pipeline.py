"""Orchestrate crawl → preprocess → unified EPUB."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from ao3kit.sources.base import SOURCE_WEB
from ao3kit.sources.extract import extract_page
from ao3kit.sources.web import work_id_for_url
from ao3kit.webcompile.bundle import bundle_meta, load_bundle, pages_from_bundle
from ao3kit.webcompile.crawl import (
    crawl_urls,
    page_url_key,
    pages_from_html_files,
)
from ao3kit.webcompile.epub import attach_compiled_epub
from ao3kit.webcompile.models import (
    CompileOptions,
    CompileResult,
    CrawlOptions,
    CrawlResult,
    ExpandMode,
)
from ao3kit.webcompile.preprocess import preprocess_pages

StatusCallback = Callable[[str], None]


def work_id_for_compile(urls: list[str], *, title: str = "") -> str:
    keys = [page_url_key(u) for u in urls if page_url_key(u)]
    if not keys and title:
        return hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]
    if len(keys) == 1:
        return work_id_for_url(keys[0])
    joined = "\n".join(keys)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def _record_from_chapters(
    chapters,
    *,
    title: str,
    author: str,
    language: str,
    seed_url: str,
    work_id: str,
) -> dict[str, Any]:
    first = chapters[0] if chapters else None
    book_title = (
        title
        or (first.title if first else "")
        or "Web pages"
    ).strip() or "Web pages"
    book_author = author.strip() or "Unknown"
    words = sum(int(ch.word_count or 0) for ch in chapters)
    tags: list[str] = []
    summary = None
    date = None
    lang = language or "en"
    if first:
        # Pull richer meta from the first page's original extract when possible.
        pass
    meta: dict[str, Any] = {"language": lang, "words": words}
    url = seed_url or (first.url if first else f"web:{work_id}")
    record: dict[str, Any] = {
        "source": SOURCE_WEB,
        "work_id": work_id,
        "url": url,
        "title": book_title,
        "author": book_author,
        "summary": summary,
        "fandoms": [],
        "tags": tags,
        "date": date,
        "metadata": meta,
        "web_compile": True,
        "page_count": len(chapters),
        "chapters": [
            {"title": ch.title, "url": ch.url, "href": ch.chapter_href}
            for ch in chapters
        ],
    }
    return record


def enrich_record_from_first_page(
    record: dict[str, Any],
    first_html: str,
    *,
    url: str,
) -> dict[str, Any]:
    """Fill author/summary/date/tags from the first page when not set by the user."""
    extracted = extract_page(first_html, url=url)
    updated = dict(record)
    if (not updated.get("author") or updated.get("author") == "Unknown") and extracted.author:
        updated["author"] = extracted.author
    if not updated.get("summary") and extracted.summary:
        updated["summary"] = extracted.summary
    if not updated.get("date") and extracted.date:
        updated["date"] = extracted.date
    if extracted.language:
        meta = dict(updated.get("metadata") or {})
        meta["language"] = extracted.language
        updated["metadata"] = meta
    tags = list(updated.get("tags") or [])
    for tag in extracted.tags:
        if tag not in tags:
            tags.append(tag)
    updated["tags"] = tags
    if (
        (not updated.get("title") or updated.get("title") == "Web pages")
        and extracted.title
        and extracted.title != "Untitled"
    ):
        # Prefer explicit compile title; otherwise keep chapter-derived title.
        pass
    return updated


def compile_from_crawl(
    crawl: CrawlResult,
    *,
    dest_dir: str | Path,
    title: str = "",
    author: str = "",
    language: str = "en",
    cover: bool = True,
    seed_url: str = "",
    on_status: StatusCallback | None = None,
) -> CompileResult:
    """Phases 2–3 from an already-collected CrawlResult."""
    out = CompileResult()
    out.errors.extend(crawl.errors)
    if not crawl.pages:
        out.errors.append("No pages to compile")
        return out

    if on_status:
        on_status(f"Preprocessing {len(crawl.pages)} page(s)…")
    chapters = preprocess_pages(crawl.pages)
    out.chapters = chapters
    for chapter in chapters:
        out.warnings.extend(chapter.warnings)

    urls = [p.url for p in crawl.pages]
    primary = seed_url or urls[0]
    work_id = work_id_for_compile(urls, title=title)
    record = _record_from_chapters(
        chapters,
        title=title,
        author=author,
        language=language,
        seed_url=primary,
        work_id=work_id,
    )
    record = enrich_record_from_first_page(
        record, crawl.pages[0].html, url=crawl.pages[0].url
    )
    if title:
        record["title"] = title
    if author:
        record["author"] = author

    if on_status:
        on_status("Building unified EPUB…")
    try:
        record = attach_compiled_epub(
            record, chapters, dest_dir, cover=cover
        )
        out.epub_path = str(Path(dest_dir) / record["epub_file"])
    except Exception as exc:
        record = dict(record)
        record["epub_error"] = str(exc)
        out.errors.append(str(exc))
    out.record = record
    return out


def compile_pages(
    options: CompileOptions,
    *,
    dest_dir: str | Path,
    on_status: StatusCallback | None = None,
) -> CompileResult:
    """Full pipeline: crawl seeds/URLs → preprocess → EPUB."""
    crawl_opts = options.crawl
    if options.max_pages is not None:
        crawl_opts = CrawlOptions(
            seeds=list(crawl_opts.seeds),
            urls=list(crawl_opts.urls),
            expand=crawl_opts.expand,
            allowed_domains=list(crawl_opts.allowed_domains),
            max_pages=int(options.max_pages),
            max_depth=crawl_opts.max_depth,
            timeout=crawl_opts.timeout,
        )
    if on_status:
        on_status("Crawling pages…")
    crawl = crawl_urls(crawl_opts, on_status=on_status)
    seed = ""
    if crawl_opts.seeds:
        seed = page_url_key(crawl_opts.seeds[0])
    elif crawl_opts.urls:
        seed = page_url_key(crawl_opts.urls[0])
    return compile_from_crawl(
        crawl,
        dest_dir=dest_dir,
        title=options.title,
        author=options.author,
        language=options.language,
        cover=options.cover,
        seed_url=seed,
        on_status=on_status,
    )


def compile_bundle_file(
    bundle_path: str | Path,
    *,
    dest_dir: str | Path,
    title: str = "",
    author: str = "",
    language: str = "en",
    cover: bool = True,
    on_status: StatusCallback | None = None,
) -> CompileResult:
    """Compile from a Tampermonkey / exported JSON bundle (skip network crawl)."""
    if on_status:
        on_status(f"Loading bundle {bundle_path}…")
    data = load_bundle(bundle_path)
    meta = bundle_meta(data)
    crawl = pages_from_bundle(data)
    return compile_from_crawl(
        crawl,
        dest_dir=dest_dir,
        title=title or meta["title"],
        author=author or meta["author"],
        language=language,
        cover=cover,
        seed_url=meta["seed_url"],
        on_status=on_status,
    )


def compile_html_files(
    paths: list[str | Path],
    *,
    dest_dir: str | Path,
    urls: list[str] | None = None,
    title: str = "",
    author: str = "",
    language: str = "en",
    cover: bool = True,
    on_status: StatusCallback | None = None,
) -> CompileResult:
    """Compile local HTML files into one EPUB (ordered)."""
    if on_status:
        on_status(f"Reading {len(paths)} HTML file(s)…")
    crawl = pages_from_html_files(paths, urls=urls)
    seed = ""
    if urls:
        seed = page_url_key(urls[0])
    return compile_from_crawl(
        crawl,
        dest_dir=dest_dir,
        title=title,
        author=author,
        language=language,
        cover=cover,
        seed_url=seed,
        on_status=on_status,
    )


def parse_expand_mode(value: str) -> ExpandMode:
    text = str(value or "").strip().casefold().replace("-", "_")
    aliases = {
        "none": ExpandMode.NONE,
        "off": ExpandMode.NONE,
        "full": ExpandMode.NONE,
        "same": ExpandMode.SAME_DOMAIN,
        "same_domain": ExpandMode.SAME_DOMAIN,
        "domain": ExpandMode.SAME_DOMAIN,
        "domains": ExpandMode.DOMAINS,
        "allow": ExpandMode.DOMAINS,
        "free": ExpandMode.FREE,
        "all": ExpandMode.FREE,
    }
    if text not in aliases:
        raise ValueError(
            f"Unknown expand mode {value!r}; use none, same_domain, domains, or free"
        )
    return aliases[text]
