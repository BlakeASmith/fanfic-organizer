"""Data models for multi-page web → EPUB compilation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExpandMode(str, Enum):
    """How seed-page link expansion chooses followable URLs."""

    NONE = "none"  # no expansion (full link list or single page)
    SAME_DOMAIN = "same_domain"
    DOMAINS = "domains"
    FREE = "free"


@dataclass
class CrawlOptions:
    """Phase 1 options: discover and fetch source HTML."""

    seeds: list[str] = field(default_factory=list)
    """Seed URLs. When ``expand`` is not NONE, links are discovered from these."""

    urls: list[str] = field(default_factory=list)
    """Explicit full URL set. When non-empty, expansion is skipped and only
    these URLs (plus any successfully fetched redirects) are collected."""

    expand: ExpandMode = ExpandMode.SAME_DOMAIN
    allowed_domains: list[str] = field(default_factory=list)
    """Hostnames allowed when ``expand`` is DOMAINS (e.g. ``example.com``)."""

    max_pages: int = 50
    max_depth: int = 2
    timeout: float = 45.0


@dataclass
class CrawledPage:
    url: str
    """Canonical URL used as the chapter key (normalized)."""

    final_url: str
    html: str
    title: str = ""
    depth: int = 0
    discovered_from: str | None = None
    source: str = "fetch"
    """``fetch`` | ``file`` | ``bundle``."""


@dataclass
class CrawlResult:
    pages: list[CrawledPage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class CompiledChapter:
    """One chapter after preprocess (content + rewritten links)."""

    url: str
    title: str
    html_body: str
    word_count: int = 0
    warnings: list[str] = field(default_factory=list)
    chapter_href: str = ""
    """Relative path inside the EPUB (e.g. ``chapter-001.xhtml``)."""


@dataclass
class CompileOptions:
    """Top-level options for the three-phase pipeline."""

    crawl: CrawlOptions = field(default_factory=CrawlOptions)
    title: str = ""
    author: str = ""
    language: str = "en"
    cover: bool = True
    max_pages: int | None = None
    """Override crawl.max_pages when set."""


@dataclass
class CompileResult:
    chapters: list[CompiledChapter] = field(default_factory=list)
    record: dict[str, Any] = field(default_factory=dict)
    epub_path: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
