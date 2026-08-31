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
"""

from __future__ import annotations

from ao3kit.webcompile.bundle import (
    BUNDLE_GENERATOR,
    BUNDLE_VERSION,
    BundleError,
    load_bundle,
    write_bundle,
)
from ao3kit.webcompile.crawl import (
    crawl_urls,
    extract_links,
    link_allowed,
    pages_from_html_files,
)
from ao3kit.webcompile.epub import attach_compiled_epub, write_compiled_epub
from ao3kit.webcompile.models import (
    CompiledChapter,
    CompileOptions,
    CompileResult,
    CrawlOptions,
    CrawledPage,
    CrawlResult,
    ExpandMode,
)
from ao3kit.webcompile.pipeline import (
    compile_bundle_file,
    compile_from_crawl,
    compile_html_files,
    compile_pages,
    parse_expand_mode,
)
from ao3kit.webcompile.preprocess import preprocess_pages, rewrite_internal_links
from ao3kit.webcompile.userscript import resolve_userscript, userscript_text

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
