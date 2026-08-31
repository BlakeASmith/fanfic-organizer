"""Build a minimal EPUB3 from Wikipedia article HTML."""

from __future__ import annotations

import hashlib
import html
import mimetypes
import re
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse, urlunparse
from xml.sax.saxutils import escape

import requests

from ao3kit.htmlsoup import parse_html
from ao3kit.rate import USER_AGENT, wait_for_request

EPUB_DIRNAME = "epubs"
StatusCallback = Callable[[str], None]

# Prefer mid-size thumbs when srcset offers several widths.
_MAX_IMAGE_WIDTH = 1200
_MAX_IMAGES = 80

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|iframe)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_EDIT_RE = re.compile(
    r'<span\b[^>]*\bclass="[^"]*\bmw-editsection\b[^"]*"[^>]*>.*?</span>',
    re.IGNORECASE | re.DOTALL,
)
_PX_RE = re.compile(r"/(\d+)px-", re.IGNORECASE)
_SRCSET_PART_RE = re.compile(
    r"(?P<url>\S+)\s*(?:(?P<w>\d+)w|(?P<x>[\d.]+)x)?",
    re.IGNORECASE,
)

_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".avif": "image/avif",
}


def epub_relpath(work_id: str) -> str:
    return f"{EPUB_DIRNAME}/{work_id}.epub"


def wiki_site_origin(lang: str = "en") -> str:
    code = str(lang or "en").strip().lower() or "en"
    return f"https://{code}.wikipedia.org"


def absolutize_wiki_url(href: str, *, lang: str = "en", base: str = "") -> str | None:
    """Turn a MediaWiki href into an absolute https URL, or None to leave as-is."""
    text = str(href or "").strip()
    if not text:
        return None
    low = text.casefold()
    if low.startswith(("mailto:", "javascript:", "data:", "tel:")):
        return None
    if text.startswith("#"):
        return text
    origin = wiki_site_origin(lang)
    page_base = base or (origin + "/")
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("/"):
        return origin + text
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https"):
        return text
    if parsed.scheme:
        return None
    return urljoin(page_base, text)


def _pick_srcset_url(srcset: str) -> str | None:
    """Choose the largest candidate at or under ``_MAX_IMAGE_WIDTH``."""
    best_url: str | None = None
    best_score = -1.0
    for part in str(srcset or "").split(","):
        part = part.strip()
        if not part:
            continue
        match = _SRCSET_PART_RE.match(part)
        if not match:
            continue
        url = match.group("url")
        if not url:
            continue
        width = match.group("w")
        density = match.group("x")
        px = _PX_RE.search(url)
        if width:
            score = float(width)
        elif px:
            score = float(px.group(1))
        elif density:
            score = float(density) * 400.0
        else:
            score = 1.0
        if score > _MAX_IMAGE_WIDTH and best_url is not None:
            continue
        if score > best_score and score <= _MAX_IMAGE_WIDTH * 1.25:
            best_score = score
            best_url = url
        elif best_url is None:
            best_score = score
            best_url = url
    return best_url


def _image_fetch_url(tag) -> str | None:
    srcset = tag.get("srcset") or tag.get("data-srcset")
    if srcset:
        picked = _pick_srcset_url(srcset)
        if picked:
            return picked
    for attr in ("src", "data-src", "data-file-src"):
        value = tag.get(attr)
        if value and not str(value).strip().casefold().startswith("data:"):
            return str(value).strip()
    return None


def _extension_for(url: str, content_type: str | None) -> str:
    path = urlparse(url).path or ""
    # Drop thumb query / size prefix noise: .../250px-Foo.jpg
    name = path.rsplit("/", 1)[-1]
    if name.lower().startswith(("http",)):
        name = ""
    # 250px-Foo.Bar.jpg → Foo.Bar.jpg
    name = re.sub(r"^\d+px-", "", name)
    suffix = Path(name).suffix.lower()
    if suffix in _MEDIA_TYPES:
        return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed == ".jpe":
            guessed = ".jpg"
        if guessed and guessed.lower() in _MEDIA_TYPES:
            return guessed.lower()
    return ".jpg"


def _media_type_for(ext: str, content_type: str | None) -> str:
    if content_type:
        main = content_type.split(";")[0].strip().lower()
        if main.startswith("image/"):
            return main
    return _MEDIA_TYPES.get(ext.lower(), "application/octet-stream")


