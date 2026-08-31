"""Combine, append, shrink, reorder, rebuild, and explode multi-work EPUBs.

Designed for AO3/Calibre EPUB2 (content.opf + toc.ncx) and generic EPUB3.
Member resources live under stable ``m/<member_id>/…`` prefixes so append/reorder
keep reader notes; rebuild rewrites prefixes on purpose.
"""

from __future__ import annotations

import json
import posixpath
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

OMNIBUS_DIR = "META-INF/fanfic-organizer"
OMNIBUS_META = f"{OMNIBUS_DIR}/omnibus.json"
OMNIBUS_MEMBERS = f"{OMNIBUS_DIR}/members.jsonl"

CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"

ET.register_namespace("", OPF_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("opf", OPF_NS)

_PREFACE_LABELS = frozenset(
    {
        "preface",
        "afterword",
        "title page",
        "titlepage",
        "cover",
        "contents",
        "table of contents",
        "toc",
    }
)

_HREF_ATTRS = ("href", "src", "xlink:href")
_CSS_URL_RE = re.compile(r"url\(\s*([\"']?)([^)\"']+)\1\s*\)", re.I)


@dataclass
class TocEntry:
    label: str
    href: str  # relative to OPF dir within member, may include #frag
    children: list[TocEntry] = field(default_factory=list)


@dataclass
class ParsedEpub:
    path: Path
    opf_path: str  # zip path to OPF
    opf_dir: str  # directory containing OPF ('' or 'OEBPS/')
    manifest: dict[str, dict[str, str]]  # id -> {href, media-type, properties?}
    spine: list[str]  # manifest ids in order
    toc: list[TocEntry]
    title: str
    files: dict[str, bytes]  # zip path -> bytes (all non-mimetype)


@dataclass
class MemberSpec:
    member_id: str
    title: str
    epub_path: Path | None = None
    record: dict[str, Any] = field(default_factory=dict)
    active: bool = True


def member_prefix(member_id: str) -> str:
    safe = re.sub(r"[^\w.-]+", "_", str(member_id).strip()) or "member"
    return f"m/{safe}"


def read_omnibus_meta(epub: str | Path | zipfile.ZipFile) -> dict[str, Any] | None:
    data = _read_zip_bytes(epub, OMNIBUS_META)
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def read_omnibus_members(epub: str | Path | zipfile.ZipFile) -> list[dict[str, Any]]:
    data = _read_zip_bytes(epub, OMNIBUS_MEMBERS)
    if not data:
        return []
    out: list[dict[str, Any]] = []
    for line in data.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def write_omnibus_sidecar(
    files: dict[str, bytes],
    meta: dict[str, Any],
    members: Sequence[dict[str, Any]],
) -> None:
    files[OMNIBUS_META] = (json.dumps(meta, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    lines = [json.dumps(m, ensure_ascii=False, separators=(",", ":")) for m in members]
    files[OMNIBUS_MEMBERS] = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def parse_epub(path: str | Path) -> ParsedEpub:
    path = Path(path)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        container = zf.read("META-INF/container.xml")
        root = ET.fromstring(container)
        rootfile = root.find(f".//{{{CONTAINER_NS}}}rootfile")
        if rootfile is None:
            rootfile = root.find(".//{*}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError(f"{path}: missing OPF in container.xml")
        opf_path = rootfile.get("full-path", "").lstrip("/")
        opf_dir = posixpath.dirname(opf_path)
        if opf_dir and not opf_dir.endswith("/"):
            opf_dir += "/"
        opf_xml = zf.read(opf_path)
        package = ET.fromstring(opf_xml)
        manifest: dict[str, dict[str, str]] = {}
        for item in package.findall(f".//{{{OPF_NS}}}manifest/{{{OPF_NS}}}item"):
            item_id = item.get("id")
            href = item.get("href")
            if not item_id or not href:
                continue
            entry = {"href": href, "media-type": item.get("media-type") or ""}
            props = item.get("properties")
            if props:
                entry["properties"] = props
            manifest[item_id] = entry
        # bare tags (some EPUB2 use default ns oddly)
        if not manifest:
            for item in package.findall(".//{*}manifest/{*}item"):
                item_id = item.get("id")
                href = item.get("href")
                if item_id and href:
                    manifest[item_id] = {
                        "href": href,
                        "media-type": item.get("media-type") or "",
                    }
        spine: list[str] = []
        for ref in package.findall(f".//{{{OPF_NS}}}spine/{{{OPF_NS}}}itemref"):
            idref = ref.get("idref")
            if idref:
                spine.append(idref)
        if not spine:
            for ref in package.findall(".//{*}spine/{*}itemref"):
                idref = ref.get("idref")
                if idref:
                    spine.append(idref)
        title = _dc_text(package, "title") or path.stem
        toc = _parse_toc(zf, package, manifest, opf_dir, opf_path)
        files: dict[str, bytes] = {}
        for name in names:
            if name.endswith("/") or name == "mimetype":
                continue
            files[name] = zf.read(name)
    return ParsedEpub(
        path=path,
        opf_path=opf_path,
        opf_dir=opf_dir,
        manifest=manifest,
        spine=spine,
        toc=toc,
        title=title,
        files=files,
    )


def is_preface_label(label: str) -> bool:
    text = re.sub(r"\s+", " ", str(label or "").strip()).casefold()
    if text in _PREFACE_LABELS:
        return True
    return text.startswith("preface") or text.startswith("afterword")


def filter_toc_prefaces(entries: list[TocEntry], *, skip: bool) -> list[TocEntry]:
    if not skip:
        return entries
    return [e for e in entries if not is_preface_label(e.label)]


def merge_epubs(
    members: Sequence[MemberSpec],
    dest: str | Path,
    *,
    omnibus_id: str | None = None,
    kind: str = "selected",
    title: str | None = None,
    series_id: str = "",
    collection: str = "",
    auto_update: bool = False,
    skip_prefaces_after_first: bool = True,
    language: str = "en",
) -> Path:
    """Create a new omnibus EPUB from member EPUBs."""
    dest = Path(dest)
    if len(members) < 1:
        raise ValueError("need at least one member")
    oid = omnibus_id or str(uuid.uuid4())
    files: dict[str, bytes] = {}
    manifest_items: list[tuple[str, str, str, str]] = []  # id, href, mt, props
    spine_ids: list[str] = []
    toc_roots: list[TocEntry] = []
    member_meta: list[dict[str, Any]] = []
    prefixes: dict[str, str] = {}

    for index, member in enumerate(members):
        if member.epub_path is None:
            raise ValueError(f"member {member.member_id} missing epub_path")
        parsed = parse_epub(member.epub_path)
        prefix = member_prefix(member.member_id)
        prefixes[member.member_id] = prefix
        id_map = _copy_member_into(
            files,
            parsed,
            prefix=prefix,
            id_prefix=f"m{index}_",
            manifest_items=manifest_items,
            spine_ids=spine_ids,
        )
        toc = list(parsed.toc)
        if index > 0 and skip_prefaces_after_first:
            toc = filter_toc_prefaces(toc, skip=True)
        if not toc:
            # point at first spine href
            first = _first_spine_href(parsed)
            toc = [TocEntry(label=member.title or parsed.title, href=first or "")]
        remapped = [_remap_toc(e, prefix, parsed.opf_dir) for e in toc]
        toc_roots.append(
            TocEntry(label=member.title or parsed.title, href=remapped[0].href, children=remapped)
        )
        member_meta.append(_member_snapshot(member, prefix=prefix, active=True))

    book_title = title or (members[0].title if members else "Omnibus")
    _write_package(
        files,
        title=book_title,
        language=language,
        uid=f"omnibus:{oid}",
        manifest_items=manifest_items,
        spine_ids=spine_ids,
        toc_roots=toc_roots,
    )
    meta = {
        "id": oid,
        "kind": kind,
        "series_id": series_id or "",
        "collection": collection or "",
        "auto_update": bool(auto_update),
        "title": book_title,
        "member_ids": [m.member_id for m in members],
        "prefixes": prefixes,
        "skip_prefaces_after_first": bool(skip_prefaces_after_first),
    }
    write_omnibus_sidecar(files, meta, member_meta)
    _write_epub_zip(dest, files)
    return dest


def append_members(
    omnibus_path: str | Path,
    new_members: Sequence[MemberSpec],
    dest: str | Path | None = None,
    *,
    skip_prefaces_after_first: bool | None = None,
) -> Path:
    """Append unknown members; leave existing member bytes/paths unchanged."""
    omnibus_path = Path(omnibus_path)
    dest = Path(dest or omnibus_path)
    meta = read_omnibus_meta(omnibus_path) or {}
    existing_ids = set(meta.get("member_ids") or [])
    to_add = [m for m in new_members if m.member_id not in existing_ids]
    if not to_add:
        if dest != omnibus_path:
            dest.write_bytes(omnibus_path.read_bytes())
        return dest

    with zipfile.ZipFile(omnibus_path) as zf:
        files = {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
    skip = (
        meta.get("skip_prefaces_after_first", True)
        if skip_prefaces_after_first is None
        else skip_prefaces_after_first
    )
    package, opf_path = _load_package(files)
    manifest_items, spine_ids, id_to_href = _manifest_spine_from_package(package)
    toc_roots = _toc_from_files(files, opf_path)
    prefixes = dict(meta.get("prefixes") or {})
    members = read_omnibus_members(omnibus_path)
    start_index = len(existing_ids)

    for offset, member in enumerate(to_add):
        if member.epub_path is None:
            raise ValueError(f"member {member.member_id} missing epub_path")
        parsed = parse_epub(member.epub_path)
        prefix = member_prefix(member.member_id)
        prefixes[member.member_id] = prefix
        _copy_member_into(
            files,
            parsed,
            prefix=prefix,
            id_prefix=f"a{start_index + offset}_",
            manifest_items=manifest_items,
            spine_ids=spine_ids,
        )
        toc = list(parsed.toc)
        if skip:
            toc = filter_toc_prefaces(toc, skip=True)
        if not toc:
            first = _first_spine_href(parsed)
            toc = [TocEntry(label=member.title or parsed.title, href=first or "")]
        remapped = [_remap_toc(e, prefix, parsed.opf_dir) for e in toc]
        toc_roots.append(
            TocEntry(
                label=member.title or parsed.title,
                href=remapped[0].href,
                children=remapped,
            )
        )
        members.append(_member_snapshot(member, prefix=prefix, active=True))
        existing_ids.add(member.member_id)

    title = _dc_text(package, "title") or meta.get("title") or "Omnibus"
    language = _dc_text(package, "language") or "en"
    uid = _dc_text(package, "identifier") or f"omnibus:{meta.get('id') or uuid.uuid4()}"
    # drop old opf/ncx/nav; rewrite
    _strip_package_files(files, opf_path)
    _write_package(
        files,
        title=title,
        language=language,
        uid=uid,
        manifest_items=manifest_items,
        spine_ids=spine_ids,
        toc_roots=toc_roots,
        opf_path=opf_path,
    )
    clean = list(meta.get("member_ids") or [])
    for m in to_add:
        if m.member_id not in clean:
            clean.append(m.member_id)
    meta["member_ids"] = clean
    meta["prefixes"] = prefixes
    write_omnibus_sidecar(files, meta, members)
    _write_epub_zip(dest, files)
    return dest


def remove_members(
    omnibus_path: str | Path,
    member_ids: Sequence[str],
    dest: str | Path | None = None,
) -> Path:
    """Remove members' prefixes from spine/manifest/files; mark inactive in JSONL."""
    omnibus_path = Path(omnibus_path)
    dest = Path(dest or omnibus_path)
    remove = {str(m) for m in member_ids}
    if not remove:
        if dest != omnibus_path:
            dest.write_bytes(omnibus_path.read_bytes())
        return dest

    meta = read_omnibus_meta(omnibus_path) or {}
    prefixes = dict(meta.get("prefixes") or {})
    drop_prefixes = [prefixes[m] for m in remove if m in prefixes]
    with zipfile.ZipFile(omnibus_path) as zf:
        files = {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}

    # delete member files
    for name in list(files):
        for pref in drop_prefixes:
            if name == pref or name.startswith(pref + "/"):
                del files[name]
                break

    package, opf_path = _load_package(files)
    manifest_items, spine_ids, id_to_href = _manifest_spine_from_package(package)
    # filter manifest/spine pointing into removed prefixes
    keep_ids: list[str] = []
    new_manifest: list[tuple[str, str, str, str]] = []
    for item_id, href, mt, props in manifest_items:
        full = href  # hrefs in our omnibus are from OPF root
        if any(full == p or full.startswith(p + "/") for p in drop_prefixes):
            continue
        new_manifest.append((item_id, href, mt, props))
        keep_ids.append(item_id)
    keep_set = set(keep_ids)
    new_spine = [s for s in spine_ids if s in keep_set]
    toc_roots = _filter_toc_prefixes(_toc_from_files(files, opf_path), drop_prefixes)

    members = read_omnibus_members(omnibus_path)
    for row in members:
        if str(row.get("member_id") or "") in remove:
            row["active"] = False
    meta["member_ids"] = [
        m for m in (meta.get("member_ids") or []) if str(m) not in remove
    ]
    for mid in remove:
        prefixes.pop(mid, None)
    meta["prefixes"] = prefixes

    title = _dc_text(package, "title") or meta.get("title") or "Omnibus"
    language = _dc_text(package, "language") or "en"
    uid = _dc_text(package, "identifier") or f"omnibus:{meta.get('id') or uuid.uuid4()}"
    _strip_package_files(files, opf_path)
    _write_package(
        files,
        title=title,
        language=language,
        uid=uid,
        manifest_items=new_manifest,
        spine_ids=new_spine,
        toc_roots=toc_roots,
        opf_path=opf_path,
    )
    write_omnibus_sidecar(files, meta, members)
    _write_epub_zip(dest, files)
    return dest


def reorder_members(
    omnibus_path: str | Path,
    member_ids_in_order: Sequence[str],
    dest: str | Path | None = None,
) -> Path:
    """Reorder spine/ToC by member without rewriting member file paths."""
    omnibus_path = Path(omnibus_path)
    dest = Path(dest or omnibus_path)
    meta = read_omnibus_meta(omnibus_path) or {}
    prefixes = dict(meta.get("prefixes") or {})
    order = [str(m) for m in member_ids_in_order]
    for mid in meta.get("member_ids") or []:
        if mid not in order:
            order.append(mid)

    with zipfile.ZipFile(omnibus_path) as zf:
        files = {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
    package, opf_path = _load_package(files)
    manifest_items, spine_ids, id_to_href = _manifest_spine_from_package(package)
    href_to_id = {href: iid for iid, href, _mt, _p in manifest_items}

    # group spine ids by member prefix
    groups: dict[str, list[str]] = {mid: [] for mid in order}
    other: list[str] = []
    for sid in spine_ids:
        href = id_to_href.get(sid, "")
        matched = None
        for mid in order:
            pref = prefixes.get(mid, "")
            if pref and (href == pref or href.startswith(pref + "/")):
                matched = mid
                break
        if matched:
            groups[matched].append(sid)
        else:
            other.append(sid)
    new_spine = other[:]  # cover etc. first if any
    for mid in order:
        new_spine.extend(groups.get(mid) or [])

    toc_roots = _toc_from_files(files, opf_path)
    # rebuild toc order by matching top-level href prefixes
    by_mid: dict[str, TocEntry] = {}
    leftovers: list[TocEntry] = []
    for entry in toc_roots:
        matched = None
        for mid in order:
            pref = prefixes.get(mid, "")
            if pref and _href_under_prefix(entry.href, pref):
                matched = mid
                break
        if matched:
            by_mid[matched] = entry
        else:
            leftovers.append(entry)
    new_toc = leftovers + [by_mid[m] for m in order if m in by_mid]

    title = _dc_text(package, "title") or meta.get("title") or "Omnibus"
    language = _dc_text(package, "language") or "en"
    uid = _dc_text(package, "identifier") or f"omnibus:{meta.get('id') or uuid.uuid4()}"
    _strip_package_files(files, opf_path)
    _write_package(
        files,
        title=title,
        language=language,
        uid=uid,
        manifest_items=manifest_items,
        spine_ids=new_spine,
        toc_roots=new_toc,
        opf_path=opf_path,
    )
    meta["member_ids"] = order
    members = read_omnibus_members(omnibus_path)
    # stable member jsonl order
    by_id = {str(r.get("member_id")): r for r in members}
    ordered_members = [by_id[m] for m in order if m in by_id]
    for r in members:
        mid = str(r.get("member_id"))
        if mid not in order:
            ordered_members.append(r)
    write_omnibus_sidecar(files, meta, ordered_members)
    _write_epub_zip(dest, files)
    return dest


def rebuild_epubs(
    members: Sequence[MemberSpec],
    dest: str | Path,
    *,
    omnibus_id: str | None = None,
    kind: str = "selected",
    title: str | None = None,
    series_id: str = "",
    collection: str = "",
    auto_update: bool = False,
    skip_prefaces_after_first: bool = True,
    language: str = "en",
) -> Path:
    """Full rematerialize (new paths). Notes/highlights will not match."""
    return merge_epubs(
        members,
        dest,
        omnibus_id=omnibus_id,
        kind=kind,
        title=title,
        series_id=series_id,
        collection=collection,
        auto_update=auto_update,
        skip_prefaces_after_first=skip_prefaces_after_first,
        language=language,
    )


def extract_member_epub(
    omnibus_path: str | Path,
    member_id: str,
    dest: str | Path,
    *,
    title: str | None = None,
) -> Path:
    """Extract one member's prefix into a standalone EPUB."""
    omnibus_path = Path(omnibus_path)
    dest = Path(dest)
    meta = read_omnibus_meta(omnibus_path) or {}
    prefixes = dict(meta.get("prefixes") or {})
    pref = prefixes.get(str(member_id))
    if not pref:
        raise ValueError(f"member {member_id} not in omnibus")
    with zipfile.ZipFile(omnibus_path) as zf:
        files = {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
    package, opf_path = _load_package(files)
    manifest_items, spine_ids, id_to_href = _manifest_spine_from_package(package)
    member_files: dict[str, bytes] = {}
    new_manifest: list[tuple[str, str, str, str]] = []
    new_spine: list[str] = []
    for item_id, href, mt, props in manifest_items:
        if not (href == pref or href.startswith(pref + "/")):
            continue
        rel = href[len(pref) + 1 :] if href.startswith(pref + "/") else posixpath.basename(href)
        member_files[rel] = files[href]
        new_manifest.append((item_id, rel, mt, props))
        if item_id in spine_ids:
            new_spine.append(item_id)
    # preserve spine order
    spine_set = set(new_spine)
    new_spine = [s for s in spine_ids if s in spine_set]
    toc_all = _toc_from_files(files, opf_path)
    toc = []
    for entry in toc_all:
        if _href_under_prefix(entry.href, pref):
            toc.append(_strip_toc_prefix(entry, pref))
    members = read_omnibus_members(omnibus_path)
    row = next((r for r in members if str(r.get("member_id")) == str(member_id)), {})
    book_title = title or str(row.get("title") or member_id)
    out_files: dict[str, bytes] = dict(member_files)
    _write_package(
        out_files,
        title=book_title,
        language="en",
        uid=f"member:{member_id}",
        manifest_items=new_manifest,
        spine_ids=new_spine,
        toc_roots=toc or [TocEntry(label=book_title, href=new_manifest[0][1] if new_manifest else "")],
        opf_path="content.opf",
    )
    _write_epub_zip(dest, out_files)
    return dest


def update_omnibus_sidecar_file(
    omnibus_path: str | Path,
    *,
    meta_updates: dict[str, Any] | None = None,
    members: Sequence[dict[str, Any]] | None = None,
    dest: str | Path | None = None,
) -> Path:
    """Rewrite META-INF omnibus sidecar inside an existing EPUB (paths unchanged)."""
    omnibus_path = Path(omnibus_path)
    dest = Path(dest or omnibus_path)
    with zipfile.ZipFile(omnibus_path) as zf:
        files = {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
    meta = read_omnibus_meta(omnibus_path) or {}
    if meta_updates:
        meta.update(meta_updates)
    member_rows = list(members) if members is not None else read_omnibus_members(omnibus_path)
    write_omnibus_sidecar(files, meta, member_rows)
    _write_epub_zip(dest, files)
    return dest


# --- internals -----------------------------------------------------------------


def _read_zip_bytes(epub: str | Path | zipfile.ZipFile, name: str) -> bytes | None:
    if isinstance(epub, zipfile.ZipFile):
        try:
            return epub.read(name)
        except KeyError:
            return None
    path = Path(epub)
    if not path.is_file():
        return None
    with zipfile.ZipFile(path) as zf:
        try:
            return zf.read(name)
        except KeyError:
            return None


def _dc_text(package: ET.Element, local: str) -> str:
    el = package.find(f".//{{{DC_NS}}}{local}")
    if el is None:
        el = package.find(f".//{{*}}{local}")
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _parse_toc(
    zf: zipfile.ZipFile,
    package: ET.Element,
    manifest: dict[str, dict[str, str]],
    opf_dir: str,
    opf_path: str,
) -> list[TocEntry]:
    # prefer NCX
    ncx_href = None
    spine = package.find(f".//{{{OPF_NS}}}spine")
    if spine is None:
        spine = package.find(".//{*}spine")
    ncx_id = spine.get("toc") if spine is not None else None
    if ncx_id and ncx_id in manifest:
        ncx_href = manifest[ncx_id]["href"]
    if not ncx_href:
        for item in manifest.values():
            if item.get("media-type") == "application/x-dtbncx+xml":
                ncx_href = item["href"]
                break
    if ncx_href:
        ncx_path = posixpath.normpath(posixpath.join(opf_dir, ncx_href)) if opf_dir else ncx_href
        try:
            return _parse_ncx(zf.read(ncx_path))
        except KeyError:
            pass
    # EPUB3 nav
    for item in manifest.values():
        props = item.get("properties") or ""
        if "nav" in props.split():
            nav_path = (
                posixpath.normpath(posixpath.join(opf_dir, item["href"]))
                if opf_dir
                else item["href"]
            )
            try:
                return _parse_nav(zf.read(nav_path))
            except KeyError:
                continue
    return []


def _parse_ncx(data: bytes) -> list[TocEntry]:
    root = ET.fromstring(data)

    def walk(points: Iterable[ET.Element]) -> list[TocEntry]:
        out: list[TocEntry] = []
        for np in points:
            label_el = np.find(f"./{{{NCX_NS}}}navLabel/{{{NCX_NS}}}text")
            if label_el is None:
                label_el = np.find(".//{*}navLabel/{*}text")
            content = np.find(f"./{{{NCX_NS}}}content")
            if content is None:
                content = np.find("./{*}content")
            label = (label_el.text or "").strip() if label_el is not None else ""
            href = content.get("src", "") if content is not None else ""
            kids = np.findall(f"./{{{NCX_NS}}}navPoint") or np.findall("./{*}navPoint")
            out.append(TocEntry(label=label or href, href=href, children=walk(kids)))
        return out

    nav_map = root.find(f"./{{{NCX_NS}}}navMap")
    if nav_map is None:
        nav_map = root.find(".//{*}navMap")
    if nav_map is None:
        return []
    top = nav_map.findall(f"./{{{NCX_NS}}}navPoint") or nav_map.findall("./{*}navPoint")
    return walk(top)


def _parse_nav(data: bytes) -> list[TocEntry]:
    root = ET.fromstring(data)

    def walk_ol(ol: ET.Element | None) -> list[TocEntry]:
        if ol is None:
            return []
        out: list[TocEntry] = []
        for li in list(ol):
            if not li.tag.endswith("li"):
                continue
            a = None
            child_ol = None
            for child in list(li):
                if child.tag.endswith("a") and a is None:
                    a = child
                if child.tag.endswith("ol"):
                    child_ol = child
            if a is None:
                continue
            label = "".join(a.itertext()).strip()
            href = a.get("href") or ""
            out.append(TocEntry(label=label or href, href=href, children=walk_ol(child_ol)))
        return out

    for nav in root.iter():
        if not str(nav.tag).endswith("nav"):
            continue
        epub_type = nav.get(f"{{{EPUB_NS}}}type") or nav.get("epub:type") or ""
        if epub_type and "toc" not in epub_type:
            continue
        ol = None
        for child in list(nav):
            if str(child.tag).endswith("ol"):
                ol = child
                break
        entries = walk_ol(ol)
        if entries:
            return entries
    return []


def _first_spine_href(parsed: ParsedEpub) -> str:
    for sid in parsed.spine:
        item = parsed.manifest.get(sid)
        if item:
            return item["href"]
    return ""


def _remap_toc(entry: TocEntry, prefix: str, opf_dir: str) -> TocEntry:
    href = entry.href
    path_part, frag = (href.split("#", 1) + [""])[:2] if href else ("", "")
    if path_part:
        # NCX hrefs are relative to OPF dir
        joined = posixpath.normpath(posixpath.join(opf_dir, path_part)) if opf_dir else path_part
        # strip leading ./
        while joined.startswith("./"):
            joined = joined[2:]
        new_href = f"{prefix}/{joined}"
        if frag:
            new_href += f"#{frag}"
    else:
        new_href = f"#{frag}" if frag else ""
    return TocEntry(
        label=entry.label,
        href=new_href,
        children=[_remap_toc(c, prefix, opf_dir) for c in entry.children],
    )


def _copy_member_into(
    files: dict[str, bytes],
    parsed: ParsedEpub,
    *,
    prefix: str,
    id_prefix: str,
    manifest_items: list[tuple[str, str, str, str]],
    spine_ids: list[str],
) -> dict[str, str]:
    """Copy member resources under prefix; extend manifest/spine. Returns old→new id map."""
    id_map: dict[str, str] = {}
    # map zip path -> new zip path
    path_map: dict[str, str] = {}
    for name, data in parsed.files.items():
        if name.startswith("META-INF/") or name.endswith(".opf") or name.endswith(".ncx"):
            # skip container/package; still copy non-nav resources under opf tree
            if name.endswith(".opf") or name.endswith(".ncx") or name == "META-INF/container.xml":
                continue
            if name.startswith("META-INF/"):
                continue
        # only copy files under opf_dir or root content
        if parsed.opf_dir and not name.startswith(parsed.opf_dir) and name != parsed.opf_path:
            if not name.startswith(parsed.opf_dir.rstrip("/")):
                # allow root-level assets next to opf
                if "/" in name and not name.startswith(parsed.opf_dir):
                    continue
        rel = name[len(parsed.opf_dir) :] if parsed.opf_dir and name.startswith(parsed.opf_dir) else name
        if not rel or rel.endswith(".opf") or rel.endswith(".ncx"):
            continue
        # skip nav documents — we rebuild toc
        item_props = ""
        for item in parsed.manifest.values():
            if item["href"] == rel or posixpath.normpath(
                posixpath.join(parsed.opf_dir, item["href"])
            ) == name:
                item_props = item.get("properties") or ""
                break
        if "nav" in item_props.split():
            continue
        new_name = f"{prefix}/{rel}"
        path_map[name] = new_name
        files[new_name] = data

    # rewrite links inside copied xhtml/css
    for old_name, new_name in list(path_map.items()):
        data = files[new_name]
        low = new_name.casefold()
        if low.endswith((".xhtml", ".html", ".htm", ".css", ".xml")):
            files[new_name] = _rewrite_resource_links(
                data, src_dir=posixpath.dirname(old_name), prefix=prefix, opf_dir=parsed.opf_dir
            )

    for old_id, item in parsed.manifest.items():
        href = item["href"]
        abs_path = (
            posixpath.normpath(posixpath.join(parsed.opf_dir, href))
            if parsed.opf_dir
            else href
        )
        props = item.get("properties") or ""
        if "nav" in props.split():
            continue
        if item.get("media-type") == "application/x-dtbncx+xml":
            continue
        if abs_path not in path_map and href not in {posixpath.relpath(v, prefix) for v in path_map.values()}:
            # try rel as key
            candidate = path_map.get(abs_path) or path_map.get(
                posixpath.join(parsed.opf_dir, href) if parsed.opf_dir else href
            )
            if not candidate:
                continue
            new_href = candidate
        else:
            new_href = path_map.get(abs_path)
            if not new_href:
                continue
        new_id = f"{id_prefix}{old_id}"
        id_map[old_id] = new_id
        # strip nav property if any
        clean_props = " ".join(p for p in props.split() if p != "nav")
        manifest_items.append(
            (new_id, new_href, item.get("media-type") or "application/octet-stream", clean_props)
        )

    for sid in parsed.spine:
        if sid in id_map:
            spine_ids.append(id_map[sid])
    return id_map


def _rewrite_resource_links(
    data: bytes, *, src_dir: str, prefix: str, opf_dir: str
) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data

    def rewrite_url(url: str) -> str:
        url = url.strip()
        if not url or url.startswith(("#", "http:", "https:", "data:", "mailto:")):
            return url
        path_part, frag = (url.split("#", 1) + [""])[:2]
        # resolve relative to resource dir
        abs_src = posixpath.normpath(posixpath.join(src_dir, path_part))
        if opf_dir and abs_src.startswith(opf_dir):
            rel = abs_src[len(opf_dir) :]
        else:
            rel = abs_src
        new = f"{prefix}/{rel}"
        # make relative from the rewritten file's directory (prefix/…)
        # files end up at prefix/relpath; use absolute-from-opf style hrefs
        # Our OPF is at content.opf root, so hrefs are prefix/...
        return new + (f"#{frag}" if frag else "")

    # href= / src=
    def repl_attr(match: re.Match[str]) -> str:
        attr, quote, url = match.group(1), match.group(2), match.group(3)
        return f"{attr}={quote}{rewrite_url(url)}{quote}"

    text = re.sub(
        r"""\b(href|src)\s*=\s*(['"])([^'"]+)\2""",
        repl_attr,
        text,
        flags=re.I,
    )

    def repl_css(match: re.Match[str]) -> str:
        q, url = match.group(1), match.group(2)
        return f"url({q}{rewrite_url(url)}{q})"

    text = _CSS_URL_RE.sub(repl_css, text)
    return text.encode("utf-8")


def _member_snapshot(member: MemberSpec, *, prefix: str, active: bool) -> dict[str, Any]:
    row = dict(member.record) if member.record else {}
    row["member_id"] = member.member_id
    row["title"] = member.title or row.get("title") or member.member_id
    row["prefix"] = prefix
    row["active"] = active
    if member.epub_path is not None:
        row["source_epub"] = str(member.epub_path)
    return row


def _write_package(
    files: dict[str, bytes],
    *,
    title: str,
    language: str,
    uid: str,
    manifest_items: list[tuple[str, str, str, str]],
    spine_ids: list[str],
    toc_roots: list[TocEntry],
    opf_path: str = "content.opf",
) -> None:
    opf_dir = posixpath.dirname(opf_path)
    if opf_dir and not opf_dir.endswith("/"):
        opf_dir += "/"
    ncx_href = "toc.ncx"
    nav_href = "nav.xhtml"
    ncx_path = f"{opf_dir}{ncx_href}" if opf_dir else ncx_href
    nav_path = f"{opf_dir}{nav_href}" if opf_dir else nav_href

    # ensure ncx/nav in manifest
    items = list(manifest_items)
    items.append(("ncx", ncx_href, "application/x-dtbncx+xml", ""))
    items.append(("nav", nav_href, "application/xhtml+xml", "nav"))

    manifest_xml = "\n".join(
        f'    <item id="{escape(iid)}" href="{escape(href)}" media-type="{escape(mt)}"'
        + (f' properties="{escape(props)}"' if props else "")
        + "/>"
        for iid, href, mt, props in items
    )
    spine_xml = "\n".join(f'    <itemref idref="{escape(sid)}"/>' for sid in spine_ids)
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="BookId">{escape(uid)}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>{escape(language)}</dc:language>
    <dc:publisher>Fanfic Organizer</dc:publisher>
  </metadata>
  <manifest>
{manifest_xml}
  </manifest>
  <spine toc="ncx">
{spine_xml}
  </spine>
</package>
"""
    files[opf_path] = opf.encode("utf-8")
    files[ncx_path] = _build_ncx(title, uid, toc_roots).encode("utf-8")
    files[nav_path] = _build_nav(title, toc_roots).encode("utf-8")
    files["META-INF/container.xml"] = f"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="{escape(opf_path)}" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""".encode("utf-8")


def _build_ncx(title: str, uid: str, roots: list[TocEntry]) -> str:
    play = 0

    def points(entries: list[TocEntry], depth: int = 0) -> str:
        nonlocal play
        chunks: list[str] = []
        for entry in entries:
            play += 1
            pid = f"np{play}"
            kids = points(entry.children, depth + 1) if entry.children else ""
            chunks.append(
                f'<navPoint id="{pid}" playOrder="{play}">'
                f"<navLabel><text>{escape(entry.label)}</text></navLabel>"
                f'<content src="{escape(entry.href)}"/>'
                f"{kids}</navPoint>"
            )
        return "".join(chunks)

    body = points(roots)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{escape(uid)}"/>
    <meta name="dtb:depth" content="3"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{escape(title)}</text></docTitle>
  <navMap>{body}</navMap>
</ncx>
"""


def _build_nav(title: str, roots: list[TocEntry]) -> str:
    def ol(entries: list[TocEntry]) -> str:
        items = []
        for e in entries:
            child = ol(e.children) if e.children else ""
            items.append(f'<li><a href="{escape(e.href)}">{escape(e.label)}</a>{child}</li>')
        return "<ol>" + "".join(items) + "</ol>" if items else ""

    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{escape(title)}</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Contents</h1>
    {ol(roots)}
  </nav>
</body>
</html>
"""


def _write_epub_zip(dest: Path, files: dict[str, bytes]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in sorted(files.items()):
            if name == "mimetype":
                continue
            zf.writestr(name, data)


def _load_package(files: dict[str, bytes]) -> tuple[ET.Element, str]:
    container = ET.fromstring(files["META-INF/container.xml"])
    rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None:
        rootfile = container.find(".//{*}rootfile")
    opf_path = rootfile.get("full-path", "content.opf") if rootfile is not None else "content.opf"
    return ET.fromstring(files[opf_path]), opf_path


def _manifest_spine_from_package(
    package: ET.Element,
) -> tuple[list[tuple[str, str, str, str]], list[str], dict[str, str]]:
    items: list[tuple[str, str, str, str]] = []
    id_to_href: dict[str, str] = {}
    for item in package.findall(f".//{{{OPF_NS}}}manifest/{{{OPF_NS}}}item"):
        iid = item.get("id") or ""
        href = item.get("href") or ""
        mt = item.get("media-type") or ""
        props = item.get("properties") or ""
        if not iid:
            continue
        if mt == "application/x-dtbncx+xml" or "nav" in props.split():
            continue
        items.append((iid, href, mt, props))
        id_to_href[iid] = href
    if not items:
        for item in package.findall(".//{*}manifest/{*}item"):
            iid = item.get("id") or ""
            href = item.get("href") or ""
            mt = item.get("media-type") or ""
            props = item.get("properties") or ""
            if not iid or mt == "application/x-dtbncx+xml" or "nav" in props.split():
                continue
            items.append((iid, href, mt, props))
            id_to_href[iid] = href
    spine: list[str] = []
    for ref in package.findall(f".//{{{OPF_NS}}}spine/{{{OPF_NS}}}itemref"):
        idref = ref.get("idref")
        if idref:
            spine.append(idref)
    if not spine:
        for ref in package.findall(".//{*}spine/{*}itemref"):
            idref = ref.get("idref")
            if idref:
                spine.append(idref)
    return items, spine, id_to_href


def _strip_package_files(files: dict[str, bytes], opf_path: str) -> None:
    opf_dir = posixpath.dirname(opf_path)
    for name in list(files):
        base = posixpath.basename(name)
        if base in {"content.opf", "toc.ncx", "nav.xhtml"} or name == opf_path:
            if name == opf_path or (
                opf_dir and name.startswith(opf_dir + "/") and base in {"toc.ncx", "nav.xhtml"}
            ):
                del files[name]
            elif not opf_dir and base in {"content.opf", "toc.ncx", "nav.xhtml"}:
                del files[name]


def _toc_from_files(files: dict[str, bytes], opf_path: str) -> list[TocEntry]:
    opf_dir = posixpath.dirname(opf_path)
    ncx = f"{opf_dir}/toc.ncx" if opf_dir else "toc.ncx"
    if ncx in files:
        return _parse_ncx(files[ncx])
    nav = f"{opf_dir}/nav.xhtml" if opf_dir else "nav.xhtml"
    if nav in files:
        return _parse_nav(files[nav])
    return []


def _href_under_prefix(href: str, prefix: str) -> bool:
    path = href.split("#", 1)[0]
    return path == prefix or path.startswith(prefix + "/")


def _filter_toc_prefixes(entries: list[TocEntry], drop_prefixes: Sequence[str]) -> list[TocEntry]:
    out: list[TocEntry] = []
    for e in entries:
        if any(_href_under_prefix(e.href, p) for p in drop_prefixes):
            continue
        kids = _filter_toc_prefixes(e.children, drop_prefixes)
        out.append(TocEntry(label=e.label, href=e.href, children=kids))
    return out


def _strip_toc_prefix(entry: TocEntry, prefix: str) -> TocEntry:
    path, frag = (entry.href.split("#", 1) + [""])[:2]
    if path.startswith(prefix + "/"):
        path = path[len(prefix) + 1 :]
    href = path + (f"#{frag}" if frag else "")
    return TocEntry(
        label=entry.label,
        href=href,
        children=[_strip_toc_prefix(c, prefix) for c in entry.children],
    )
