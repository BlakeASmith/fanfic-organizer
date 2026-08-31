"""CLI: ``python -m ao3kit webcompile`` — multi-page web → unified EPUB."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ao3kit.sources.web import write_jsonl
from ao3kit.webcompile.models import CrawlOptions, ExpandMode
from ao3kit.webcompile.pipeline import (
    compile_bundle_file,
    compile_html_files,
    compile_pages,
    parse_expand_mode,
)
from ao3kit.webcompile.userscript import resolve_userscript


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ao3kit webcompile",
        description=(
            "Compile multiple linked web pages into one EPUB with a TOC. "
            "Phase 1 crawls seeds (or uses an explicit URL list / Tampermonkey "
            "bundle); phase 2 extracts content and rewrites in-book links; "
            "phase 3 writes a unified EPUB."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output JSONL path (default: stdout)",
    )
    parser.add_argument(
        "--seed",
        action="append",
        default=None,
        help="Seed URL to crawl (repeatable). Links are expanded per --expand.",
    )
    parser.add_argument(
        "--url",
        action="append",
        default=None,
        help="Explicit page URL (repeatable). When set, skips link expansion.",
    )
    parser.add_argument(
        "--html",
        action="append",
        default=None,
        dest="html_files",
        help="Local HTML file to include as a chapter (repeatable, ordered)",
    )
    parser.add_argument(
        "--bundle",
        help="Tampermonkey / exported crawl bundle JSON (skips network crawl)",
    )
    parser.add_argument(
        "--expand",
        default="same_domain",
        help="Link expansion for seeds: none, same_domain, domains, free "
        "(default: same_domain)",
    )
    parser.add_argument(
        "--domain",
        action="append",
        default=None,
        dest="domains",
        help="Allowed domain when --expand=domains (repeatable)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="Maximum pages to collect (default: 50)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=2,
        help="Maximum link depth from seeds (default: 2)",
    )
    parser.add_argument("--title", default="", help="Book title override")
    parser.add_argument("--author", default="", help="Book author override")
    parser.add_argument(
        "--language",
        default="en",
        help="EPUB language code (default: en)",
    )
    parser.add_argument(
        "--epub-dir",
        help="Directory for epubs/ (default: same dir as --output, or cwd)",
    )
    parser.add_argument(
        "--cover",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stamp a generated cover (default: on)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Progress messages on stderr",
    )
    parser.add_argument(
        "--print-userscript",
        action="store_true",
        help="Print the companion Tampermonkey script path and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.print_userscript:
        path = resolve_userscript()
        if path is None:
            print("error: Tampermonkey userscript not found", file=sys.stderr)
            return 1
        print(path)
        return 0

    seeds = [str(u).strip() for u in (args.seed or []) if str(u).strip()]
    urls = [str(u).strip() for u in (args.url or []) if str(u).strip()]
    html_files = [str(p).strip() for p in (args.html_files or []) if str(p).strip()]
    bundle = str(args.bundle or "").strip()

    if not seeds and not urls and not html_files and not bundle:
        parser.error("Provide --seed, --url, --html, and/or --bundle")

    try:
        expand = parse_expand_mode(args.expand)
    except ValueError as exc:
        parser.error(str(exc))

    if expand == ExpandMode.DOMAINS and not (args.domains or []):
        parser.error("--expand=domains requires at least one --domain")

    on_status = (lambda msg: print(msg, file=sys.stderr)) if args.verbose else None
    print(
        "warning: static crawl does not run JavaScript. For dynamic sites, use "
        "the Tampermonkey companion (--print-userscript) and pass --bundle.",
        file=sys.stderr,
    )

    if args.epub_dir:
        epub_root = Path(args.epub_dir)
    elif args.output:
        epub_root = Path(args.output).resolve().parent
    else:
        epub_root = Path.cwd()

    try:
        if bundle:
            result = compile_bundle_file(
                bundle,
                dest_dir=epub_root,
                title=args.title,
                author=args.author,
                language=args.language,
                cover=bool(args.cover),
                on_status=on_status,
            )
        elif html_files and not seeds and not urls:
            result = compile_html_files(
                html_files,
                dest_dir=epub_root,
                title=args.title,
                author=args.author,
                language=args.language,
                cover=bool(args.cover),
                on_status=on_status,
            )
        else:
            # Explicit --url list wins over seed expansion.
            crawl = CrawlOptions(
                seeds=seeds,
                urls=urls,
                expand=ExpandMode.NONE if urls else expand,
                allowed_domains=list(args.domains or []),
                max_pages=max(1, int(args.max_pages)),
                max_depth=max(0, int(args.max_depth)),
            )
            from ao3kit.webcompile.models import CompileOptions

            result = compile_pages(
                CompileOptions(
                    crawl=crawl,
                    title=args.title,
                    author=args.author,
                    language=args.language,
                    cover=bool(args.cover),
                ),
                dest_dir=epub_root,
                on_status=on_status,
            )
            # Optional HTML files appended? Keep simple: if both, prefer network
            # path only. Users can use --bundle for mixed offline.
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for err in result.errors:
        print(f"error: {err}", file=sys.stderr)
    for warn in result.warnings:
        if args.verbose:
            print(f"warning: {warn}", file=sys.stderr)

    if not result.record or not result.chapters:
        return 1

    records = [result.record]
    if args.output:
        write_jsonl(records, args.output)
    else:
        write_jsonl(records, sys.stdout)

    print(
        f"Compiled {len(result.chapters)} page(s) → "
        f"{result.record.get('title') or 'book'}",
        file=sys.stderr,
    )
    if result.epub_path:
        print(f"EPUB: {result.epub_path}", file=sys.stderr)
    elif result.record.get("epub_error"):
        print(f"EPUB failed: {result.record['epub_error']}", file=sys.stderr)
        return 1
    return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
