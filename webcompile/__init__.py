"""Multi-page web compilation: crawl → preprocess → unified EPUB.

Standalone package (not part of ``ao3kit``) for compiling linked web pages into
one EPUB. The Calibre **Import → URL or HTML…** dialog and
``python -m webcompile`` (also ``python -m ao3kit webcompile``) call this
package.

Phases
------
1. **Crawl** — Tampermonkey bundle (preferred for JS sites), seed URLs with
   free / same-domain / specific-domains expansion, an explicit URL list, or
   local HTML files.
2. **Preprocess** — extract main content and rewrite ``href``s so links between
   collected pages become in-EPUB chapter links.
3. **EPUB** — write one multi-chapter EPUB3 with a TOC nav document.

Imports are lazy so Calibre can load ``webcompile.userscript`` without pulling
``requests`` / crawl into the GUI process.
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
    "BUNDLE_GENERATOR": ("webcompile.bundle", "BUNDLE_GENERATOR"),
    "BUNDLE_VERSION": ("webcompile.bundle", "BUNDLE_VERSION"),
    "BundleError": ("webcompile.bundle", "BundleError"),
    "load_bundle": ("webcompile.bundle", "load_bundle"),
    "write_bundle": ("webcompile.bundle", "write_bundle"),
    "crawl_urls": ("webcompile.crawl", "crawl_urls"),
    "extract_links": ("webcompile.crawl", "extract_links"),
    "link_allowed": ("webcompile.crawl", "link_allowed"),
    "pages_from_html_files": ("webcompile.crawl", "pages_from_html_files"),
    "attach_compiled_epub": ("webcompile.epub", "attach_compiled_epub"),
    "write_compiled_epub": ("webcompile.epub", "write_compiled_epub"),
    "CompiledChapter": ("webcompile.models", "CompiledChapter"),
    "CompileOptions": ("webcompile.models", "CompileOptions"),
    "CompileResult": ("webcompile.models", "CompileResult"),
    "CrawlOptions": ("webcompile.models", "CrawlOptions"),
    "CrawledPage": ("webcompile.models", "CrawledPage"),
    "CrawlResult": ("webcompile.models", "CrawlResult"),
    "ExpandMode": ("webcompile.models", "ExpandMode"),
    "compile_bundle_file": ("webcompile.pipeline", "compile_bundle_file"),
    "compile_from_crawl": ("webcompile.pipeline", "compile_from_crawl"),
    "compile_html_files": ("webcompile.pipeline", "compile_html_files"),
    "compile_pages": ("webcompile.pipeline", "compile_pages"),
    "parse_expand_mode": ("webcompile.pipeline", "parse_expand_mode"),
    "preprocess_pages": ("webcompile.preprocess", "preprocess_pages"),
    "rewrite_internal_links": ("webcompile.preprocess", "rewrite_internal_links"),
    "resolve_userscript": ("webcompile.userscript", "resolve_userscript"),
    "userscript_text": ("webcompile.userscript", "userscript_text"),
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
