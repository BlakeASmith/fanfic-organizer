"""Best-effort HTML article extraction (static pages).

Pulls title / author / summary / date / language from common meta tags and
JSON-LD when present, then picks a main content fragment for EPUB packing.

This is intentionally dependency-light (BeautifulSoup only). It does **not**
execute JavaScript — SPAs and other client-rendered pages usually need a
browser "Save Page" / "Download page" HTML export instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from ao3kit.htmlsoup import parse_html

_WS_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# Tags kept in the EPUB body fragment (plus text).
_KEEP_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "cite",
        "code",
        "dd",
        "del",
        "div",
        "dl",
        "dt",
        "em",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "mark",
        "ol",
        "p",
        "pre",
        "q",
        "s",
        "section",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)

_DROP_SELECTORS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "canvas",
    "form",
    "button",
    "input",
    "textarea",
    "select",
    "nav",
    "header",
    "footer",
    "aside",
    "[role='navigation']",
    "[role='banner']",
    "[role='contentinfo']",
    ".nav",
    ".navbar",
    ".navigation",
    ".menu",
    ".sidebar",
    ".advert",
    ".ad",
    ".ads",
    ".cookie",
    ".share",
    ".social",
    ".comments",
    "#comments",
)


@dataclass
class ExtractedPage:
    title: str = "Untitled"
    author: str | None = None
    summary: str | None = None
    date: str | None = None
    language: str | None = None
    site_name: str | None = None
    html_body: str = "<p></p>"
    tags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    word_count: int = 0


def _norm_space(text: str | None) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def _meta_content(soup, *, prop: str | None = None, name: str | None = None) -> str:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return _norm_space(tag["content"])
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return _norm_space(tag["content"])
    return ""


def _date_yyyy_mm_dd(value: Any) -> str | None:
    text = _norm_space(str(value or ""))
    if not text:
        return None
    match = _DATE_RE.match(text)
    return match.group(1) if match else None


def _json_ld_blocks(soup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    stack.extend(item["@graph"])
                else:
                    out.append(item)
    return out


def _json_ld_author(block: dict[str, Any]) -> str | None:
    author = block.get("author")
    if isinstance(author, str):
        return _norm_space(author) or None
    if isinstance(author, dict):
        return _norm_space(author.get("name")) or None
    if isinstance(author, list):
        names: list[str] = []
        for entry in author:
            if isinstance(entry, str):
                name = _norm_space(entry)
            elif isinstance(entry, dict):
                name = _norm_space(entry.get("name"))
            else:
                name = ""
            if name:
                names.append(name)
        return ", ".join(names) if names else None
    return None


def _type_matches(block: dict[str, Any], *wanted: str) -> bool:
    raw = block.get("@type")
    types: list[str] = []
    if isinstance(raw, str):
        types = [raw]
    elif isinstance(raw, list):
        types = [str(t) for t in raw]
    wanted_cf = {w.casefold() for w in wanted}
    return any(t.casefold() in wanted_cf for t in types)


def extract_metadata(soup, *, url: str = "") -> dict[str, Any]:
    """Return title/author/summary/date/language/site_name/tags from head + JSON-LD."""
    title = (
        _meta_content(soup, prop="og:title")
        or _meta_content(soup, name="twitter:title")
        or _norm_space(soup.title.string if soup.title else "")
    )
    h1 = soup.find("h1")
    if not title and h1 is not None:
        title = _norm_space(h1.get_text(" ", strip=True))

    author = (
        _meta_content(soup, name="author")
        or _meta_content(soup, prop="article:author")
        or _meta_content(soup, name="byl")
    )
    summary = (
        _meta_content(soup, prop="og:description")
        or _meta_content(soup, name="twitter:description")
        or _meta_content(soup, name="description")
    )
    date = _date_yyyy_mm_dd(
        _meta_content(soup, prop="article:published_time")
        or _meta_content(soup, name="pubdate")
        or _meta_content(soup, name="publishdate")
        or _meta_content(soup, name="date")
    )
    language = ""
    html_tag = soup.find("html")
    if html_tag is not None:
        language = _norm_space(html_tag.get("lang") or html_tag.get("xml:lang"))
    if not language:
        language = _meta_content(soup, prop="og:locale").replace("_", "-")
    if language and "-" in language:
        language = language.split("-", 1)[0].lower()
    elif language:
        language = language.lower()[:12]

    site_name = _meta_content(soup, prop="og:site_name")
    if not site_name and url:
        host = urlparse(url).hostname or ""
        if host.startswith("www."):
            host = host[4:]
        site_name = host or None

    tags: list[str] = []
    keywords = _meta_content(soup, name="keywords")
    if keywords:
        for part in re.split(r"[,;]", keywords):
            tag = _norm_space(part)
            if tag and tag not in tags:
                tags.append(tag)
    for section in soup.find_all("meta", attrs={"property": "article:tag"}):
        tag = _norm_space(section.get("content"))
        if tag and tag not in tags:
            tags.append(tag)

    for block in _json_ld_blocks(soup):
        if not _type_matches(
            block,
            "Article",
            "NewsArticle",
            "BlogPosting",
            "WebPage",
            "Report",
            "TechArticle",
        ):
            continue
        if not title:
            title = _norm_space(block.get("headline") or block.get("name"))
        if not author:
            author = _json_ld_author(block) or ""
        if not summary:
            summary = _norm_space(block.get("description"))
        if not date:
            date = _date_yyyy_mm_dd(
                block.get("datePublished") or block.get("dateCreated")
            )
        if not language:
            language = _norm_space(block.get("inLanguage")).lower()[:12] or language
        kw = block.get("keywords")
        if isinstance(kw, str):
            for part in re.split(r"[,;]", kw):
                tag = _norm_space(part)
                if tag and tag not in tags:
                    tags.append(tag)
        elif isinstance(kw, list):
            for part in kw:
                tag = _norm_space(part)
                if tag and tag not in tags:
                    tags.append(tag)

    return {
        "title": title or "Untitled",
        "author": author or None,
        "summary": summary or None,
        "date": date,
        "language": language or None,
        "site_name": site_name or None,
        "tags": tags,
    }


def _link_density(node) -> float:
    text_len = len(_norm_space(node.get_text(" ", strip=True)))
    if text_len <= 0:
        return 1.0
    link_len = 0
    for a in node.find_all("a"):
        link_len += len(_norm_space(a.get_text(" ", strip=True)))
    return min(1.0, link_len / float(text_len))


def _score_node(node) -> float:
    text = _norm_space(node.get_text(" ", strip=True))
    if len(text) < 80:
        return 0.0
    paragraphs = node.find_all("p")
    p_score = sum(min(len(_norm_space(p.get_text(" ", strip=True))), 500) for p in paragraphs)
    density = _link_density(node)
    score = float(len(text)) + float(p_score)
    score *= max(0.05, 1.0 - density)
    classes = " ".join(node.get("class") or []).casefold()
    node_id = str(node.get("id") or "").casefold()
    blob = f"{classes} {node_id}"
    if any(bad in blob for bad in ("comment", "sidebar", "footer", "nav", "menu", "related")):
        score *= 0.25
    if any(good in blob for good in ("content", "article", "post", "entry", "main", "story")):
        score *= 1.35
    return score


def _strip_chrome(soup) -> None:
    for selector in _DROP_SELECTORS:
        for tag in soup.select(selector):
            tag.decompose()


def _choose_content_root(soup):
    for selector in ("article", "main", "[role='main']"):
        node = soup.select_one(selector)
        if node is None:
            continue
        text = _norm_space(node.get_text(" ", strip=True))
        if len(text) >= 120 and _link_density(node) < 0.6:
            return node

    candidates = []
    body = soup.body or soup
    for tag in body.find_all(["article", "section", "div", "main"]):
        score = _score_node(tag)
        if score > 0:
            candidates.append((score, tag))
    if not candidates:
        return body
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _serialize_clean(node, *, base_url: str = "") -> str:
    """Return a cleaned HTML fragment from ``node``."""
    clone = parse_html(str(node))
    root = clone.body if clone.body is not None else clone
    # Unwrap the outer wrapper soup adds when parsing a fragment.
    if len(list(root.children)) == 1 and getattr(root, "contents", None):
        only = root.contents[0]
        if getattr(only, "name", None) == getattr(node, "name", None):
            root = only

    for tag in list(root.find_all(True)):
        name = (tag.name or "").lower()
        if name not in _KEEP_TAGS:
            tag.unwrap() if tag.get_text(strip=True) else tag.decompose()
            continue
        # Drop event handlers / style noise.
        attrs = dict(tag.attrs or {})
        for key in list(attrs):
            key_l = str(key).lower()
            if key_l.startswith("on") or key_l in {"style", "class", "id"}:
                del tag.attrs[key]
        if name == "a":
            href = tag.get("href")
            if href:
                tag["href"] = urljoin(base_url, href) if base_url else href
            else:
                tag.unwrap()
        elif name == "img":
            src = tag.get("src") or tag.get("data-src")
            if not src:
                tag.decompose()
                continue
            tag.attrs = {"src": urljoin(base_url, src) if base_url else src}
            alt = _norm_space(attrs.get("alt"))
            if alt:
                tag["alt"] = alt

    if getattr(root, "decode_contents", None):
        inner = root.decode_contents()
    else:
        inner = str(root)
    inner = inner.strip()
    return inner or "<p></p>"


def extract_page(html: str, *, url: str = "") -> ExtractedPage:
    """Extract metadata + main HTML body from a page string."""
    warnings: list[str] = [
        "Generic URL/HTML import is best-effort and often fails on "
        "JavaScript-rendered (dynamic) sites. For those, save the page from "
        "your browser (Save Page / Download page) and import the HTML file."
    ]
    raw = str(html or "")
    if not raw.strip():
        return ExtractedPage(warnings=warnings + ["Empty HTML input."])

    soup = parse_html(raw)
    meta = extract_metadata(soup, url=url)

    # Heuristic: lots of scripts, little visible text → likely dynamic.
    scripts = soup.find_all("script")
    body = soup.body or soup
    visible = _norm_space(body.get_text(" ", strip=True))
    if len(scripts) >= 8 and len(visible) < 400:
        warnings.append(
            "Page looks script-heavy with little static text — content may be "
            "incomplete. Prefer a browser-exported HTML file."
        )

    work = parse_html(raw)
    _strip_chrome(work)
    root = _choose_content_root(work)
    html_body = _serialize_clean(root, base_url=url)
    words = re.findall(r"\w+", _norm_space(parse_html(html_body).get_text(" ", strip=True)), flags=re.UNICODE)
    if len(words) < 40:
        warnings.append(
            "Extracted very little text; the page may be paywalled, empty, or "
            "dynamically rendered."
        )

    tags = list(meta["tags"])
    site = meta.get("site_name")
    if site and site not in tags:
        tags.append(site)

    return ExtractedPage(
        title=str(meta["title"] or "Untitled"),
        author=meta.get("author"),
        summary=meta.get("summary"),
        date=meta.get("date"),
        language=meta.get("language"),
        site_name=site,
        html_body=html_body,
        tags=tags,
        warnings=warnings,
        word_count=len(words),
    )
