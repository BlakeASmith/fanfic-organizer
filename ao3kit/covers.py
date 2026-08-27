"""Generate AO3-style EPUB covers and stamp them into native EPUB files.

Layout and fandom-seeded colours follow
https://github.com/alexwlchan/add-cover-to-ao3-files (title + author on a
dark gradient). Extra fields, palettes, fonts, and sizes are configurable
via :class:`ao3kit.config.CoverSettings`.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET

from ao3kit.config import (
    COVER_COLOR_MODES,
    COVER_COLOR_SEEDS,
    COVER_FIELDS,
    CoverSettings,
    load_cover_settings,
    merge_cover_settings,
    parse_color_map,
)
from ao3kit.scrape import calculate_quality_score, resolve_quality_score

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
XHTML_NS = "http://www.w3.org/1999/xhtml"

_RATING_TAGS = (
    "Not Rated",
    "General Audiences",
    "Teen And Up Audiences",
    "Mature",
    "Explicit",
)

_FONT_CANDIDATES = (
    "Georgia",
    "Times New Roman",
    "Times",
    "Liberation Serif",
    "DejaVu Serif",
    "Nimbus Roman",
    "Noto Serif",
    "Serif",
)

_FONT_FILES = (
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/Library/Fonts/Georgia.ttf",
    "/Library/Fonts/Times New Roman.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
    "C:/Windows/Fonts/georgia.ttf",
    "C:/Windows/Fonts/times.ttf",
)


class CoverError(Exception):
    """Cover generation or EPUB injection failed."""


def _load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CoverError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if isinstance(record, dict):
            records.append(record)
    return records


@dataclass
class CoverInfo:
    title: str = ""
    author: str = ""
    fandom: str = ""
    relationship: str = ""
    series: str = ""
    rating: str = ""
    wordcount: int | None = None
    score: float | None = None
    complete: bool | None = None
    work_id: str = ""

    def seed_text(self, which: str) -> str:
        mapping = {
            "fandom": self.fandom,
            "relationship": self.relationship,
            "author": self.author,
            "title": self.title,
            "work_id": self.work_id,
        }
        text = str(mapping.get(which) or "").strip()
        if text:
            return text
        for fallback in (self.fandom, self.title, self.author, self.work_id):
            if fallback:
                return fallback
        return "ao3"


@dataclass
class CoverOutcome:
    path: Path | None
    status: str
    png_path: Path | None = None
    error: str | None = None
    info: CoverInfo | None = None


def _local(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _find_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in list(parent):
        if _local(child.tag) == name:
            return child
    return None


def _iter_local(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(parent) if _local(child.tag) == name]


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return str(el.text).strip()


def _zip_read(zf: zipfile.ZipFile, name: str) -> bytes:
    with zf.open(name) as handle:
        return handle.read()


def _zip_names(zf: zipfile.ZipFile) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name in zf.namelist():
        mapping[name.replace("\\", "/")] = name
    return mapping


def find_opf_path(zf: zipfile.ZipFile) -> str | None:
    names = _zip_names(zf)
    container_name = names.get("META-INF/container.xml")
    if container_name:
        try:
            root = ET.fromstring(_zip_read(zf, container_name))
        except ET.ParseError:
            root = None
        if root is not None:
            for el in root.iter():
                if _local(el.tag) == "rootfile":
                    path = (el.get("full-path") or "").replace("\\", "/").lstrip("/")
                    if path:
                        return names.get(path, path)
    for candidate in ("content.opf", "OEBPS/content.opf", "OPS/content.opf"):
        if candidate in names:
            return names[candidate]
    for path in names:
        if path.lower().endswith(".opf"):
            return names[path]
    return None


def _opf_dir(opf_path: str) -> str:
    path = opf_path.replace("\\", "/")
    if "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]


def _join_zip(folder: str, rel: str) -> str:
    rel = rel.replace("\\", "/").lstrip("/")
    if not folder:
        return rel
    return f"{folder.rstrip('/')}/{rel}"


def cover_info_from_record(record: dict[str, Any] | None) -> CoverInfo:
    record = record or {}
    fandoms = record.get("fandoms") or []
    if isinstance(fandoms, str):
        fandoms = [fandoms]
    relationships = record.get("relationships") or []
    if isinstance(relationships, str):
        relationships = [relationships]
    cleaned = record.get("cleaned") if isinstance(record.get("cleaned"), dict) else {}
    if not fandoms and isinstance(cleaned.get("fandoms"), list):
        fandoms = cleaned["fandoms"]
    if not relationships and isinstance(cleaned.get("relationships"), list):
        relationships = cleaned["relationships"]
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    words = meta.get("words")
    if not isinstance(words, int):
        raw = words if words not in (None, "") else (
            record.get("wordcount") or record.get("word_count")
        )
        try:
            words = int(str(raw).replace(",", "")) if raw not in (None, "") else None
        except (TypeError, ValueError):
            words = None
    chapters = meta.get("chapters") if isinstance(meta.get("chapters"), dict) else {}
    complete = chapters.get("is_complete")
    if complete is None:
        tags = [str(tag) for tag in (record.get("tags") or [])]
        lowered = {tag.casefold() for tag in tags}
        if "completed" in lowered or "complete" in lowered:
            complete = True
    rating = str(record.get("rating") or "").strip()
    if not rating:
        tags = [str(tag) for tag in (record.get("tags") or [])]
        for tag in tags:
            if tag in _RATING_TAGS:
                rating = tag
                break
    series_name = ""
    series = record.get("series")
    if isinstance(series, list) and series:
        first = series[0] if isinstance(series[0], dict) else {}
        series_name = str(first.get("name") or "").strip()
        position = first.get("position")
        if series_name and position not in (None, ""):
            series_name = f"{series_name} #{position}"
    elif isinstance(series, str):
        series_name = series.strip()
    authors = record.get("author") or record.get("authors")
    if isinstance(authors, (list, tuple)):
        author = ", ".join(str(item).strip() for item in authors if str(item).strip())
    else:
        author = str(authors or "").strip()
    return CoverInfo(
        title=str(record.get("title") or "").strip(),
        author=author,
        fandom=", ".join(str(item).strip() for item in fandoms if str(item).strip()),
        relationship=", ".join(
            str(item).strip() for item in relationships if str(item).strip()
        ),
        series=series_name,
        rating=rating,
        wordcount=words if isinstance(words, int) else None,
        score=_score_from_record(record, meta, words if isinstance(words, int) else None),
        complete=bool(complete) if complete is not None else None,
        work_id=str(record.get("work_id") or "").strip(),
    )


def _first_html_href(opf_root: ET.Element) -> str | None:
    manifest = _find_child(opf_root, "manifest")
    spine = _find_child(opf_root, "spine")
    by_id = {
        item.get("id") or "": item
        for item in (_iter_local(manifest, "item") if manifest is not None else [])
    }

    def _usable(item: ET.Element | None) -> str | None:
        if item is None:
            return None
        media = (item.get("media-type") or "").lower()
        props = (item.get("properties") or "").split()
        if "nav" in props:
            return None
        if media not in {"application/xhtml+xml", "text/html"}:
            return None
        item_id = (item.get("id") or "").lower()
        name = (item.get("href") or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
        if item_id in {"ao3-cover", "cover"} or name == "cover.xhtml":
            return None
        return item.get("href")

    if spine is not None:
        for itemref in _iter_local(spine, "itemref"):
            href = _usable(by_id.get(itemref.get("idref") or ""))
            if href:
                return href
    if manifest is not None:
        for item in _iter_local(manifest, "item"):
            href = _usable(item)
            if href:
                return href
    return None


def _dt_value(html: str, labels: tuple[str, ...]) -> str:
    wanted = {label.casefold().rstrip(":") for label in labels}
    try:
        root = ET.fromstring(html)
    except ET.ParseError:
        wrapped = f"<div xmlns='{XHTML_NS}'>{html}</div>"
        try:
            root = ET.fromstring(wrapped)
        except ET.ParseError:
            return _dt_value_regex(html, labels)
    for parent in root.iter():
        children = list(parent)
        for index, child in enumerate(children):
            if _local(child.tag).lower() != "dt":
                continue
            label = "".join(child.itertext()).strip().rstrip(":").casefold()
            if label not in wanted:
                continue
            for sib in children[index + 1 :]:
                if _local(sib.tag).lower() == "dd":
                    return "".join(sib.itertext()).strip()
            break
    return _dt_value_regex(html, labels)


def _dt_value_regex(html: str, labels: tuple[str, ...]) -> str:
    blob = re.sub(r"\s+", " ", html)
    for label in labels:
        match = re.search(
            rf"<dt[^>]*>\s*{re.escape(label)}:?\s*</dt>\s*<dd[^>]*>(.*?)</dd>",
            blob,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return re.sub(r"<[^>]+>", "", match.group(1)).strip()
    return ""


def _parse_wordcount(html: str) -> int | None:
    raw = _dt_value(html, ("Words", "Word Count", "Word count"))
    if not raw:
        match = re.search(
            r"\bWords:\s*([\d,]+)",
            re.sub(r"\s+", " ", html),
            flags=re.IGNORECASE,
        )
        raw = match.group(1) if match else ""
    try:
        value = int(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _parse_stat(html: str, label: str) -> int | None:
    raw = _dt_value(html, (label,))
    if not raw:
        match = re.search(
            rf"\b{re.escape(label)}:\s*([\d,]+)",
            re.sub(r"\s+", " ", html),
            flags=re.IGNORECASE,
        )
        raw = match.group(1) if match else ""
    try:
        value = int(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _score_from_record(
    record: dict[str, Any],
    meta: dict[str, Any],
    words: int | None,
) -> int | None:
    return resolve_quality_score(
        kudos=meta.get("kudos"),
        hits=meta.get("hits"),
        words=words if words is not None else meta.get("words"),
        quality_score=meta.get("quality_score", record.get("quality_score")),
        quality_score_raw=meta.get("quality_score_raw", record.get("quality_score_raw")),
    )


def _format_score(score: float) -> str:
    if abs(score - round(score)) < 0.05:
        shown = str(int(round(score)))
    else:
        shown = f"{score:.1f}"
    return f"Score {shown}"


def cover_info_from_epub_bytes(data: bytes) -> CoverInfo:
    info = CoverInfo()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise CoverError(f"Not an EPUB (zip) file: {exc}") from exc
    with zf:
        opf_path = find_opf_path(zf)
        if not opf_path:
            return info
        try:
            opf_root = ET.fromstring(_zip_read(zf, opf_path))
        except ET.ParseError:
            return info
        titles = [
            _text(el)
            for el in opf_root.iter()
            if _local(el.tag) == "title" and _text(el)
        ]
        creators = [
            _text(el)
            for el in opf_root.iter()
            if _local(el.tag) == "creator" and _text(el)
        ]
        info.title = titles[0] if titles else ""
        info.author = ", ".join(creators)
        html_href = _first_html_href(opf_root)
        if html_href:
            html_zip = _join_zip(_opf_dir(opf_path), html_href)
            names = _zip_names(zf)
            real = names.get(html_zip) or names.get(html_href)
            if real:
                try:
                    html = _zip_read(zf, real).decode("utf-8", errors="replace")
                except Exception:
                    html = ""
                if html:
                    info.fandom = _dt_value(html, ("Fandom", "Fandoms"))
                    info.relationship = info.relationship or _dt_value(
                        html, ("Relationship", "Relationships")
                    )
                    info.rating = info.rating or _dt_value(html, ("Rating",))
                    series = _dt_value(html, ("Series",))
                    if series:
                        info.series = series
                    words = _parse_wordcount(html)
                    if words is not None:
                        info.wordcount = words
                    info.score = calculate_quality_score(
                        _parse_stat(html, "Kudos"),
                        _parse_stat(html, "Hits"),
                        words,
                    )
    return info


def cover_info_from_epub(path: str | Path) -> CoverInfo:
    return cover_info_from_epub_bytes(Path(path).read_bytes())


def merge_cover_info(*parts: CoverInfo | None) -> CoverInfo:
    merged = CoverInfo()
    for part in parts:
        if part is None:
            continue
        for name in (
            "title",
            "author",
            "fandom",
            "relationship",
            "series",
            "rating",
            "work_id",
        ):
            value = getattr(part, name)
            if value and not getattr(merged, name):
                setattr(merged, name, value)
        if merged.wordcount is None and part.wordcount is not None:
            merged.wordcount = part.wordcount
        if merged.score is None and part.score is not None:
            merged.score = part.score
        if merged.complete is None and part.complete is not None:
            merged.complete = part.complete
    return merged


def parse_color(value: str) -> tuple[int, int, int, int]:
    text = str(value or "").strip().lstrip("#")
    if text.startswith("0x"):
        text = text[2:]
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text) + "ff"
    elif len(text) == 4:
        text = "".join(ch * 2 for ch in text)
    elif len(text) == 6:
        text += "ff"
    elif len(text) != 8:
        raise CoverError(f"Invalid colour {value!r}")
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore[return-value]


def relative_luminance(rgb: tuple[int, ...]) -> float:
    def _chan(channel: int) -> float:
        value = max(0, min(255, int(channel))) / 255.0
        if value <= 0.04045:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * _chan(rgb[0]) + 0.7152 * _chan(rgb[1]) + 0.0722 * _chan(rgb[2])
    )


def contrast_ratio(
    foreground: tuple[int, ...],
    background: tuple[int, ...],
) -> float:
    lighter = max(relative_luminance(foreground), relative_luminance(background))
    darker = min(relative_luminance(foreground), relative_luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def ensure_contrast(
    hex_color: str,
    *,
    against: tuple[int, int, int] = (255, 255, 255),
    min_ratio: float = 3.5,
) -> str:
    """Darken ``hex_color`` until white (or ``against``) text stays readable."""
    red, green, blue, _alpha = parse_color(hex_color)
    target = max(1.0, float(min_ratio))
    for _ in range(48):
        if contrast_ratio((red, green, blue), against) >= target:
            break
        red = max(0, int(red * 0.91))
        green = max(0, int(green * 0.91))
        blue = max(0, int(blue * 0.91))
    return f"#{red:02x}{green:02x}{blue:02x}"


def _u32(n: int) -> int:
    return n & 0xFFFFFFFF


def _i32(n: int) -> int:
    n = _u32(n)
    return n - 0x100000000 if n >= 0x80000000 else n


def _imul(a: int, b: int) -> int:
    return _i32(_i32(a) * _i32(b))


def cyrb128(text: str) -> tuple[int, int, int, int]:
    """JS cyrb128 (bryc / Stack Overflow) used by the original cover tool."""
    h1, h2, h3, h4 = 1779033703, 3144134277, 1013904242, 2773480762
    for ch in text:
        k = ord(ch)
        h1 = h2 ^ _imul(h1 ^ k, 597399067)
        h2 = h3 ^ _imul(h2 ^ k, 2869860233)
        h3 = h4 ^ _imul(h3 ^ k, 951274213)
        h4 = h1 ^ _imul(h4 ^ k, 2716044179)
    h1 = _imul(h3 ^ (_u32(h1) >> 18), 597399067)
    h2 = _imul(h4 ^ (_u32(h2) >> 22), 2869860233)
    h3 = _imul(h1 ^ (_u32(h3) >> 17), 951274213)
    h4 = _imul(h2 ^ (_u32(h4) >> 19), 2716044179)
    h1 ^= h2 ^ h3 ^ h4
    h2 ^= h1
    h3 ^= h1
    h4 ^= h1
    return _u32(h1), _u32(h2), _u32(h3), _u32(h4)


def sfc32(a: int, b: int, c: int, d: int) -> Callable[[], float]:
    state = [_i32(a), _i32(b), _i32(c), _i32(d)]

    def _rand() -> float:
        a_, b_, c_, d_ = state
        t = _i32(_i32(a_ + b_) + d_)
        d_ = _i32(d_ + 1)
        a_ = b_ ^ (_u32(b_) >> 9)
        b_ = _i32(c_ + _i32(c_ << 3))
        c_ = _i32((_u32(c_) << 21) | (_u32(c_) >> 11))
        c_ = _i32(c_ + t)
        state[:] = [a_, b_, c_, d_]
        return _u32(t) / 4294967296

    return _rand


def _hue_to_rgb(p: float, q: float, t: float) -> float:
    if t < 0:
        t += 1
    if t > 1:
        t -= 1
    if t < 1 / 6:
        return p + (q - p) * 6 * t
    if t < 1 / 2:
        return q
    if t < 2 / 3:
        return p + (q - p) * (2 / 3 - t) * 6
    return p


def hsl_to_rgb(h: float, s: float, lightness: float) -> tuple[int, int, int]:
    if s == 0:
        value = int(round(lightness * 255))
        return value, value, value
    q = (
        lightness * (1 + s)
        if lightness < 0.5
        else lightness + s - lightness * s
    )
    p = 2 * lightness - q
    r = _hue_to_rgb(p, q, h + 1 / 3)
    g = _hue_to_rgb(p, q, h)
    b = _hue_to_rgb(p, q, h - 1 / 3)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def _seed_slice(text: str, words: int) -> str:
    parts = str(text or "").split()
    if words <= 0:
        sliced = str(text or "").strip()
    else:
        sliced = " ".join(parts[:words])
    return sliced.rstrip(":")


def _lookup_mapped_color(text: str, mapping: dict[str, str]) -> str | None:
    if not mapping or not text:
        return None
    folded = text.casefold()
    for name, color in mapping.items():
        if name.casefold() == folded:
            return color
    for name, color in mapping.items():
        key = name.casefold()
        if folded.startswith(key) or key.startswith(folded):
            return color
    for name, color in mapping.items():
        if name.casefold() in folded:
            return color
    return None


def choose_colours(
    info: CoverInfo,
    settings: CoverSettings | None = None,
) -> tuple[str, str]:
    """Return (top, bottom) hex colours for the cover gradient."""
    settings = settings or CoverSettings()
    seed = _seed_slice(info.seed_text(settings.color_seed), settings.seed_words)
    mapped = _lookup_mapped_color(info.fandom, settings.fandom_colors)
    if mapped is None:
        mapped = _lookup_mapped_color(seed, settings.fandom_colors)
    if mapped:
        top = mapped
        bottom = mapped
        if settings.gradient:
            r, g, b, a = parse_color(mapped)
            bottom_rgb = tuple(max(0, int(ch * 0.55)) for ch in (r, g, b))
            bottom = f"#{bottom_rgb[0]:02x}{bottom_rgb[1]:02x}{bottom_rgb[2]:02x}"
        return _maybe_auto_contrast(top, bottom, settings)
    if settings.color_mode == "solid":
        color = settings.solid_color or "#2c3e6b"
        return _maybe_auto_contrast(color, color, settings)
    rand = sfc32(*cyrb128(seed or "ao3"))
    if settings.color_mode == "palette" and settings.palette:
        index = int(rand() * len(settings.palette)) % len(settings.palette)
        top = settings.palette[index]
        if settings.gradient and len(settings.palette) > 1:
            bottom = settings.palette[(index + 1) % len(settings.palette)]
        else:
            bottom = top
        return _maybe_auto_contrast(top, bottom, settings)
    hue = rand()
    span = max(0.0, settings.saturation_max - settings.saturation_min)
    saturation = settings.saturation_min + span * rand()
    r1, g1, b1 = hsl_to_rgb(hue, saturation, settings.lightness_top)
    r2, g2, b2 = hsl_to_rgb(hue, saturation, settings.lightness_bottom)
    top, bottom = (
        f"#{r1:02x}{g1:02x}{b1:02x}",
        f"#{r2:02x}{g2:02x}{b2:02x}",
    )
    return _maybe_auto_contrast(top, bottom, settings)


def _maybe_auto_contrast(
    top: str,
    bottom: str,
    settings: CoverSettings,
) -> tuple[str, str]:
    if not settings.auto_contrast:
        return top, bottom
    minimum = float(settings.contrast_min_ratio)
    return (
        ensure_contrast(top, min_ratio=minimum),
        ensure_contrast(bottom, min_ratio=minimum),
    )


def _require_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise CoverError(
            "Cover generation requires Pillow (pip install pillow). "
            "Calibre already includes it."
        ) from exc
    return Image, ImageDraw, ImageFont


def _font_paths_for(name: str) -> list[Path]:
    paths: list[Path] = []
    raw = str(name or "").strip()
    if raw and (("/" in raw or "\\" in raw or raw.lower().endswith((".ttf", ".otf", ".ttc")))):
        paths.append(Path(raw).expanduser())
    return paths


def resolve_font(settings: CoverSettings, size: int):
    _, _, ImageFont = _require_pillow()
    tried: list[Path] = []
    for candidate in (
        *(_font_paths_for(settings.font_path) if settings.font_path else ()),
        *_font_paths_for(settings.font),
        *(Path(item) for item in _FONT_FILES),
    ):
        if candidate in tried:
            continue
        tried.append(candidate)
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    for family in (settings.font, *_FONT_CANDIDATES):
        if not family:
            continue
        try:
            return ImageFont.truetype(family, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_width(draw, font, text: str) -> float:
    if hasattr(draw, "textlength"):
        try:
            return float(draw.textlength(text, font=font))
        except Exception:
            pass
    if hasattr(font, "getlength"):
        try:
            return float(font.getlength(text))
        except Exception:
            pass
    bbox = draw.textbbox((0, 0), text, font=font)
    return float(bbox[2] - bbox[0])


def _wrap_words(
    draw,
    font,
    words: list[str],
    max_width: float,
    separator: str,
) -> tuple[list[str], str]:
    if not words:
        return [""], separator
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = current + separator + word
        if _text_width(draw, font, candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines, separator


def _split_intercaps(name: str) -> list[str]:
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)
    return parts or [name]


def _break_long_token(draw, font, token: str, max_width: float) -> list[str]:
    if not token or _text_width(draw, font, token) <= max_width:
        return [token] if token else [""]
    chunks = re.split(r"([-–—/:|])", token)
    pieces: list[str] = []
    current = ""
    for chunk in chunks:
        if not chunk:
            continue
        trial = current + chunk
        if current and _text_width(draw, font, trial) > max_width:
            pieces.append(current)
            current = chunk
        else:
            current = trial
    if current:
        pieces.append(current)
    broken: list[str] = []
    for piece in pieces or [token]:
        if _text_width(draw, font, piece) <= max_width:
            broken.append(piece)
            continue
        current = ""
        for char in piece:
            trial = current + char
            if current and _text_width(draw, font, trial) > max_width:
                broken.append(current + ("-" if current[-1:] not in "-–—/" else ""))
                current = char
            else:
                current = trial
        if current:
            broken.append(current)
    return broken or [token]


def wrap_author(draw, font, author: str, max_width: float) -> tuple[list[str], str]:
    if " " in author:
        tokens: list[str] = []
        for word in author.split(" "):
            tokens.extend(_break_long_token(draw, font, word, max_width))
        return _wrap_words(draw, font, tokens, max_width, " ")
    if "_" in author:
        return _wrap_words(
            draw,
            font,
            [
                part
                for word in author.split("_")
                for part in _break_long_token(draw, font, word, max_width)
            ],
            max_width,
            "_",
        )
    if re.search(r"[A-Z]", author) and re.search(r"[a-z]", author):
        return _wrap_words(
            draw, font, _split_intercaps(author), max_width, ""
        )
    return _wrap_words(
        draw, font, _break_long_token(draw, font, author, max_width), max_width, ""
    )


def wrap_title(draw, font, title: str, max_width: float) -> list[str]:
    tokens: list[str] = []
    for word in title.split(" ") if title else [""]:
        tokens.extend(_break_long_token(draw, font, word, max_width))
    lines, _sep = _wrap_words(draw, font, tokens, max_width, " ")
    return lines


def _display_line(text: str, separator: str, *, last: bool) -> str:
    if separator == " ":
        return text
    if separator == "_":
        return text + ("_" if not last else "")
    if not last:
        return text + "-"
    return text


def _stroke_for_size(base_px: int, size: int, *, ref: int = 88) -> int:
    if base_px <= 0:
        return 0
    return max(1, int(round(base_px * max(size, 8) / ref)))


def _effect_extra(settings: CoverSettings, size: int) -> float:
    extra = float(_stroke_for_size(settings.text_stroke_px, size))
    if settings.text_shadow:
        extra += 2.0
    return extra


def _font_line_height(font, fallback_size: int, leading: float) -> float:
    try:
        bbox = font.getbbox("Hg")
        cap = float(bbox[3] - bbox[1])
        descent = 0.0
        try:
            _ascent, raw_descent = font.getmetrics()
            descent = max(0.0, float(raw_descent))
        except Exception:
            descent = cap * 0.2
        # Cap-height plus real descenders, without the extra em-square padding
        # that made wrapped titles look double-spaced.
        measured = cap + descent * 0.7
        if measured > 0:
            return max(1.0, measured * leading)
    except Exception:
        pass
    return max(1.0, float(fallback_size) * leading)


def _limit_lines(lines: list[str], max_lines: int) -> list[str]:
    if max_lines > 0 and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip("…") + "…"
    return lines


def _block_height(line_count: int, line_height: float) -> float:
    return max(0.0, line_count * line_height)


@dataclass
class CoverTextBlock:
    kind: str
    lines: list[str]
    separator: str
    size: int
    line_height: float
    y: float
    fill: str
    max_lines: int

    @property
    def bottom(self) -> float:
        return self.y + _block_height(len(self.lines), self.line_height)


def _scratch_draw(width: int, height: int):
    Image, ImageDraw, _ImageFont = _require_pillow()
    image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    return ImageDraw.Draw(image, "RGBA")


def _fit_wrapped(
    draw,
    settings: CoverSettings,
    text: str,
    *,
    start_size: int,
    min_size: int,
    max_width: float,
    max_height: float,
    max_lines: int,
    leading: float,
    wrap,
    auto_fit: bool,
) -> tuple[object, int, list[str], str, float]:
    size = max(int(start_size), int(min_size))
    floor = max(8, int(min_size))
    last: tuple[object, int, list[str], str, float] | None = None
    while size >= floor:
        font = resolve_font(settings, size)
        wrapped = wrap(draw, font, text, max_width)
        if isinstance(wrapped, tuple):
            lines, separator = wrapped
        else:
            lines, separator = wrapped, " "
        line_height = _font_line_height(font, size, leading) + _effect_extra(
            settings, size
        )
        last = (font, size, lines, separator, line_height)
        fits_lines = max_lines <= 0 or len(lines) <= max_lines
        fits_height = _block_height(len(lines), line_height) <= max_height
        if fits_lines and fits_height:
            return last
        if not auto_fit:
            break
        size -= 2
    font, size, lines, separator, line_height = last or (
        resolve_font(settings, floor),
        floor,
        [text],
        " ",
        float(floor) * leading + _effect_extra(settings, floor),
    )
    lines = _limit_lines(lines, max_lines)
    while (
        auto_fit
        and size > floor
        and _block_height(len(lines), line_height) > max_height
    ):
        size -= 2
        font = resolve_font(settings, size)
        wrapped = wrap(draw, font, text, max_width)
        if isinstance(wrapped, tuple):
            lines, separator = wrapped
        else:
            lines, separator = wrapped, " "
        lines = _limit_lines(lines, max_lines)
        line_height = _font_line_height(font, size, leading) + _effect_extra(
            settings, size
        )
    if _block_height(len(lines), line_height) > max_height and line_height > 1:
        allowed = max(1, int(max_height // line_height))
        lines = _limit_lines(lines, min(max_lines or allowed, allowed))
    return font, size, lines, separator, line_height


def plan_cover_layout(
    info: CoverInfo,
    settings: CoverSettings | None = None,
    *,
    draw=None,
) -> tuple[CoverTextBlock | None, CoverTextBlock | None, CoverTextBlock | None, list[CoverTextBlock]]:
    """Return (title, author, footer, headers) with fitted sizes and y positions."""
    settings = settings or CoverSettings()
    width = max(120, int(settings.width))
    height = max(180, int(settings.height))
    if draw is None:
        draw = _scratch_draw(width, height)
    max_width = width * max(0.4, min(0.95, 1 - 2 * settings.padding))
    gap = height * float(settings.block_gap)
    headers: list[CoverTextBlock] = []
    header_y = height * settings.header_y
    header_bits: list[str] = []
    if settings.shows("fandom") and info.fandom:
        header_bits.append(info.fandom)
    if settings.shows("relationship") and info.relationship:
        header_bits.append(info.relationship)
    for block in header_bits:
        font = resolve_font(settings, settings.header_size)
        lines, sep = _wrap_words(draw, font, block.split(" "), max_width, " ")
        lines = _limit_lines(lines, settings.header_max_lines)
        line_height = _font_line_height(
            font, settings.header_size, settings.header_leading
        ) + _effect_extra(settings, settings.header_size)
        headers.append(
            CoverTextBlock(
                kind="header",
                lines=lines,
                separator=sep,
                size=settings.header_size,
                line_height=line_height,
                y=header_y,
                fill=settings.header_color,
                max_lines=settings.header_max_lines,
            )
        )
        header_y += _block_height(len(lines), line_height) + 8

    footer_block: CoverTextBlock | None = None
    footer_parts = _format_footer(info, settings)
    footer_top = height * settings.footer_y
    if footer_parts:
        font = resolve_font(settings, settings.footer_size)
        block = " · ".join(footer_parts)
        lines, sep = _wrap_words(draw, font, block.split(" "), max_width, " ")
        lines = _limit_lines(lines, 3)
        line_height = _font_line_height(
            font, settings.footer_size, settings.footer_leading
        ) + _effect_extra(settings, settings.footer_size)
        shown = len(lines)
        footer_top = height * settings.footer_y - _block_height(shown, line_height)
        footer_block = CoverTextBlock(
            kind="footer",
            lines=lines,
            separator=sep,
            size=settings.footer_size,
            line_height=line_height,
            y=footer_top,
            fill=settings.footer_color,
            max_lines=3,
        )

    author_block: CoverTextBlock | None = None
    author_top = height * settings.author_y
    if settings.shows("author") and info.author:
        author_ceiling = footer_top - gap if footer_block else height * 0.96
        max_author_height = max(settings.min_author_size, author_ceiling - author_top)
        _font, size, lines, sep, line_height = _fit_wrapped(
            draw,
            settings,
            info.author,
            start_size=settings.author_size,
            min_size=settings.min_author_size,
            max_width=max_width,
            max_height=max_author_height,
            max_lines=settings.author_max_lines,
            leading=settings.author_leading,
            wrap=wrap_author,
            auto_fit=True,
        )
        author_block = CoverTextBlock(
            kind="author",
            lines=lines,
            separator=sep,
            size=size,
            line_height=line_height,
            y=author_top,
            fill=settings.author_color,
            max_lines=settings.author_max_lines,
        )

    title_block: CoverTextBlock | None = None
    if settings.shows("title") and info.title:
        title = info.title.upper() if settings.uppercase_title else info.title
        title_top = max(height * settings.title_y, header_y + gap if headers else 0)
        title_floor = (
            author_block.y - gap
            if author_block is not None
            else (footer_top - gap if footer_block else height * 0.78)
        )
        max_title_height = max(settings.min_title_size, title_floor - title_top)
        _font, size, lines, sep, line_height = _fit_wrapped(
            draw,
            settings,
            title,
            start_size=settings.title_size,
            min_size=settings.min_title_size,
            max_width=max_width,
            max_height=max_title_height,
            max_lines=settings.title_max_lines,
            leading=settings.title_leading,
            wrap=wrap_title,
            auto_fit=bool(settings.auto_fit_title),
        )
        title_block = CoverTextBlock(
            kind="title",
            lines=lines,
            separator=sep,
            size=size,
            line_height=line_height,
            y=title_top,
            fill=settings.title_color,
            max_lines=settings.title_max_lines,
        )
    return title_block, author_block, footer_block, headers


def _draw_text_line(
    draw,
    font,
    text: str,
    *,
    xy: tuple[float, float],
    fill: tuple[int, int, int, int],
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int, int] | None = None,
    anchor: str = "ma",
) -> None:
    kwargs: dict[str, Any] = {"font": font, "fill": fill}
    if stroke_width > 0 and stroke_fill is not None:
        kwargs["stroke_width"] = int(stroke_width)
        kwargs["stroke_fill"] = stroke_fill
    try:
        draw.text(xy, text, anchor=anchor, **kwargs)
        return
    except TypeError:
        kwargs.pop("stroke_width", None)
        kwargs.pop("stroke_fill", None)
        try:
            draw.text(xy, text, anchor=anchor, **kwargs)
            return
        except TypeError:
            pass
    tw = _text_width(draw, font, text)
    draw.text((xy[0] - tw / 2, xy[1]), text, **kwargs)


def _draw_lines(
    draw,
    font,
    lines: list[str],
    *,
    width: int,
    y: float,
    line_height: float,
    fill: tuple[int, int, int, int],
    max_lines: int,
    separator: str = " ",
    shadow: bool = False,
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int, int] | None = None,
) -> None:
    lines = _limit_lines(list(lines), max_lines)
    x = width / 2
    shadow_fill = (0, 0, 0, 170)
    offset = max(2, int(stroke_width) + 1) if stroke_width else 2
    for index, line in enumerate(lines):
        shown = _display_line(line, separator, last=index == len(lines) - 1)
        top = y + index * line_height
        if shadow:
            _draw_text_line(
                draw,
                font,
                shown,
                xy=(x + offset, top + offset),
                fill=shadow_fill,
                stroke_width=stroke_width,
                stroke_fill=shadow_fill,
            )
        _draw_text_line(
            draw,
            font,
            shown,
            xy=(x, top),
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )


def _format_footer(info: CoverInfo, settings: CoverSettings) -> list[str]:
    parts: list[str] = []
    if settings.shows("series") and info.series:
        parts.append(info.series)
    if settings.shows("rating") and info.rating:
        parts.append(info.rating)
    if settings.shows("wordcount") and info.wordcount is not None:
        parts.append(f"{info.wordcount:,} words")
    if settings.shows("score") and info.score is not None:
        parts.append(_format_score(info.score))
    if settings.shows("complete") and info.complete is not None:
        parts.append("Complete" if info.complete else "WIP")
    return parts


def _apply_scrim(Image, image, amount: float):
    if amount <= 0:
        return image
    width, height = image.size
    amount = min(max(float(amount), 0.0), 0.8)
    band = Image.new("L", (1, height))
    for row in range(height):
        t = row / max(1, height - 1)
        edge = min(t / 0.14, (1.0 - t) / 0.12, 1.0)
        band.putpixel((0, row), int(255 * amount * (0.4 + 0.6 * edge)))
    alpha = band.resize((width, height))
    black = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    black.putalpha(alpha)
    return Image.alpha_composite(image.convert("RGBA"), black)


def render_cover_image(
    info: CoverInfo,
    settings: CoverSettings | None = None,
) -> bytes:
    """Return PNG or JPEG bytes for ``info``."""
    Image, ImageDraw, _ImageFont = _require_pillow()
    settings = settings or CoverSettings()
    width = max(120, int(settings.width))
    height = max(180, int(settings.height))
    top_hex, bottom_hex = choose_colours(info, settings)
    top = parse_color(top_hex)
    bottom = parse_color(bottom_hex)
    if settings.gradient and top[:3] != bottom[:3]:
        grad = Image.new("RGB", (1, 2))
        grad.putpixel((0, 0), top[:3])
        grad.putpixel((0, 1), bottom[:3])
        resample = getattr(getattr(Image, "Resampling", Image), "BILINEAR", Image.BILINEAR)
        base = grad.resize((width, height), resample)
        image = base.convert("RGBA")
    else:
        image = Image.new("RGBA", (width, height), top)
    image = _apply_scrim(Image, image, settings.scrim)
    draw = ImageDraw.Draw(image, "RGBA")
    if settings.border_px > 0:
        inset = max(1, int(settings.border_px))
        draw.rectangle(
            [inset, inset, width - inset - 1, height - inset - 1],
            outline=parse_color(settings.border_color),
            width=inset,
        )
    title_block, author_block, footer_block, headers = plan_cover_layout(
        info, settings, draw=draw
    )
    stroke_color = parse_color(settings.text_stroke_color)
    shadow = bool(settings.text_shadow)

    def _paint(block: CoverTextBlock | None) -> None:
        if block is None or not block.lines:
            return
        font = resolve_font(settings, block.size)
        _draw_lines(
            draw,
            font,
            block.lines,
            width=width,
            y=block.y,
            line_height=block.line_height,
            fill=parse_color(block.fill),
            max_lines=block.max_lines,
            separator=block.separator,
            shadow=shadow,
            stroke_width=_stroke_for_size(settings.text_stroke_px, block.size),
            stroke_fill=stroke_color,
        )

    for header in headers:
        _paint(header)
    _paint(title_block)
    _paint(author_block)
    _paint(footer_block)

    buf = io.BytesIO()
    if settings.image_format == "jpeg":
        image.convert("RGB").save(
            buf, format="JPEG", quality=max(40, min(100, int(settings.jpeg_quality)))
        )
    else:
        image.save(buf, format="PNG")
    return buf.getvalue()


def cover_media_type(settings: CoverSettings) -> str:
    return "image/jpeg" if settings.image_format == "jpeg" else "image/png"


def existing_cover_item(opf_root: ET.Element) -> tuple[ET.Element | None, str]:
    """Return (manifest item, id) for an existing cover image, if any."""
    cover_id = ""
    metadata = _find_child(opf_root, "metadata")
    if metadata is not None:
        for meta in _iter_local(metadata, "meta"):
            if (meta.get("name") or "").lower() == "cover":
                cover_id = (meta.get("content") or "").strip()
                break
    manifest = _find_child(opf_root, "manifest")
    if manifest is None:
        return None, cover_id
    if cover_id:
        for item in _iter_local(manifest, "item"):
            if item.get("id") == cover_id:
                return item, cover_id
    for item in _iter_local(manifest, "item"):
        props = (item.get("properties") or "").split()
        if "cover-image" in props:
            return item, item.get("id") or "cover-image"
    return None, cover_id or "cover-image"


def epub_has_cover(path: str | Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            opf_path = find_opf_path(zf)
            if not opf_path:
                return False
            root = ET.fromstring(_zip_read(zf, opf_path))
    except (OSError, zipfile.BadZipFile, ET.ParseError):
        return False
    item, _cover_id = existing_cover_item(root)
    return item is not None


def extract_cover_bytes(path: str | Path) -> bytes | None:
    try:
        with zipfile.ZipFile(path) as zf:
            opf_path = find_opf_path(zf)
            if not opf_path:
                return None
            root = ET.fromstring(_zip_read(zf, opf_path))
            item, _cover_id = existing_cover_item(root)
            if item is None:
                return None
            href = (item.get("href") or "").strip()
            if not href:
                return None
            zip_name = _join_zip(_opf_dir(opf_path), href)
            names = _zip_names(zf)
            real = names.get(zip_name) or names.get(href)
            if not real:
                return None
            return _zip_read(zf, real)
    except (OSError, zipfile.BadZipFile, ET.ParseError):
        return None


def _ensure_cover_refs(
    opf_root: ET.Element,
    *,
    href: str,
    media_type: str,
    cover_id: str = "cover-image",
) -> None:
    ns = opf_root.tag.split("}")[0][1:] if opf_root.tag.startswith("{") else OPF_NS
    metadata = _find_child(opf_root, "metadata")
    if metadata is None:
        metadata = ET.SubElement(opf_root, f"{{{ns}}}metadata")
    has_meta = False
    for meta in _iter_local(metadata, "meta"):
        if (meta.get("name") or "").lower() == "cover":
            meta.set("content", cover_id)
            has_meta = True
            break
    if not has_meta:
        meta = ET.Element(f"{{{ns}}}meta")
        meta.set("name", "cover")
        meta.set("content", cover_id)
        metadata.insert(0, meta)
    manifest = _find_child(opf_root, "manifest")
    if manifest is None:
        manifest = ET.SubElement(opf_root, f"{{{ns}}}manifest")
    item, found_id = existing_cover_item(opf_root)
    if found_id:
        cover_id = found_id
    if item is None:
        item = ET.Element(f"{{{ns}}}item")
        manifest.insert(0, item)
    item.set("id", cover_id)
    item.set("href", href)
    item.set("media-type", media_type)
    props = [part for part in (item.get("properties") or "").split() if part]
    if "cover-image" not in props:
        props.append("cover-image")
    item.set("properties", " ".join(props))


COVER_PAGE_ID = "ao3-cover"
COVER_PAGE_HREF = "cover.xhtml"


def _cover_page_bytes(image_href: str) -> bytes:
    src = html.escape(image_href, quote=True)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">\n'
        "<head><title>Cover</title>\n"
        "<style type=\"text/css\">\n"
        "html, body { margin: 0; padding: 0; text-align: center; background: #000; }\n"
        "img { max-width: 100%; height: auto; }\n"
        "</style>\n"
        "</head>\n"
        f'<body><div><img src="{src}" alt="Cover"/></div></body>\n'
        "</html>\n"
    ).encode("utf-8")


def _ensure_cover_page(
    opf_root: ET.Element,
    *,
    page_href: str = COVER_PAGE_HREF,
    page_id: str = COVER_PAGE_ID,
) -> str:
    """Add a cover XHTML document as the first spine item. Returns the href."""
    ns = opf_root.tag.split("}")[0][1:] if opf_root.tag.startswith("{") else OPF_NS
    manifest = _find_child(opf_root, "manifest")
    if manifest is None:
        manifest = ET.SubElement(opf_root, f"{{{ns}}}manifest")
    page_item = None
    for item in _iter_local(manifest, "item"):
        href = (item.get("href") or "").replace("\\", "/")
        name = href.rsplit("/", 1)[-1]
        if item.get("id") == page_id or name == Path(page_href).name:
            page_item = item
            page_id = item.get("id") or page_id
            page_href = item.get("href") or page_href
            break
    if page_item is None:
        page_item = ET.Element(f"{{{ns}}}item")
        manifest.insert(0, page_item)
    page_item.set("id", page_id)
    page_item.set("href", page_href)
    page_item.set("media-type", "application/xhtml+xml")
    spine = _find_child(opf_root, "spine")
    if spine is not None:
        existing = None
        for itemref in list(_iter_local(spine, "itemref")):
            if itemref.get("idref") == page_id:
                existing = itemref
                break
        if existing is None:
            existing = ET.Element(f"{{{ns}}}itemref")
            existing.set("idref", page_id)
        else:
            spine.remove(existing)
        spine.insert(0, existing)
    guide = _find_child(opf_root, "guide")
    if guide is None:
        guide = ET.SubElement(opf_root, f"{{{ns}}}guide")
    has_ref = False
    for ref in _iter_local(guide, "reference"):
        if (ref.get("type") or "").lower() == "cover":
            ref.set("href", page_href)
            has_ref = True
            break
    if not has_ref:
        ref = ET.SubElement(guide, f"{{{ns}}}reference")
        ref.set("type", "cover")
        ref.set("title", "Cover")
        ref.set("href", page_href)
    return page_href


def _write_epub(
    dest: Path,
    *,
    names_in_order: list[str],
    contents: dict[str, bytes],
    compress: set[str],
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".cover-tmp")
    with zipfile.ZipFile(tmp, "w") as zf:
        if "mimetype" in contents:
            zf.writestr(
                "mimetype",
                contents["mimetype"],
                compress_type=zipfile.ZIP_STORED,
            )
        for name in names_in_order:
            if name == "mimetype":
                continue
            data = contents.get(name)
            if data is None:
                continue
            zf.writestr(
                name,
                data,
                compress_type=(
                    zipfile.ZIP_STORED
                    if name not in compress
                    else zipfile.ZIP_DEFLATED
                ),
            )
    tmp.replace(dest)


def inject_cover(
    epub_path: str | Path,
    image: bytes,
    settings: CoverSettings | None = None,
    *,
    dest: str | Path | None = None,
) -> Path:
    """Insert ``image`` as the EPUB cover. Returns the written path."""
    settings = settings or CoverSettings()
    src = Path(epub_path)
    out = Path(dest) if dest is not None else src
    try:
        zf = zipfile.ZipFile(src)
    except zipfile.BadZipFile as exc:
        raise CoverError(f"{src}: not an EPUB") from exc
    with zf:
        opf_path = find_opf_path(zf)
        if not opf_path:
            raise CoverError(f"{src}: no OPF (not a valid EPUB)")
        opf_xml = _zip_read(zf, opf_path)
        try:
            opf_root = ET.fromstring(opf_xml)
        except ET.ParseError as exc:
            raise CoverError(f"{src}: could not parse OPF") from exc
        item, cover_id = existing_cover_item(opf_root)
        if item is not None and not settings.replace_existing:
            return src
        ext = "jpg" if settings.image_format == "jpeg" else "png"
        href = settings.cover_href or f"media/cover.{ext}"
        if "." not in Path(href).name:
            href = f"{href}.{ext}"
        elif item is not None and item.get("href"):
            href = item.get("href") or href
        zip_cover = _join_zip(_opf_dir(opf_path), href)
        _ensure_cover_refs(
            opf_root,
            href=href,
            media_type=cover_media_type(settings),
            cover_id=cover_id or "cover-image",
        )
        page_href = _ensure_cover_page(opf_root)
        zip_page = _join_zip(_opf_dir(opf_path), page_href)
        ET.register_namespace("", OPF_NS)
        ET.register_namespace("dc", DC_NS)
        ET.register_namespace("opf", OPF_NS)
        new_opf = ET.tostring(opf_root, encoding="utf-8", xml_declaration=True)
        contents: dict[str, bytes] = {}
        order: list[str] = []
        compress: set[str] = set()
        skip = {zip_cover, zip_page}
        if item is not None and item.get("href"):
            skip.add(_join_zip(_opf_dir(opf_path), item.get("href") or ""))
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name in skip or name == opf_path:
                continue
            order.append(name)
            contents[name] = zf.read(info.filename)
            if info.compress_type != zipfile.ZIP_STORED:
                compress.add(name)
        contents[opf_path] = new_opf
        if opf_path not in order:
            order.append(opf_path)
        compress.add(opf_path)
        contents[zip_cover] = image
        if zip_cover not in order:
            order.append(zip_cover)
        compress.add(zip_cover)
        contents[zip_page] = _cover_page_bytes(href)
        if zip_page not in order:
            order.append(zip_page)
        compress.add(zip_page)
        if "mimetype" not in contents:
            contents["mimetype"] = b"application/epub+zip"
            order.insert(0, "mimetype")
    _write_epub(out, names_in_order=order, contents=contents, compress=compress)
    return out


def apply_cover_to_epub(
    epub_path: str | Path,
    *,
    record: dict[str, Any] | None = None,
    info: CoverInfo | None = None,
    settings: CoverSettings | None = None,
    dest: str | Path | None = None,
    png_path: str | Path | None = None,
) -> CoverOutcome:
    settings = settings or CoverSettings()
    src = Path(epub_path)
    try:
        from_epub = cover_info_from_epub(src)
    except CoverError as exc:
        return CoverOutcome(path=src, status="failed", error=str(exc))
    except OSError as exc:
        return CoverOutcome(path=src, status="failed", error=str(exc))
    merged = merge_cover_info(info, cover_info_from_record(record), from_epub)
    if epub_has_cover(src) and not settings.replace_existing:
        if png_path:
            existing = extract_cover_bytes(src)
            if existing:
                Path(png_path).write_bytes(existing)
        return CoverOutcome(path=src, status="skipped", info=merged)
    try:
        image = render_cover_image(merged, settings)
    except CoverError as exc:
        return CoverOutcome(path=src, status="failed", error=str(exc), info=merged)
    try:
        written = inject_cover(src, image, settings, dest=dest)
    except CoverError as exc:
        return CoverOutcome(path=src, status="failed", error=str(exc), info=merged)
    png = None
    if png_path:
        png = Path(png_path)
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(image)
    return CoverOutcome(path=written, status="updated", png_path=png, info=merged)


def apply_cover_from_info(
    info: CoverInfo,
    *,
    settings: CoverSettings | None = None,
    epub_path: str | Path | None = None,
    dest: str | Path | None = None,
    png_path: str | Path | None = None,
) -> CoverOutcome:
    settings = settings or CoverSettings()
    try:
        image = render_cover_image(info, settings)
    except CoverError as exc:
        return CoverOutcome(path=None, status="failed", error=str(exc), info=info)
    png = None
    if png_path:
        png = Path(png_path)
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(image)
    if epub_path:
        try:
            written = inject_cover(epub_path, image, settings, dest=dest)
        except CoverError as exc:
            return CoverOutcome(
                path=Path(epub_path),
                status="failed",
                error=str(exc),
                png_path=png,
                info=info,
            )
        return CoverOutcome(path=written, status="updated", png_path=png, info=info)
    return CoverOutcome(path=None, status="png" if png else "updated", png_path=png, info=info)


def iter_epubs(folder: str | Path) -> list[Path]:
    root = Path(folder)
    return sorted(root.glob("*.epub")) + sorted(root.glob("epubs/*.epub"))


def _png_name(info: CoverInfo, fallback: str) -> str:
    stem = info.work_id or re.sub(r"[^\w.-]+", "_", fallback).strip("._") or "cover"
    return f"{stem}.png"


def apply_cover_to_record(
    record: dict[str, Any],
    *,
    bundle: str | Path,
    settings: CoverSettings | None = None,
    png_dir: str | Path | None = None,
) -> CoverOutcome:
    from ao3kit.epubs import epub_relpath, work_id_for_record

    settings = settings or CoverSettings()
    info = cover_info_from_record(record)
    root = Path(bundle)
    epub = None
    rel = str(record.get("epub_file") or "").strip()
    if rel:
        candidate = Path(rel) if Path(rel).is_absolute() else root / rel
        if candidate.is_file():
            epub = candidate
    if epub is None:
        try:
            work_id = work_id_for_record(record)
        except Exception:
            work_id = info.work_id
        if work_id:
            candidate = root / epub_relpath(work_id)
            if candidate.is_file():
                epub = candidate
    png_path = None
    if png_dir is not None:
        book_id = record.get("calibre_book_id")
        fallback = f"book-{book_id}" if book_id not in (None, "") else (
            Path(epub).stem if epub else "cover"
        )
        png_path = Path(png_dir) / _png_name(info, str(fallback))
    if epub is None:
        if png_path is None:
            return CoverOutcome(path=None, status="skipped", error="no EPUB", info=info)
        return apply_cover_from_info(info, settings=settings, png_path=png_path)
    return apply_cover_to_epub(
        epub, record=record, info=info, settings=settings, png_path=png_path
    )


def maybe_stamp_downloaded_epub(
    dest: Path,
    record: dict[str, Any],
    *,
    cover: bool | None = None,
    settings: CoverSettings | None = None,
) -> str | None:
    """Stamp a cover onto a just-downloaded EPUB. Returns an error or None."""
    resolved = settings
    if cover is False:
        return None
    if resolved is None:
        resolved = load_cover_settings()
    if cover is None and not resolved.enabled:
        return None
    if not dest.is_file():
        return None
    try:
        outcome = apply_cover_to_epub(dest, record=record, settings=resolved)
    except CoverError as exc:
        return str(exc)
    if outcome.status == "failed":
        return outcome.error
    return None


def _settings_from_args(args: argparse.Namespace) -> CoverSettings:
    base = load_cover_settings()
    if getattr(args, "settings_json", None):
        try:
            parsed = json.loads(args.settings_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--settings-json: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("--settings-json must be a JSON object")
        base = merge_cover_settings(base, **parsed)
    changes: dict[str, Any] = {
        "replace_existing": args.replace,
        "width": args.width,
        "height": args.height,
        "font": args.font,
        "font_path": args.font_path,
        "color_seed": args.color_seed,
        "color_mode": args.color_mode,
        "solid_color": args.color,
        "gradient": args.gradient,
        "image_format": args.format,
        "uppercase_title": args.uppercase_title,
        "text_shadow": args.text_shadow,
    }
    if args.fields:
        changes["fields"] = args.fields
    if args.palette:
        changes["palette"] = args.palette
        if not args.color_mode:
            changes["color_mode"] = "palette"
    if args.fandom_color:
        mapping = dict(base.fandom_colors)
        mapping.update(parse_color_map("\n".join(args.fandom_color)))
        changes["fandom_colors"] = mapping
    return merge_cover_settings(base, **changes)


def _print_outcome(outcome: CoverOutcome, *, verbose: bool) -> None:
    if not verbose and outcome.status != "failed":
        return
    path = outcome.path or outcome.png_path or ""
    if outcome.status == "failed":
        print(f"cover failed {path}: {outcome.error}", file=sys.stderr)
        return
    print(f"cover {outcome.status} {path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate AO3-style covers (title/author on a fandom-coloured "
            "field) and stamp them into EPUB files."
        )
    )
    parser.add_argument("epubs", nargs="*", type=Path, help="EPUB files to stamp")
    parser.add_argument("--dir", type=Path, help="Folder of EPUBs (and optional epubs/)")
    parser.add_argument("--jsonl", type=Path, help="Work records; uses epub_file when set")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output EPUB (single file) or PNG with --preview",
    )
    parser.add_argument(
        "--png-dir",
        type=Path,
        help="Also write cover PNGs here (named by work id)",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Write a cover image only (no EPUB). Needs --output or --png-dir",
    )
    parser.add_argument(
        "--settings-json",
        default=None,
        help="JSON object of CoverSettings overrides (plugin preview / scripting)",
    )
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--fandom", default="")
    parser.add_argument("--relationship", default="")
    parser.add_argument("--series", default="")
    parser.add_argument("--rating", default="")
    parser.add_argument("--wordcount", type=int, default=None)
    parser.add_argument("--score", type=float, default=None)
    parser.add_argument(
        "--complete",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument(
        "--replace",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Replace a cover already in the EPUB (default: config)",
    )
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--font", default=None)
    parser.add_argument("--font-path", default=None)
    parser.add_argument(
        "--fields",
        default=None,
        help="Comma-separated: " + ", ".join(COVER_FIELDS),
    )
    parser.add_argument(
        "--color-seed",
        choices=COVER_COLOR_SEEDS,
        default=None,
    )
    parser.add_argument(
        "--color-mode",
        choices=COVER_COLOR_MODES,
        default=None,
    )
    parser.add_argument("--color", default=None, help="Solid colour (#rrggbb)")
    parser.add_argument("--palette", default=None, help="Comma-separated #hex colours")
    parser.add_argument(
        "--fandom-color",
        action="append",
        default=[],
        help="Override: Fandom=#hex (repeatable)",
    )
    parser.add_argument(
        "--gradient",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument(
        "--format",
        choices=("png", "jpeg", "jpg"),
        default=None,
    )
    parser.add_argument(
        "--uppercase-title",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument(
        "--text-shadow",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--verbose", action="store_true")
    # Plugin jobs used to append AO3 login to every step. Cover never
    # contacts AO3; accept and ignore so retries of those jobs still run.
    parser.add_argument("--username", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--password", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        settings = _settings_from_args(args)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))

    cli_info = CoverInfo(
        title=args.title,
        author=args.author,
        fandom=args.fandom,
        relationship=args.relationship,
        series=args.series,
        rating=args.rating,
        wordcount=args.wordcount,
        score=args.score,
        complete=args.complete,
    )

    if args.preview:
        dest = args.output
        if dest is None and args.png_dir is not None:
            dest = Path(args.png_dir) / "cover.png"
        if dest is None:
            parser.error("--preview requires -o/--output or --png-dir")
        info = cli_info
        if args.epubs:
            info = merge_cover_info(cli_info, cover_info_from_epub(args.epubs[0]))
        elif args.jsonl:
            records = _load_jsonl_records(args.jsonl)
            if records:
                info = merge_cover_info(cli_info, cover_info_from_record(records[0]))
        if not info.title:
            parser.error("--preview needs --title (or an EPUB / JSONL with a title)")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(render_cover_image(info, settings))
        except CoverError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.verbose:
            print(f"wrote {dest}", file=sys.stderr)
        return 0

    paths: list[Path] = list(args.epubs)
    if args.dir:
        paths.extend(iter_epubs(args.dir))
    records: list[dict[str, Any]] = []
    if args.jsonl:
        records = _load_jsonl_records(args.jsonl)

    failed = 0
    updated = 0
    if records:
        bundle = args.dir or args.jsonl.parent
        for record in records:
            outcome = apply_cover_to_record(
                record,
                bundle=bundle,
                settings=settings,
                png_dir=args.png_dir,
            )
            _print_outcome(outcome, verbose=args.verbose)
            if outcome.status == "failed":
                failed += 1
            elif outcome.status == "updated":
                updated += 1
    elif paths:
        if args.output and len(paths) != 1:
            parser.error("-o/--output only works with a single EPUB")
        for path in paths:
            png = None
            if args.png_dir is not None:
                png = args.png_dir / (path.stem + ".png")
            info = cli_info if cli_info.title or cli_info.author else None
            outcome = apply_cover_to_epub(
                path,
                info=info,
                settings=settings,
                dest=args.output,
                png_path=png,
            )
            _print_outcome(outcome, verbose=args.verbose)
            if outcome.status == "failed":
                failed += 1
            elif outcome.status == "updated":
                updated += 1
    else:
        parser.error("Provide EPUB paths, --dir, --jsonl, or --preview")

    print(
        f"Covers: {updated} updated"
        + (f", {failed} failed" if failed else ""),
        file=sys.stderr,
    )
    return 1 if failed and not updated else 0


if __name__ == "__main__":
    raise SystemExit(main())
