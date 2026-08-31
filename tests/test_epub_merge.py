"""Tests for EPUB omnibus merge / append / shrink / reorder / explode."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from ao3kit.epub_merge import (
    MemberSpec,
    append_members,
    extract_member_epub,
    merge_epubs,
    read_omnibus_members,
    read_omnibus_meta,
    remove_members,
    reorder_members,
)
from ao3kit.omnibus import merge_omnibus_record, series_omnibus_title, sort_collection_members


def test_series_omnibus_title():
    assert series_omnibus_title("Time Storm") == "Time Storm - Series"
    assert series_omnibus_title("Time Storm - Series") == "Time Storm - Series"
    assert series_omnibus_title("") == "Series"
    assert series_omnibus_title("  ") == "Series"


def _mini_epub(path: Path, *, title: str, chapters: list[tuple[str, str]]) -> Path:
    """Build a small AO3-like EPUB2 with NCX."""
    manifest_items = []
    spine = []
    files: dict[str, str] = {}
    nav_points = []
    for i, (label, body) in enumerate(chapters):
        name = f"chap_{i}.xhtml"
        iid = f"c{i}"
        files[name] = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{label}</title>
<link rel="stylesheet" href="style.css" type="text/css"/>
</head><body><h2 id="h">{label}</h2><p>{body}</p></body></html>
"""
        manifest_items.append(
            f'<item id="{iid}" href="{name}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{iid}"/>')
        nav_points.append(
            f'<navPoint id="np{i}" playOrder="{i+1}">'
            f"<navLabel><text>{label}</text></navLabel>"
            f'<content src="{name}"/>'
            f"</navPoint>"
        )
    files["style.css"] = "body { font-family: serif; }\n"
    manifest_items.append(
        '<item id="css" href="style.css" media-type="text/css"/>'
    )
    manifest_items.append(
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    )
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>Author</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">{title}</dc:identifier>
  </metadata>
  <manifest>
    {''.join(manifest_items)}
  </manifest>
  <spine toc="ncx">
    {''.join(spine)}
  </spine>
</package>
"""
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{title}"/></head>
  <docTitle><text>{title}</text></docTitle>
  <navMap>{''.join(nav_points)}</navMap>
</ncx>
"""
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    with ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", opf)
        zf.writestr("toc.ncx", ncx)
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def test_merge_hierarchical_toc_and_sidecar(tmp_path: Path):
    a = _mini_epub(
        tmp_path / "a.epub",
        title="Work A",
        chapters=[("Preface", "pre"), ("Chapter 1", "one"), ("Chapter 2", "two")],
    )
    b = _mini_epub(
        tmp_path / "b.epub",
        title="Work B",
        chapters=[("Preface", "preb"), ("Chapter 1", "bone")],
    )
    out = tmp_path / "omnibus.epub"
    merge_epubs(
        [
            MemberSpec("111", "Work A", a, {"work_id": "111", "title": "Work A"}),
            MemberSpec("222", "Work B", b, {"work_id": "222", "title": "Work B"}),
        ],
        out,
        kind="selected",
        title="A + B",
        skip_prefaces_after_first=True,
    )
    meta = read_omnibus_meta(out)
    assert meta is not None
    assert meta["member_ids"] == ["111", "222"]
    assert "m/111" in meta["prefixes"]["111"]
    members = read_omnibus_members(out)
    assert len(members) == 2
    with ZipFile(out) as zf:
        names = set(zf.namelist())
        assert "m/111/chap_1.xhtml" in names
        assert "m/222/chap_1.xhtml" in names
        ncx = zf.read("toc.ncx").decode()
        assert "Work A" in ncx
        assert "Work B" in ncx
        assert "Chapter 1" in ncx
        # second work preface skipped in toc labels under work B children —
        # Work B still present as parent
        assert ncx.count("Preface") == 1


