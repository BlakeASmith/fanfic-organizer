"""Generated AO3-style EPUB covers."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest

from ao3kit.config import CoverSettings, UserSettings, init_user_config, load_user_config
from ao3kit.covers import (
    CoverInfo,
    apply_cover_to_epub,
    choose_colours,
    cover_info_from_epub,
    cover_info_from_record,
    epub_has_cover,
    extract_cover_bytes,
    inject_cover,
    main as cover_main,
    merge_cover_info,
    render_cover_image,
    _format_footer,
)

pytest.importorskip("PIL")


def ao3_epub_bytes(
    *,
    title: str = "Operation Cameo",
    author: str = "alexwlchan",
    fandom: str = "Operation Mincemeat: A New Musical - SpitLip",
) -> bytes:
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">cover-test</dc:identifier>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="page" href="titlepage.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="page"/>
  </spine>
</package>
"""
    titlepage = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
<dl>
<dt>Fandom:</dt>
<dd>{fandom}</dd>
<dt>Rating:</dt>
<dd>General Audiences</dd>
<dt>Stats:</dt>
<dd>Published: 2019-02-05 Words: 12,000 Chapters: 1/1 Kudos: 200 Hits: 1000</dd>
</dl>
</body>
</html>
"""
    nav = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body></body></html>
"""
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", opf)
        zf.writestr("titlepage.xhtml", titlepage)
        zf.writestr("nav.xhtml", nav)
    return buf.getvalue()


def test_same_fandom_gets_same_colour():
    star_wars = CoverInfo(title="A", author="B", fandom="Star Wars - All Media Types")
    other_sw = CoverInfo(title="C", author="D", fandom="Star Wars")
    trek = CoverInfo(title="E", author="F", fandom="Star Trek")
    settings = CoverSettings()
    sw1 = choose_colours(star_wars, settings)
    sw2 = choose_colours(other_sw, settings)
    st = choose_colours(trek, settings)
    assert sw1 == sw2
    assert sw1 != st


def test_fandom_color_override_and_solid_mode():
    info = CoverInfo(title="A", author="B", fandom="Harry Potter - J. K. Rowling")
    mapped = CoverSettings(fandom_colors={"Harry Potter": "#740001"}, gradient=False)
    top, bottom = choose_colours(info, mapped)
    assert top.lower() == "#740001"
    assert bottom.lower() == "#740001"
    solid = CoverSettings(color_mode="solid", solid_color="#112233", gradient=False)
    assert choose_colours(info, solid) == ("#112233", "#112233")


def test_cover_info_from_record_and_epub(tmp_path: Path):
    record = {
        "work_id": "1",
        "title": "Record Title",
        "author": "Record Author",
        "fandoms": ["Doctor Who (2005)"],
        "relationships": ["Tenth Doctor/Rose Tyler"],
        "metadata": {
            "words": 1234,
            "chapters": {"is_complete": True},
            "kudos": 1321,
            "hits": 53450,
            "quality_score": 62,
        },
        "series": [{"series_id": "9", "name": "A Series", "url": "", "position": 2}],
        "tags": ["Teen And Up Audiences"],
    }
    info = cover_info_from_record(record)
    assert info.fandom == "Doctor Who (2005)"
    assert info.relationship == "Tenth Doctor/Rose Tyler"
    assert info.wordcount == 1234
    assert info.score == 62
    assert info.complete is True
    assert info.series == "A Series #2"
    assert info.rating == "Teen And Up Audiences"
    string_words = cover_info_from_record({"title": "A", "metadata": {"words": "12,345"}})
    assert string_words.wordcount == 12345

    epub = tmp_path / "work.epub"
    epub.write_bytes(ao3_epub_bytes())
    from_file = cover_info_from_epub(epub)
    assert from_file.title == "Operation Cameo"
    assert from_file.author == "alexwlchan"
    assert "Operation Mincemeat" in from_file.fandom
    assert from_file.wordcount == 12000
    assert from_file.score is not None
    assert from_file.score > 0
    merged = merge_cover_info(info, from_file)
    assert merged.title == "Record Title"
    assert merged.fandom == "Doctor Who (2005)"


