"""Tampermonkey / offline crawl bundle format (JSON).

Schema version 1::

    {
      "version": 1,
      "generator": "fanfic-organizer-webcompile",
      "title": "optional book title",
      "author": "optional",
      "seed_url": "https://…",
      "pages": [
        {"url": "https://…", "title": "…", "html": "<!DOCTYPE html>…"}
      ]
    }

The companion userscript exports this shape so Phase 1 can be skipped for
JavaScript-rendered sites; Phases 2–3 run unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ao3kit.webcompile.crawl import page_url_key
from ao3kit.webcompile.models import CrawledPage, CrawlResult

BUNDLE_VERSION = 1
BUNDLE_GENERATOR = "fanfic-organizer-webcompile"


class BundleError(ValueError):
    """Invalid or unsupported crawl bundle."""


def load_bundle(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.is_file():
        raise BundleError(f"Bundle file not found: {file_path}")
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"Could not read bundle: {exc}") from exc
    return validate_bundle(data)


def validate_bundle(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise BundleError("Bundle must be a JSON object")
    version = int(data.get("version") or 0)
    if version != BUNDLE_VERSION:
        raise BundleError(
            f"Unsupported bundle version {version!r} (expected {BUNDLE_VERSION})"
        )
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        raise BundleError("Bundle must include a non-empty pages array")
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise BundleError(f"pages[{index}] must be an object")
        html = str(page.get("html") or "")
        if not html.strip():
            raise BundleError(f"pages[{index}] is missing html")
        url = str(page.get("url") or "").strip()
        if not url:
            raise BundleError(f"pages[{index}] is missing url")
    return data


def pages_from_bundle(data: dict[str, Any]) -> CrawlResult:
    data = validate_bundle(data)
    result = CrawlResult()
    seen: set[str] = set()
    for page in data["pages"]:
        url = page_url_key(str(page.get("url") or "")) or str(page.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        html = str(page.get("html") or "")
        title = str(page.get("title") or "").strip()
        result.pages.append(
            CrawledPage(
                url=url,
                final_url=url,
                html=html,
                title=title,
                depth=0,
                source="bundle",
            )
        )
    if not result.pages:
        result.errors.append("Bundle contained no usable pages")
    return result


def write_bundle(
    path: str | Path,
    pages: list[CrawledPage],
    *,
    title: str = "",
    author: str = "",
    seed_url: str = "",
) -> Path:
    """Write a crawl bundle JSON (useful for tests / CLI round-trip)."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": BUNDLE_VERSION,
        "generator": BUNDLE_GENERATOR,
        "title": title or "",
        "author": author or "",
        "seed_url": seed_url or "",
        "pages": [
            {
                "url": page.url,
                "title": page.title or "",
                "html": page.html,
            }
            for page in pages
        ],
    }
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return dest


def bundle_meta(data: dict[str, Any]) -> dict[str, str]:
    return {
        "title": str(data.get("title") or "").strip(),
        "author": str(data.get("author") or "").strip(),
        "seed_url": str(data.get("seed_url") or "").strip(),
    }
