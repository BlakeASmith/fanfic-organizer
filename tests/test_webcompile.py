"""Tests for ao3kit.webcompile (multi-page crawl → EPUB)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ao3kit.webcompile.bundle import load_bundle, write_bundle
from ao3kit.webcompile.crawl import (
    extract_links,
    link_allowed,
)
from ao3kit.webcompile.epub import write_compiled_epub
from ao3kit.webcompile.models import (
    CrawledPage,
    ExpandMode,
)
from ao3kit.webcompile.pipeline import compile_html_files
from ao3kit.webcompile.preprocess import preprocess_pages, rewrite_internal_links
from ao3kit.webcompile.userscript import resolve_userscript


def _article_html(title: str, body: str, links: list[tuple[str, str]] | None = None) -> str:
    anchors = ""
    for href, text in links or []:
        anchors += f'<p><a href="{href}">{text}</a></p>'
    return f"""<!DOCTYPE html>
<html lang="en"><head><title>{title}</title>
<meta name="author" content="Tester"/>
<meta property="og:description" content="A summary."/>
</head>
<body>
<nav><a href="/">Home</a></nav>
<article>
<h1>{title}</h1>
<p>{body}</p>
{anchors}
</article>
</body></html>
"""


def test_extract_links_resolves_relative():
    html = _article_html(
        "One",
        "x " * 80,
        [("/two", "Two"), ("https://other.example/x", "Other"), ("#frag", "Frag")],
    )
    links = extract_links(html, base_url="https://example.com/one")
    assert "https://example.com/two" in links
    assert "https://other.example/x" in links
    assert all("#" not in u for u in links)


def test_link_allowed_modes():
    seeds = {"example.com"}
    assert link_allowed(
        "https://example.com/a",
        expand=ExpandMode.SAME_DOMAIN,
        seed_hosts=seeds,
        allowed_domains=[],
    )
    assert not link_allowed(
        "https://other.com/a",
        expand=ExpandMode.SAME_DOMAIN,
        seed_hosts=seeds,
        allowed_domains=[],
    )
    assert link_allowed(
        "https://docs.example.com/a",
        expand=ExpandMode.DOMAINS,
        seed_hosts=seeds,
        allowed_domains=["example.com"],
    )
    assert link_allowed(
        "https://anywhere.test/z",
        expand=ExpandMode.FREE,
        seed_hosts=seeds,
        allowed_domains=[],
    )
    assert not link_allowed(
        "https://example.com/a",
        expand=ExpandMode.NONE,
        seed_hosts=seeds,
        allowed_domains=[],
    )


def test_rewrite_internal_links():
    url_map = {
        "https://example.com/one": "chapter-001.xhtml",
        "https://example.com/two": "chapter-002.xhtml",
    }
    body = (
        '<p><a href="https://example.com/two#sec">Two</a></p>'
        '<p><a href="https://external.test/x">Out</a></p>'
        '<p><a href="#local">Local</a></p>'
    )
    out = rewrite_internal_links(
        body, base_url="https://example.com/one", url_map=url_map
    )
    assert 'href="chapter-002.xhtml#sec"' in out
    assert "https://external.test/x" in out
    assert 'href="#local"' in out


def test_preprocess_and_epub(tmp_path: Path):
    pages = [
        CrawledPage(
            url="https://example.com/one",
            final_url="https://example.com/one",
            html=_article_html(
                "Chapter One",
                ("hello world " * 40),
                [("/two", "Next")],
            ),
            title="Chapter One",
        ),
        CrawledPage(
            url="https://example.com/two",
            final_url="https://example.com/two",
            html=_article_html(
                "Chapter Two",
                ("second page text " * 40),
                [("/one", "Back")],
            ),
            title="Chapter Two",
        ),
    ]
    chapters = preprocess_pages(pages)
    assert len(chapters) == 2
    assert "chapter-002.xhtml" in chapters[0].html_body
    assert chapters[0].chapter_href == "chapter-001.xhtml"

    epub = tmp_path / "book.epub"
    write_compiled_epub(
        epub,
        chapters,
        title="Demo Book",
        author="Tester",
        work_id="demo123",
    )
    assert epub.is_file()
    with zipfile.ZipFile(epub) as zf:
        names = set(zf.namelist())
        assert "OEBPS/nav.xhtml" in names
        assert "OEBPS/chapter-001.xhtml" in names
        assert "OEBPS/chapter-002.xhtml" in names
        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
        assert "Chapter One" in nav
        assert "Chapter Two" in nav
        ch1 = zf.read("OEBPS/chapter-001.xhtml").decode("utf-8")
        assert "chapter-002.xhtml" in ch1


def test_compile_html_files_pipeline(tmp_path: Path):
    p1 = tmp_path / "a.html"
    p2 = tmp_path / "b.html"
    p1.write_text(
        _article_html("A", "alpha " * 50, [("b.html", "B")]),
        encoding="utf-8",
    )
    p2.write_text(
        _article_html("B", "bravo " * 50),
        encoding="utf-8",
    )
    result = compile_html_files(
        [p1, p2],
        dest_dir=tmp_path / "out",
        urls=["https://example.com/a", "https://example.com/b"],
        title="AB",
        cover=False,
    )
    assert not result.errors
    assert len(result.chapters) == 2
    assert result.record["title"] == "AB"
    assert result.record["web_compile"] is True
    assert result.record["page_count"] == 2
    assert result.record.get("epub_file")
    assert Path(result.epub_path).is_file()


def test_bundle_roundtrip(tmp_path: Path):
    pages = [
        CrawledPage(
            url="https://example.com/a",
            final_url="https://example.com/a",
            html=_article_html("A", "word " * 60),
            title="A",
            source="bundle",
        ),
        CrawledPage(
            url="https://example.com/b",
            final_url="https://example.com/b",
            html=_article_html("B", "more " * 60),
            title="B",
            source="bundle",
        ),
    ]
    bundle_path = tmp_path / "bundle.json"
    write_bundle(bundle_path, pages, title="From Bundle", seed_url=pages[0].url)
    data = load_bundle(bundle_path)
    assert data["title"] == "From Bundle"
    assert len(data["pages"]) == 2

    from ao3kit.webcompile.pipeline import compile_bundle_file

    result = compile_bundle_file(
        bundle_path, dest_dir=tmp_path / "out", cover=False
    )
    assert result.record["title"] == "From Bundle"
    assert len(result.chapters) == 2
    assert Path(result.epub_path).is_file()


def test_resolve_userscript():
    path = resolve_userscript()
    assert path is not None
    assert path.name.endswith(".user.js")
    text = path.read_text(encoding="utf-8")
    assert "fanfic-organizer-webcompile" in text
    assert "@grant" in text


def test_prepare_webcompile_command(tmp_path: Path):
    from sources.web.run import describe_web, prepare_web_command, web_import_is_usable

    assert web_import_is_usable(
        {"mode": "compile", "seeds": ["https://example.com/a"]}
    )
    argv, jsonl = prepare_web_command(
        {
            "mode": "compile",
            "seeds": ["https://example.com/a", "https://example.com/b"],
            "full_list": False,
            "expand": "same_domain",
            "max_pages": 10,
            "max_depth": 1,
            "book_title": "Demo",
            "download_epubs": True,
        },
        tmp_path / "work",
    )
    assert argv[0] == "webcompile"
    assert "--seed" in argv
    assert "--expand" in argv
    assert argv[argv.index("--expand") + 1] == "same_domain"
    assert "--title" in argv
    assert describe_web({"mode": "compile", "book_title": "Demo"}).startswith(
        "Web compile"
    )

    bundle = tmp_path / "b.json"
    write_bundle(
        bundle,
        [
            CrawledPage(
                url="https://example.com/x",
                final_url="https://example.com/x",
                html="<html><body><article><p>" + ("z " * 80) + "</p></article></body></html>",
                title="X",
            )
        ],
    )
    argv2, _ = prepare_web_command(
        {"mode": "compile", "bundle_path": str(bundle), "download_epubs": True},
        tmp_path / "work2",
    )
    assert "--bundle" in argv2
    assert (tmp_path / "work2" / "bundle.json").is_file()


def test_plan_webcompile(tmp_path: Path):
    from sources.web.plan import plan_web

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    spec = plan_web(
        {
            "mode": "compile",
            "seeds": ["https://example.com/seed"],
            "expand": "none",
            "full_list": True,
            "download_epubs": True,
        },
        job_dir,
    )
    assert spec["kind"] == "webcompile"
    assert spec["steps"][0][0] == "webcompile"
    assert spec["result"]["label"] == "book"


def test_cli_html_compile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from ao3kit.webcompile import cli as webcompile_cli

    p1 = tmp_path / "1.html"
    p2 = tmp_path / "2.html"
    p1.write_text(_article_html("One", "one " * 50), encoding="utf-8")
    p2.write_text(_article_html("Two", "two " * 50), encoding="utf-8")
    out = tmp_path / "results.jsonl"
    code = webcompile_cli.main(
        [
            "--html",
            str(p1),
            "--html",
            str(p2),
            "--title",
            "CLI Book",
            "--no-cover",
            "--output",
            str(out),
            "--epub-dir",
            str(tmp_path),
        ]
    )
    assert code == 0
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["title"] == "CLI Book"
    assert record["page_count"] == 2
    assert (tmp_path / record["epub_file"]).is_file()


def test_plugin_zip_includes_userscript():
    from makeplugin import iter_zip_entries

    names = {arc for _path, arc in iter_zip_entries(vendor_dir=None)}
    assert "resources/webcompile/fanfic-organizer-webcompile.user.js" in names
    assert any(n.startswith("ao3kit/webcompile/") for n in names)
