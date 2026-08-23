"""HTML parsing with lxml when available, html.parser otherwise.

Calibre ships lxml. The GitHub plugin zip is pure Python so a system
Python fallback (no lxml) still works.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, FeatureNotFound

_PARSER = ""


def html_parser() -> str:
    """Parser name BeautifulSoup should use in this process."""
    global _PARSER
    if _PARSER:
        return _PARSER
    try:
        BeautifulSoup("", "lxml")
        _PARSER = "lxml"
    except FeatureNotFound:
        _PARSER = "html.parser"
    return _PARSER


def parse_html(markup: str | bytes) -> BeautifulSoup:
    return BeautifulSoup(markup, html_parser())
