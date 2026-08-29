"""CLI helpers for KOReader collections deploy."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ao3kit.epubs import load_jsonl_records
from ao3kit.koreader.deploy import atomic_write_json, build_collections_index_from_rows


def _collections_from_record(record: dict[str, Any]) -> list[str]:
    cleaned = record.get("cleaned") or {}
    raw = cleaned.get("collections")
    if isinstance(raw, dict):
        return [str(name) for name in raw.keys() if str(name).strip()]
    if isinstance(raw, list):
        return [str(name) for name in raw if str(name).strip()]
    direct = record.get("collections")
    if isinstance(direct, list):
        return [str(name) for name in direct if str(name).strip()]
    return []


def records_to_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        lpath = str(record.get("lpath") or "").strip()
        if not lpath:
            continue
        row: dict[str, Any] = {
            "lpath": lpath,
            "collections": _collections_from_record(record),
        }
        title = record.get("title")
        if title:
            row["title"] = str(title)
        authors = record.get("authors")
        if authors:
            row["authors"] = [str(author) for author in authors if str(author).strip()]
        rows.append(row)
    return rows


def build_index_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ao3kit koreader build-index",
        description="Build fanfic.collections.json from JSONL work records.",
    )
    parser.add_argument(
        "jsonl",
        help="Input JSONL with lpath and cleaned.collections (or collections)",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output path for fanfic.collections.json",
    )
    args = parser.parse_args(argv)
    records = load_jsonl_records(args.jsonl)
    entries = build_collections_index_from_rows(records_to_rows(records))
    output = Path(args.output)
    atomic_write_json(output, entries)
    sys.stderr.write(f"Wrote {len(entries)} book(s) to {output}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="ao3kit koreader")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser(
        "build-index",
        help="Build fanfic.collections.json from JSONL rows with collections",
    )
    if not argv or argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    command = argv[0]
    rest = argv[1:]
    if command == "build-index":
        return build_index_command(rest)
    parser.error(f"Unknown koreader command: {command}")
    return 2
