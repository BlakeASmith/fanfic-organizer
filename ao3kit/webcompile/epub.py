"""Phase 3: build a unified multi-chapter EPUB3 with TOC."""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from ao3kit.htmlsoup import parse_html
from ao3kit.sources.web_epub import EPUB_DIRNAME, epub_relpath
from ao3kit.webcompile.models import CompiledChapter

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|iframe)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)


def _clean_fragment(raw: str) -> str:
    text = _SCRIPT_STYLE_RE.sub("", str(raw or ""))
    soup = parse_html(text)
    for tag in soup.find_all(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    body = soup.body
    inner = body.decode_contents() if body is not None else str(soup)
    return inner.strip() or "<p></p>"


def _chapter_xhtml(
    chapter: CompiledChapter,
    *,
    language: str,
) -> str:
    lang = escape(language)
    title = escape(chapter.title or "Chapter")
    fragment = _clean_fragment(chapter.html_body)
    source = ""
    if chapter.url and not chapter.url.startswith("file:"):
        source = (
            f'<p class="source"><a href="{html.escape(chapter.url, quote=True)}">'
            f"{escape(chapter.url)}</a></p>"
        )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}" lang="{lang}">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <style type="text/css">
    body {{ font-family: serif; line-height: 1.45; margin: 1em; }}
    h1, h2, h3 {{ line-height: 1.2; }}
    .source {{ font-size: 0.9em; color: #444; }}
    img {{ max-width: 100%; height: auto; }}
    table {{ border-collapse: collapse; margin: 0.5em 0; }}
    th, td {{ border: 1px solid #ccc; padding: 0.25em 0.4em; vertical-align: top; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  {source}
  {fragment}
</body>
</html>
"""


def _nav_xhtml(chapters: list[CompiledChapter], *, language: str) -> str:
    lang = escape(language)
    items = []
    for chapter in chapters:
        href = escape(chapter.chapter_href or "chapter-001.xhtml")
        items.append(f'      <li><a href="{href}">{escape(chapter.title)}</a></li>')
    body = "\n".join(items)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      xml:lang="{lang}" lang="{lang}">
<head><title>Navigation</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Contents</h1>
    <ol>
{body}
    </ol>
  </nav>
</body>
</html>
"""


def _opf(
    chapters: list[CompiledChapter],
    *,
    title: str,
    author: str,
    language: str,
    work_id: str,
    publisher: str = "Web",
) -> str:
    uid = escape(f"web:{work_id}")
    manifest_items = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    ]
    spine_items = []
    for index, chapter in enumerate(chapters, start=1):
        cid = f"chap{index:03d}"
        href = escape(chapter.chapter_href)
        manifest_items.append(
            f'    <item id="{cid}" href="{href}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'    <itemref idref="{cid}"/>')
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">{uid}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:creator>{escape(author or "Unknown")}</dc:creator>
    <dc:language>{escape(language)}</dc:language>
    <dc:publisher>{escape(publisher)}</dc:publisher>
    <meta property="dcterms:modified">2020-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
{chr(10).join(manifest_items)}
  </manifest>
  <spine>
{chr(10).join(spine_items)}
  </spine>
</package>
"""


_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def write_compiled_epub(
    path: str | Path,
    chapters: list[CompiledChapter],
    *,
    title: str,
    author: str = "Unknown",
    language: str = "en",
    work_id: str = "",
    publisher: str = "Web",
) -> Path:
    """Write a multi-chapter EPUB3 with a TOC nav document."""
    if not chapters:
        raise ValueError("No chapters to write")
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    safe_title = str(title or chapters[0].title or "Web pages").strip() or "Web pages"
    lang = str(language or "en").strip() or "en"
    book_id = str(work_id or dest.stem).strip() or "webcompile"

    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr(
            "OEBPS/content.opf",
            _opf(
                chapters,
                title=safe_title,
                author=author,
                language=lang,
                work_id=book_id,
                publisher=publisher,
            ),
        )
        zf.writestr("OEBPS/nav.xhtml", _nav_xhtml(chapters, language=lang))
        for chapter in chapters:
            name = chapter.chapter_href or "chapter-001.xhtml"
            zf.writestr(
                f"OEBPS/{name}",
                _chapter_xhtml(chapter, language=lang),
            )
    return dest


def attach_compiled_epub(
    record: dict[str, Any],
    chapters: list[CompiledChapter],
    dest_dir: str | Path,
    *,
    cover: bool = True,
) -> dict[str, Any]:
    """Write EPUB under ``dest_dir/epubs/{id}.epub`` and set ``epub_file``."""
    work_id = str(record.get("work_id") or "").strip()
    if not work_id:
        raise ValueError("record missing work_id")
    root = Path(dest_dir)
    rel = epub_relpath(work_id)
    path = root / rel
    meta = dict(record.get("metadata") or {})
    words = sum(int(ch.word_count or 0) for ch in chapters)
    if words:
        meta["words"] = words
    write_compiled_epub(
        path,
        chapters,
        title=str(record.get("title") or work_id),
        author=str(record.get("author") or "Unknown"),
        language=str(meta.get("language") or "en"),
        work_id=work_id,
        publisher="Web",
    )
    updated = dict(record)
    updated["metadata"] = meta
    updated["epub_file"] = rel
    updated["page_count"] = len(chapters)
    updated.pop("epub_error", None)
    if cover:
        try:
            from ao3kit.covers import maybe_stamp_downloaded_epub

            err = maybe_stamp_downloaded_epub(path, updated, cover=True)
            if err:
                updated["cover_error"] = err
        except Exception as exc:  # pragma: no cover
            updated["cover_error"] = str(exc)
    return updated


__all__ = [
    "EPUB_DIRNAME",
    "attach_compiled_epub",
    "epub_relpath",
    "write_compiled_epub",
]