def _safe_image_filename(url: str, index: int, ext: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"img{index:03d}-{digest}{ext}"


def rewrite_article_links(soup, *, lang: str = "en", page_url: str = "") -> None:
    """Make wiki / protocol-relative hrefs absolute https so readers can open them."""
    origin = wiki_site_origin(lang)
    base = page_url or (origin + "/")
    for tag in soup.find_all("a", href=True):
        absolute = absolutize_wiki_url(tag["href"], lang=lang, base=base)
        if absolute is not None:
            tag["href"] = absolute


def absolutize_remote_images(soup, *, lang: str = "en", page_url: str = "") -> None:
    """Point img/srcset at https URLs (no download) for readers that allow remote media."""
    origin = wiki_site_origin(lang)
    base = page_url or (origin + "/")
    for tag in soup.find_all("img"):
        for attr in ("src", "data-src", "data-file-src"):
            if not tag.get(attr):
                continue
            absolute = absolutize_wiki_url(tag[attr], lang=lang, base=base)
            if absolute is not None:
                tag[attr] = absolute
        for attr in ("srcset", "data-srcset"):
            raw = tag.get(attr)
            if not raw:
                continue
            parts: list[str] = []
            for chunk in str(raw).split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                match = _SRCSET_PART_RE.match(chunk)
                if not match:
                    parts.append(chunk)
                    continue
                url = match.group("url")
                rest = chunk[len(url) :].strip()
                absolute = absolutize_wiki_url(url, lang=lang, base=base) or url
                parts.append(f"{absolute} {rest}".strip() if rest else absolute)
            tag[attr] = ", ".join(parts)


def _download_bytes(
    session: requests.Session,
    url: str,
    *,
    on_status: StatusCallback | None = None,
) -> tuple[bytes, str | None]:
    wait_for_request(url, on_status=on_status)
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type")


def embed_article_images(
    soup,
    *,
    lang: str = "en",
    page_url: str = "",
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
    max_images: int = _MAX_IMAGES,
) -> list[tuple[str, bytes, str]]:
    """Download images, rewrite ``src`` to ``images/…``, return EPUB assets.

    Each asset is ``(epub_relpath, bytes, media_type)`` under ``OEBPS/``.
    Failed downloads keep an absolute https ``src`` so online readers may still load them.
    """
    origin = wiki_site_origin(lang)
    base = page_url or (origin + "/")
    own = session is None
    sess = session or requests.Session()
    if own:
        sess.headers.update({"User-Agent": USER_AGENT})
    assets: list[tuple[str, bytes, str]] = []
    seen: dict[str, str] = {}
    try:
        for tag in soup.find_all("img"):
            raw = _image_fetch_url(tag)
            if len(assets) >= max(1, int(max_images)):
                if raw:
                    absolute = absolutize_wiki_url(raw, lang=lang, base=base)
                    if absolute:
                        tag["src"] = absolute
                tag.attrs.pop("srcset", None)
                tag.attrs.pop("data-srcset", None)
                continue
            if not raw:
                continue
            absolute = absolutize_wiki_url(raw, lang=lang, base=base)
            if not absolute or not absolute.startswith("https://"):
                continue
            host = (urlparse(absolute).netloc or "").lower()
            if "wikimedia.org" not in host and "wikipedia.org" not in host:
                # Non-wiki remote: leave absolute https, do not package.
                tag["src"] = absolute
                tag.attrs.pop("srcset", None)
                tag.attrs.pop("data-srcset", None)
                continue
            # Strip tracking query for cache key; keep full URL for fetch.
            clean = urlunparse(urlparse(absolute)._replace(query="", fragment=""))
            if clean in seen:
                tag["src"] = seen[clean]
                tag.attrs.pop("srcset", None)
                tag.attrs.pop("data-srcset", None)
                for attr in ("data-src", "data-file-src"):
                    tag.attrs.pop(attr, None)
                continue
            try:
                if on_status:
                    on_status(f"Downloading image {len(assets) + 1}…")
                data, content_type = _download_bytes(
                    sess, absolute, on_status=on_status
                )
            except (requests.RequestException, OSError):
                tag["src"] = absolute
                tag.attrs.pop("srcset", None)
                tag.attrs.pop("data-srcset", None)
                continue
            if not data:
                tag["src"] = absolute
                continue
            ext = _extension_for(clean, content_type)
            filename = _safe_image_filename(clean, len(assets) + 1, ext)
            rel = f"images/{filename}"
            media = _media_type_for(ext, content_type)
            assets.append((rel, data, media))
            seen[clean] = rel
            tag["src"] = rel
            tag.attrs.pop("srcset", None)
            tag.attrs.pop("data-srcset", None)
            for attr in ("data-src", "data-file-src"):
                tag.attrs.pop(attr, None)
    finally:
        if own:
            sess.close()
    return assets


def _clean_article_html(raw: str) -> str:
    text = _SCRIPT_STYLE_RE.sub("", str(raw or ""))
    text = _EDIT_RE.sub("", text)
    soup = parse_html(text)
    for tag in soup.find_all(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    # Collect first — decomposing a navbox invalidates nested matches (attrs=None).
    to_remove = []
    for tag in soup.select(".mw-editsection, .navbox"):
        if getattr(tag, "attrs", None) is None:
            continue
        to_remove.append(tag)
    for tag in to_remove:
        tag.decompose()
    body = soup.body
    inner = body.decode_contents() if body is not None else str(soup)
    return inner.strip() or "<p></p>"


def prepare_article_fragment(
    html_body: str,
    *,
    lang: str = "en",
    page_url: str = "",
    include_images: bool = False,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> tuple[str, list[tuple[str, bytes, str]]]:
    """Clean HTML, rewrite links, optionally embed images. Returns (fragment, assets)."""
    fragment = _clean_article_html(html_body)
    soup = parse_html(f"<div id='ao3kit-wiki-root'>{fragment}</div>")
    root = soup.find(id="ao3kit-wiki-root") or soup
    rewrite_article_links(root, lang=lang, page_url=page_url)
    assets: list[tuple[str, bytes, str]] = []
    if include_images:
        assets = embed_article_images(
            root,
            lang=lang,
            page_url=page_url,
            session=session,
            on_status=on_status,
        )
    else:
        absolutize_remote_images(root, lang=lang, page_url=page_url)
    inner = root.decode_contents() if hasattr(root, "decode_contents") else str(root)
    return inner.strip() or "<p></p>", assets


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
    include_images: bool = False,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> Path:
    """Write a single-chapter EPUB3; returns the path."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    safe_title = str(title or "Wikipedia article").strip() or "Wikipedia article"
    lang = str(language or "en").strip() or "en"
    page_url = str(url or "").strip()
    fragment, image_assets = prepare_article_fragment(
        html_body,
        lang=lang,
        page_url=page_url,
        include_images=include_images,
        session=session,
        on_status=on_status,
    )
    book_id = str(work_id or dest.stem).strip() or "wikipedia"
    uid = f"wikipedia:{book_id}"
    source_line = ""
    if page_url:
        source_line = (
            f'<p class="source"><a href="{html.escape(page_url, quote=True)}">'
            f"{escape(page_url)}</a></p>"
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
    img {{ max-width: 100%; height: auto; }}
  </style>
</head>
<body>
  <h1>{escape(safe_title)}</h1>
  {source_line}
  {fragment}
</body>
</html>
"""

    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
    ]
    for index, (rel, _data, media) in enumerate(image_assets, start=1):
        manifest_items.append(
            f'<item id="img{index}" href="{escape(rel)}" media-type="{escape(media)}"/>'
        )
    manifest = "\n    ".join(manifest_items)

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
    {manifest}
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
        for rel, data, _media in image_assets:
            zf.writestr(f"OEBPS/{rel}", data)
    return dest


def attach_epub_to_record(
    record: dict[str, Any],
    dest_dir: str | Path,
    html_body: str,
    *,
    cover: bool = True,
    include_images: bool = False,
    session: requests.Session | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Write EPUB under ``dest_dir/epubs/{id}.epub`` and set ``epub_file``."""
    work_id = str(record.get("work_id") or "").strip()
    if not work_id:
        raise ValueError("record missing work_id")
    root = Path(dest_dir)
    rel = epub_relpath(work_id)
    path = root / rel
    meta = dict(record.get("metadata") or {})
    lang = str(meta.get("language") or "en")
    page_url = str(record.get("url") or "")
    fragment, _assets = prepare_article_fragment(
        html_body,
        lang=lang,
        page_url=page_url,
        include_images=False,
    )
    words = _plain_word_count(fragment)
    if words:
        meta["words"] = words
    write_article_epub(
        path,
        title=str(record.get("title") or work_id),
        html_body=html_body,
        author=str(record.get("author") or "Wikipedia contributors"),
        language=lang,
        url=page_url,
        work_id=work_id,
        include_images=include_images,
        session=session,
        on_status=on_status,
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
