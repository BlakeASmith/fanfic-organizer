"""Unified CLI for ao3kit.

Usage:
  python -m ao3kit scrape ...
  python -m ao3kit tags ...
  python -m ao3kit download ...
  python -m ao3kit config ...
  python -m ao3kit serve   # web UI + REST API (/api/v1, docs at /api/v1/docs)
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(
        prog="ao3kit",
        description="AO3 scrape, tag wrangling, EPUB download, web UI, and REST API.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scrape", help="Scrape AO3 search results to JSONL")
    sub.add_parser("tags", help="Tag profiles, search, resolve, tag sets")
    sub.add_parser("download", help="Download EPUBs from JSONL results")
    sub.add_parser("config", help="User settings and rule files (.ao3kit/)")
    serve_p = sub.add_parser(
        "serve",
        help="Run the FastAPI web UI and REST API (/api/v1; docs at /api/v1/docs)",
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

    if command == "config":
        from ao3kit.config_cli import main as config_main

        return config_main(rest)

    if command == "serve":
        serve_ns = serve_p.parse_args(rest)
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