def test_inject_cover_marks_opf_and_adds_image(tmp_path: Path):
    epub = tmp_path / "work.epub"
    epub.write_bytes(ao3_epub_bytes())
    assert epub_has_cover(epub) is False
    outcome = apply_cover_to_epub(epub, record={"title": "Operation Cameo"})
    assert outcome.status == "updated"
    assert epub_has_cover(epub) is True
    image = extract_cover_bytes(epub)
    assert image is not None and image[:8] == b"\x89PNG\r\n\x1a\n"
    with ZipFile(epub) as zf:
        names = zf.namelist()
        assert "mimetype" in names
        opf = zf.read("content.opf").decode("utf-8")
        assert "cover.xhtml" in zf.namelist()
    assert 'name="cover"' in opf
    assert "cover-image" in opf
    assert "media/cover.png" in opf
    assert "cover.xhtml" in opf
    assert 'idref="ao3-cover"' in opf
    after = cover_info_from_epub(epub)
    assert "Operation Mincemeat" in after.fandom


def test_skip_existing_cover_unless_replace(tmp_path: Path):
    epub = tmp_path / "work.epub"
    epub.write_bytes(ao3_epub_bytes())
    first = render_cover_image(CoverInfo(title="One", author="A", fandom="Star Wars"))
    inject_cover(epub, first, CoverSettings(replace_existing=True))
    original = extract_cover_bytes(epub)
    second = render_cover_image(CoverInfo(title="Two", author="B", fandom="Star Trek"))
    inject_cover(epub, second, CoverSettings(replace_existing=False))
    assert extract_cover_bytes(epub) == original
    inject_cover(epub, second, CoverSettings(replace_existing=True))
    assert extract_cover_bytes(epub) == second


def test_cover_cli_ignores_login_flags(tmp_path: Path):
    dest = tmp_path / "cover.png"
    rc = cover_main(
        [
            "--preview",
            "--title",
            "Ship Happens",
            "--author",
            "Ann Thology",
            "--fandom",
            "Star Wars",
            "-o",
            str(dest),
            "--username",
            "emily",
            "--password",
            "secret",
        ]
    )
    assert rc == 0
    assert dest.is_file()


def test_preview_cli_writes_png(tmp_path: Path):
    dest = tmp_path / "cover.png"
    rc = cover_main(
        [
            "--preview",
            "--title",
            "Ship Happens",
            "--author",
            "Ann Thology",
            "--fandom",
            "Star Wars",
            "-o",
            str(dest),
        ]
    )
    assert rc == 0
    assert dest.is_file()
    assert dest.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_cover_cli_stamps_dir(tmp_path: Path):
    epub = tmp_path / "50448730.epub"
    epub.write_bytes(ao3_epub_bytes(title="Clandestine", author="Writer"))
    rc = cover_main(["--dir", str(tmp_path), "--verbose"])
    assert rc == 0
    assert epub_has_cover(epub)


def test_cover_settings_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AO3KIT_HOME", str(home))
    cfg = init_user_config(home=home)
    cfg.update_settings(
        cover=CoverSettings(
            enabled=False,
            fields=["title", "author", "fandom"],
            width=800,
            fandom_colors={"Star Wars": "#c41e3a"},
        )
    )
    reloaded = load_user_config(home=home)
    assert reloaded.settings.cover.enabled is False
    assert reloaded.settings.cover.width == 800
    assert reloaded.settings.cover.shows("fandom")
    assert reloaded.settings.cover.fandom_colors["Star Wars"] == "#c41e3a"


def test_user_settings_cover_defaults_when_absent():
    settings = UserSettings.from_dict({"request_delay": 3, "nope": 1})
    assert settings.request_delay == 3.0
    assert settings.cover.enabled is True
    assert settings.cover.fields == ["title", "author", "wordcount", "score"]


def test_cover_footer_includes_wordcount():
    info = CoverInfo(title="A", author="B", wordcount=344429, score=62)
    assert _format_footer(info, CoverSettings()) == ["344,429 words", "Score 62"]
    hidden = CoverSettings(fields=["title", "author"])
    assert _format_footer(info, hidden) == []
    raw = CoverInfo(score=13.4)
    assert _format_footer(raw, CoverSettings(fields=["score"])) == ["Score 13.4"]
