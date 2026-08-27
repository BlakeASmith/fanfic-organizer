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
    contrast_ratio,
    cover_info_from_epub,
    cover_info_from_record,
    ensure_contrast,
    epub_has_cover,
    extract_cover_bytes,
    inject_cover,
    main as cover_main,
    merge_cover_info,
    parse_color,
    plan_cover_layout,
    render_cover_image,
    resolve_font,
    wrap_title,
    _format_footer,
    _scratch_draw,
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
    mapped = CoverSettings(
        fandom_colors={"Harry Potter": "#740001"},
        gradient=False,
        auto_contrast=False,
    )
    top, bottom = choose_colours(info, mapped)
    assert top.lower() == "#740001"
    assert bottom.lower() == "#740001"
    solid = CoverSettings(
        color_mode="solid",
        solid_color="#112233",
        gradient=False,
        auto_contrast=False,
    )
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
            "quality_score_raw": 13.6,
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
    assert from_file.score == 100
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
    settings = UserSettings.from_dict({"nope": 1})
    assert settings.cover.enabled is True
    assert settings.cover.fields == ["title", "author", "wordcount", "score"]


def test_cover_footer_includes_wordcount():
    info = CoverInfo(title="A", author="B", wordcount=344429, score=62)
    assert _format_footer(info, CoverSettings()) == ["344,429 words", "Score 62"]
    hidden = CoverSettings(fields=["title", "author"])
    assert _format_footer(info, hidden) == []
    raw = CoverInfo(score=62)
    assert _format_footer(raw, CoverSettings(fields=["score"])) == ["Score 62"]


def test_long_title_auto_fits_without_ellipsis():
    info = CoverInfo(
        title=(
            "The One Where They All Get Together in a Coffee Shop "
            "and Save the Galaxy (Again)"
        ),
        author="Jane AUs-ten",
        fandom="Star Wars",
        wordcount=125000,
        score=72,
    )
    title, author, _footer, _headers = plan_cover_layout(info, CoverSettings())
    assert title is not None
    assert author is not None
    joined = " ".join(title.lines)
    assert "…" not in joined
    assert "Galaxy" in joined
    assert title.size < 88
    assert title.line_height <= title.size * 1.2
    assert title.bottom <= author.y - 8


def test_short_title_keeps_large_type():
    info = CoverInfo(title="Cameo", author="A", fandom="Star Wars")
    title, _author, _footer, _headers = plan_cover_layout(info, CoverSettings())
    assert title is not None
    assert title.size == 88
    assert len(title.lines) == 1


EXTREME_TITLE = (
    "in which there is a coffee shop, a time loop, three (3) fake dating "
    "contracts, one (1) accidental marriage, the inherent eroticism of sharing "
    "a tiny apartment, found family, identity porn, amnesia, a missing prince, "
    "a talking sword, five soulmate tropes in a trench coat, and they were "
    "roommates (oh my god they were roommates), or: a treatise on why the "
    "author should have stopped adding subtitles around chapter forty-seven "
    "but absolutely did not"
)


def test_extreme_title_uses_the_cover_instead_of_ellipsis():
    info = CoverInfo(
        title=EXTREME_TITLE,
        author="Jane AUs-ten",
        fandom="Star Wars - All Media Types",
        wordcount=344429,
        score=62,
    )
    title, author, _footer, _headers = plan_cover_layout(info, CoverSettings())
    assert title is not None
    assert author is not None
    joined = " ".join(title.lines)
    assert "…" not in joined
    assert "forty-seven" in joined
    assert "did not" in joined
    assert title.size <= 40
    assert len(title.lines) > 8
    assert title.bottom <= author.y - 8


def test_title_max_lines_still_truncates_when_set():
    info = CoverInfo(title=EXTREME_TITLE, author="A")
    title, _author, _footer, _headers = plan_cover_layout(
        info, CoverSettings(title_max_lines=5, auto_fit_title=True)
    )
    assert title is not None
    assert len(title.lines) <= 5
    assert title.lines[-1].endswith("…")


def test_long_unbroken_word_wraps():
    draw = _scratch_draw(600, 900)
    font = resolve_font(CoverSettings(), 88)
    lines = wrap_title(draw, font, "Supercalifragilisticexpialidocious", 180)
    assert len(lines) > 1
    assert all(line for line in lines)


def test_auto_contrast_darkens_bright_mapped_color():
    info = CoverInfo(title="Gold Hour", author="Sunny", fandom="Yellow Fandom")
    settings = CoverSettings(
        fandom_colors={"Yellow Fandom": "#e8c44a"},
        gradient=False,
        auto_contrast=True,
        contrast_min_ratio=3.5,
    )
    top, _bottom = choose_colours(info, settings)
    assert top.lower() != "#e8c44a"
    rgb = parse_color(top)[:3]
    assert contrast_ratio(rgb, (255, 255, 255)) >= 3.5


def test_ensure_contrast_leaves_dark_colors_alone():
    assert ensure_contrast("#740001", min_ratio=3.5).lower() == "#740001"


def test_cover_settings_new_defaults():
    settings = CoverSettings()
    assert settings.auto_fit_title is True
    assert settings.auto_contrast is True
    assert settings.text_shadow is True
    assert settings.text_stroke_px == 3
    assert settings.title_leading == 1.08
    assert settings.scrim == 0.22


def test_settings_json_cli_overrides_preview(tmp_path: Path):
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
            "--settings-json",
            '{"width": 400, "height": 600, "scrim": 0.4, "text_stroke_px": 0}',
            "-o",
            str(dest),
        ]
    )
    assert rc == 0
    assert dest.is_file()
    from PIL import Image

    image = Image.open(dest)
    assert image.size == (400, 600)
