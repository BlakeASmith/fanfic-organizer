"""Build a minimal EPUB3 from Wikipedia article HTML."""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from ao3kit.htmlsoup import parse_html

EPUB_DIRNAME = "epubs"
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|iframe)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_EDIT_RE = re.compile(
    r'<span\b[^>]*\bclass="[^"]*\bmw-editsection\b[^"]*"[^>]*>.*?</span>',
    re.IGNORECASE | re.DOTALL,
)


def epub_relpath(work_id: str) -> str:
    return f"{EPUB_DIRNAME}/{work_id}.epub"


def _clean_article_html(raw: str) -> str:
    text = _SCRIPT_STYLE_RE.sub("", str(raw or ""))
    text = _EDIT_RE.sub("", text)
    soup = parse_html(text)
    for tag in soup.find_all(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    for tag in soup.select(".mw-editsection, .navbox, .reference"):
        # Keep references list content; only strip inline [edit] / navboxes.
        classes = tag.get("class") or []
        if "reference" in classes and tag.name == "sup":
            continue
        if "navbox" in classes or "mw-editsection" in classes:
            tag.decompose()
    body = soup.body
    inner = body.decode_contents() if body is not None else str(soup)
    return inner.strip() or "<p></p>"


def _plain_word_count(fragment: str) -> int:
    soup = parse_html(f"<div>{fragment}</div>")
    words = re.findall(r"\w+", soup.get_text(" ", strip=True), flags=re.UNICODE)
    return len(words)


def write_article_epub(
    path: str | Path,
    *,
    title: str,
    html_body: str,
    author: str = "Wikipedia contributors",
    language: str = "en",
    url: str = "",
    work_id: str = "",
) -> Path:
    """Write a single-chapter EPUB3; returns the path."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    safe_title = str(title or "Wikipedia article").strip() or "Wikipedia article"
    fragment = _clean_article_html(html_body)
    lang = str(language or "en").strip() or "en"
    book_id = str(work_id or dest.stem).strip() or "wikipedia"
    uid = f"wikipedia:{book_id}"
    source_line = ""
    if url:
        source_line = (
            f'<p class="source"><a href="{html.escape(url, quote=True)}">'
            f"{escape(url)}</a></p>"
        )

    chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{escape(lang)}" lang="{escape(lang)}">
<head>
  <meta charset="utf-8"/>
  <title>{escape(safe_title)}</title>
  <style type="text/css">
    body {{ font-family: serif; line-height: 1.45; margin: 1em; }}
    h1, h2, h3 {{ line-height: 1.2; }}
    .source {{ font-size: 0.9em; color: #444; }}
    table {{ border-collapse: collapse; margin: 0.5em 0; }}
    th, td {{ border: 1px solid #ccc; padding: 0.25em 0.4em; vertical-align: top; }}
  </style>
</head>
<body>
  <h1>{escape(safe_title)}</h1>
  {source_line}
  {fragment}
</body>
</html>
"""

    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">{escape(uid)}</dc:identifier>
    <dc:title>{escape(safe_title)}</dc:title>
    <dc:creator>{escape(author or "Wikipedia contributors")}</dc:creator>
    <dc:language>{escape(lang)}</dc:language>
    <dc:publisher>Wikipedia</dc:publisher>
    <meta property="dcterms:modified">2020-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
"""

    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      xml:lang="{escape(lang)}" lang="{escape(lang)}">
<head><title>Navigation</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Contents</h1>
    <ol>
      <li><a href="chapter.xhtml">{escape(safe_title)}</a></li>
    </ol>
  </nav>
</body>
</html>
"""

    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

    with zipfile.ZipFile(dest, "w") as zf:
        # mimetype must be first and uncompressed for EPUB.
        zf.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/nav.xhtml", nav)
        zf.writestr("OEBPS/chapter.xhtml", chapter)
    return dest


def attach_epub_to_record(
    record: dict[str, Any],
    dest_dir: str | Path,
    html_body: str,
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
    fragment = _clean_article_html(html_body)
    words = _plain_word_count(fragment)
    if words:
        meta["words"] = words
    write_article_epub(
        path,
        title=str(record.get("title") or work_id),
        html_body=html_body,
        author=str(record.get("author") or "Wikipedia contributors"),
        language=str(meta.get("language") or "en"),
        url=str(record.get("url") or ""),
        work_id=work_id,
    )
    updated = dict(record)
    updated["metadata"] = meta
    updated["epub_file"] = rel
    updated.pop("epub_error", None)
    if cover:
        try:
            from ao3kit.covers import maybe_stamp_downloaded_epub

            err = maybe_stamp_downloaded_epub(path, updated, cover=True)
            if err:
                updated["cover_error"] = err
        except Exception as exc:  # pragma: no cover — cover is best-effort
            updated["cover_error"] = str(exc)
    return updated
