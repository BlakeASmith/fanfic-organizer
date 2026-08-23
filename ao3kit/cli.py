"""Unified CLI for ao3kit.

Usage:
  python -m ao3kit scrape ...
  python -m ao3kit tags ...
  python -m ao3kit download ...
  python -m ao3kit cover ...
  python -m ao3kit job ...
  python -m ao3kit config ...
  python -m ao3kit login    # test AO3 username/password
  python -m ao3kit rate     # limiter snapshot + AO3 request log
  python -m ao3kit serve    # deprecated: frozen web UI + REST API
"""

from __future__ import annotations

import argparse
import sys
import warnings

from ao3kit import DEPRECATED_WEB_AND_API


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
        "tags",
        help="Tag profiles, search, resolve, tag sets, graph, background cache",
    )
    sub.add_parser("download", help="Download EPUBs from JSONL results")
    sub.add_parser(
        "cover",
        help="Generate AO3-style covers and stamp them into EPUBs",
    )
    sub.add_parser(
        "job",
        help="Background jobs: start, list, status, log, attach, stop, retry, delete, clear",
    )
    sub.add_parser("config", help="User settings and rule files (.ao3kit/)")
    sub.add_parser("login", help="Test AO3 username and password")
    sub.add_parser(
        "rate",
        help="Host-wide AO3 rate-limit snapshot and collected request log",
    )
    serve_p = sub.add_parser(
        "serve",
        help="Deprecated: frozen web UI and REST API (use CLI or Calibre plugin)",
        description=DEPRECATED_WEB_AND_API,
    )
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8000)
    serve_p.add_argument("--reload", action="store_true")

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

    if command == "tags":
        from ao3kit.tags.metadata import main as tags_main

        return tags_main(rest)

    if command == "download":
        from ao3kit.epubs import main as epubs_main

        return epubs_main(rest)

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

    if command == "serve":
        serve_ns = serve_p.parse_args(rest)
        print(f"warning: {DEPRECATED_WEB_AND_API}", file=sys.stderr)
        warnings.warn(DEPRECATED_WEB_AND_API, DeprecationWarning, stacklevel=2)
        import uvicorn

        uvicorn.run(
            "ao3kit.webapp:app",
            host=serve_ns.host,
            port=serve_ns.port,
            reload=serve_ns.reload,
        )
        return 0

    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