def test_append_keeps_first_member_bytes(tmp_path: Path):
    a = _mini_epub(tmp_path / "a.epub", title="A", chapters=[("Chapter 1", "a1")])
    b = _mini_epub(tmp_path / "b.epub", title="B", chapters=[("Chapter 1", "b1")])
    c = _mini_epub(tmp_path / "c.epub", title="C", chapters=[("Chapter 1", "c1")])
    out = tmp_path / "omni.epub"
    merge_epubs(
        [MemberSpec("a", "A", a), MemberSpec("b", "B", b)],
        out,
        omnibus_id="oid1",
    )
    with ZipFile(out) as zf:
        before = zf.read("m/a/chap_0.xhtml")
    append_members(out, [MemberSpec("c", "C", c)])
    with ZipFile(out) as zf:
        after = zf.read("m/a/chap_0.xhtml")
        assert "m/c/chap_0.xhtml" in zf.namelist()
    assert before == after
    meta = read_omnibus_meta(out)
    assert meta["member_ids"] == ["a", "b", "c"]


def test_remove_and_reorder(tmp_path: Path):
    a = _mini_epub(tmp_path / "a.epub", title="A", chapters=[("C1", "a")])
    b = _mini_epub(tmp_path / "b.epub", title="B", chapters=[("C1", "b")])
    c = _mini_epub(tmp_path / "c.epub", title="C", chapters=[("C1", "c")])
    out = tmp_path / "omni.epub"
    merge_epubs(
        [MemberSpec("a", "A", a), MemberSpec("b", "B", b), MemberSpec("c", "C", c)],
        out,
    )
    remove_members(out, ["b"])
    meta = read_omnibus_meta(out)
    assert meta["member_ids"] == ["a", "c"]
    with ZipFile(out) as zf:
        names = zf.namelist()
        assert not any(n.startswith("m/b/") for n in names)
    reorder_members(out, ["c", "a"])
    meta = read_omnibus_meta(out)
    assert meta["member_ids"] == ["c", "a"]
    # paths still stable
    with ZipFile(out) as zf:
        assert "m/a/chap_0.xhtml" in zf.namelist()
        assert "m/c/chap_0.xhtml" in zf.namelist()


def test_extract_member(tmp_path: Path):
    a = _mini_epub(tmp_path / "a.epub", title="A", chapters=[("Chapter 1", "hello")])
    b = _mini_epub(tmp_path / "b.epub", title="B", chapters=[("Chapter 1", "world")])
    omni = tmp_path / "omni.epub"
    merge_epubs([MemberSpec("a", "A", a), MemberSpec("b", "B", b)], omni)
    dest = tmp_path / "a_out.epub"
    extract_member_epub(omni, "a", dest, title="A")
    with ZipFile(dest) as zf:
        assert any(n.endswith("chap_0.xhtml") for n in zf.namelist())
        body = next(zf.read(n) for n in zf.namelist() if n.endswith("chap_0.xhtml"))
        assert b"hello" in body


def test_merge_omnibus_record_and_sort():
    records = [
        {
            "work_id": "2",
            "title": "B",
            "author": "Ann",
            "published": "2020-02-01",
            "cleaned": {"tags": ["Fluff"], "fandoms": ["X"], "complete": True},
            "metadata": {"words": 100, "complete": True},
        },
        {
            "work_id": "1",
            "title": "A",
            "author": "Bob",
            "published": "2020-01-01",
            "cleaned": {"tags": ["Angst"], "fandoms": ["X"], "complete": True},
            "metadata": {"words": 50, "complete": True},
        },
    ]
    ordered = sort_collection_members(records)
    assert [r["work_id"] for r in ordered] == ["1", "2"]
    merged = merge_omnibus_record(
        ordered,
        omnibus_id="u1",
        kind="collection",
        title="My Collection",
        collection="My Collection",
        auto_update=True,
    )
    assert merged["identifiers"]["omnibus"] == "u1"
    assert merged["cleaned"]["word_count"] == 150
    assert "Fluff" in merged["cleaned"]["tags"]
    assert "Angst" in merged["cleaned"]["tags"]
    assert [m["title"] for m in merged["members"]] == ["A", "B"]
    assert [m["member_id"] for m in merged["members"]] == ["1", "2"]
    assert merged["cleaned"]["complete"] is True
    assert "My Collection" in merged["cleaned"]["collections"]
