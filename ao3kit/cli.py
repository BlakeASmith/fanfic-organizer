"""Unified CLI for ao3kit.

Usage:
  python -m ao3kit scrape ...
  python -m ao3kit tags ...
  python -m ao3kit download ...
  python -m ao3kit identify ...
  python -m ao3kit cover ...
  python -m ao3kit job ...
  python -m ao3kit config ...
  python -m ao3kit login    # test AO3 username/password
  python -m ao3kit rate     # limiter snapshot + AO3 request log
  python -m ao3kit library  # estimate unmatched tags / missing EPUBs from JSONL
  python -m ao3kit wikipedia  # search/fetch Wikipedia articles to JSONL
  python -m ao3kit web        # fetch URL or saved HTML → JSONL/EPUB
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(
        prog="ao3kit",
        description="AO3 scrape, tag wrangling, EPUB download, and Calibre plugin toolkit.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser(
        "scrape",
        help="Scrape AO3 search results to JSONL (optionally download EPUBs)",
    )
    sub.add_parser(
        "wikipedia",
        help="Search or fetch Wikipedia articles to JSONL (source=wikipedia)",
    )
    sub.add_parser(
        "web",
        help="Fetch a URL or saved HTML to JSONL/EPUB (source=web; best-effort)",
    )
    sub.add_parser(
        "webcompile",
        help="Compile linked web pages into one EPUB (delegates to webcompile package)",
    )
    sub.add_parser(
        "tags",
        help="Tag profiles, search, resolve, suggest, tag sets, graph, background cache",
    )
    sub.add_parser("download", help="Download EPUBs from JSONL results")
    sub.add_parser(
        "identify",
        help="Identify AO3 works from URLs, EPUBs, or title + author",
    )
    sub.add_parser(
        "cover",
        help="Generate AO3-style covers and stamp them into EPUBs",
    )
    sub.add_parser(
        "job",
        help="Background jobs: start, list, status, log, attach, stop, retry, delete, clear",
    )
    sub.add_parser("config", help="User settings and rule files (XDG config dir)")
    sub.add_parser("login", help="Test AO3 username and password")
    sub.add_parser(
        "rate",
        help="Host-wide AO3 rate-limit snapshot and collected request log",
    )
    sub.add_parser(
        "library",
        help="Whole-library helpers (estimate unmatched tags / missing EPUBs from JSONL)",
    )
    sub.add_parser(
        "koreader",
        help="KOReader collections index helpers",
    )

    if not argv:
        parser.print_help()
        return 0

    command = argv[0]
    rest = argv[1:]

    if command in {"-h", "--help"}:
        parser.print_help()
        return 0

    if command == "scrape":
        from ao3kit.scrape import main as scrape_main

        return scrape_main(rest)

    if command == "wikipedia":
        from ao3kit.sources.wikipedia import main as wikipedia_main

        return wikipedia_main(rest)

    if command == "web":
        from ao3kit.sources.web import main as web_main

        return web_main(rest)

    if command == "webcompile":
        from webcompile.cli import main as webcompile_main

        return webcompile_main(rest)

    if command == "tags":
        from ao3kit.tags.metadata import main as tags_main

        return tags_main(rest)

    if command == "download":
        from ao3kit.epubs import main as epubs_main

        return epubs_main(rest)

    if command == "identify":
        from ao3kit.identify import main as identify_main

        return identify_main(rest)

    if command == "cover":
        from ao3kit.covers import main as cover_main

        return cover_main(rest)

    if command == "job":
        from ao3kit.jobs import main as job_main

        return job_main(rest)

    if command == "config":
        from ao3kit.config_cli import main as config_main

        return config_main(rest)

    if command == "login":
        from ao3kit.http import login_main

        return login_main(rest)

    if command == "rate":
        from ao3kit.rate import main as rate_main

        return rate_main(rest)

    if command == "library":
        from ao3kit.library import main as library_main

        return library_main(rest)

    if command == "koreader":
        from ao3kit.koreader.cli import main as koreader_main

        return koreader_main(rest)

    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
