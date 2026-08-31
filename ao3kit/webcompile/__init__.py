"""Multi-page web compilation: crawl → preprocess → unified EPUB.

Standalone library (usable outside the Calibre plugin). The Calibre
**Import → URL or HTML…** dialog and ``python -m ao3kit webcompile`` both call
this package.

Phases
------
1. **Crawl** — collect source HTML from seed URLs (with free / same-domain /
   specific-domains link expansion), an explicit URL list, local HTML files, or
   a Tampermonkey export bundle.
2. **Preprocess** — extract main content and rewrite ``href``s so links between
   collected pages become in-EPUB chapter links.
3. **EPUB** — write one multi-chapter EPUB3 with a TOC nav document.

Imports are lazy so Calibre can load ``ao3kit.webcompile.userscript`` without
pulling ``requests`` / crawl into the GUI process.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "BUNDLE_GENERATOR",
    "BUNDLE_VERSION",
    "BundleError",
    "CompiledChapter",
    "CompileOptions",
    "CompileResult",
    "CrawlOptions",
    "CrawledPage",
    "CrawlResult",
    "ExpandMode",
    "attach_compiled_epub",
    "compile_bundle_file",
    "compile_from_crawl",
    "compile_html_files",
    "compile_pages",
    "crawl_urls",
    "extract_links",
    "link_allowed",
    "load_bundle",
    "parse_expand_mode",
    "pages_from_html_files",
    "preprocess_pages",
    "resolve_userscript",
    "rewrite_internal_links",
    "userscript_text",
    "write_bundle",
    "write_compiled_epub",
]

_LAZY: dict[str, tuple[str, str]] = {
    "BUNDLE_GENERATOR": ("ao3kit.webcompile.bundle", "BUNDLE_GENERATOR"),
    "BUNDLE_VERSION": ("ao3kit.webcompile.bundle", "BUNDLE_VERSION"),
    "BundleError": ("ao3kit.webcompile.bundle", "BundleError"),
    "load_bundle": ("ao3kit.webcompile.bundle", "load_bundle"),
    "write_bundle": ("ao3kit.webcompile.bundle", "write_bundle"),
    "crawl_urls": ("ao3kit.webcompile.crawl", "crawl_urls"),
    "extract_links": ("ao3kit.webcompile.crawl", "extract_links"),
    "link_allowed": ("ao3kit.webcompile.crawl", "link_allowed"),
    "pages_from_html_files": ("ao3kit.webcompile.crawl", "pages_from_html_files"),
    "attach_compiled_epub": ("ao3kit.webcompile.epub", "attach_compiled_epub"),
    "write_compiled_epub": ("ao3kit.webcompile.epub", "write_compiled_epub"),
    "CompiledChapter": ("ao3kit.webcompile.models", "CompiledChapter"),
    "CompileOptions": ("ao3kit.webcompile.models", "CompileOptions"),
    "CompileResult": ("ao3kit.webcompile.models", "CompileResult"),
    "CrawlOptions": ("ao3kit.webcompile.models", "CrawlOptions"),
    "CrawledPage": ("ao3kit.webcompile.models", "CrawledPage"),
    "CrawlResult": ("ao3kit.webcompile.models", "CrawlResult"),
    "ExpandMode": ("ao3kit.webcompile.models", "ExpandMode"),
    "compile_bundle_file": ("ao3kit.webcompile.pipeline", "compile_bundle_file"),
    "compile_from_crawl": ("ao3kit.webcompile.pipeline", "compile_from_crawl"),
    "compile_html_files": ("ao3kit.webcompile.pipeline", "compile_html_files"),
    "compile_pages": ("ao3kit.webcompile.pipeline", "compile_pages"),
    "parse_expand_mode": ("ao3kit.webcompile.pipeline", "parse_expand_mode"),
    "preprocess_pages": ("ao3kit.webcompile.preprocess", "preprocess_pages"),
    "rewrite_internal_links": ("ao3kit.webcompile.preprocess", "rewrite_internal_links"),
    "resolve_userscript": ("ao3kit.webcompile.userscript", "resolve_userscript"),
    "userscript_text": ("ao3kit.webcompile.userscript", "userscript_text"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    from importlib import import_module

    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value
