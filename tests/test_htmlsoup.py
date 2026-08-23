"""HTML parser fallback when lxml is missing."""

from __future__ import annotations

from ao3kit.htmlsoup import html_parser, parse_html


def test_parse_html_returns_soup():
    soup = parse_html("<html><body><p>Hi</p></body></html>")
    assert soup.find("p").get_text() == "Hi"
    assert html_parser() in {"lxml", "html.parser"}
