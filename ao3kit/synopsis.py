"""Repair AO3 synopsis fields for Kobo / device sync (library + JSONL)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ao3kit.covers import (
    comments_for_import_synopsis,
    resolve_record_summary,
    summary_text_from_comments,
)
from ao3kit.epubs import load_jsonl_records


def classify_synopsis(
    comments: Any,
    summary: Any = None,
    *,
    work_id: str = "",
) -> str:
    """Return ``ok``, ``needs_local``, ``needs_fetch``, or ``no_source``."""
    text = resolve_record_summary(
        {},
        summary_column=summary,
        comments=comments,
    )
    if text and not summary_text_from_comments(comments):
        return "needs_local"
    if text:
        return "ok"
    if str(work_id or "").strip():
        return "needs_fetch"
    return "no_source"


def repair_record_synopsis(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``{comments, summary}`` updates, or ``None`` if nothing to change."""
    comments = record.get("comments")
    summary = record.get("summary")
    text = resolve_record_summary(record, comments=comments)
    if not text:
        return None
    updates: dict[str, Any] = {}
    new_comments = comments_for_import_synopsis(text, comments)
    if new_comments is not None and new_comments != str(comments or ""):
        updates["comments"] = new_comments
    if not str(summary or "").strip():
        updates["summary"] = text
    return updates or None


def count_synopsis_states(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"ok": 0, "needs_local": 0, "needs_fetch": 0, "no_source": 0}
    for row in records:
        work_id = str(row.get("work_id") or "").strip()
        state = classify_synopsis(
            row.get("comments"),
            row.get("summary"),
            work_id=work_id,
        )
        counts[state] = counts.get(state, 0) + 1
    return counts


def repair_jsonl_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Apply local synopsis repair to each row. Returns (records, updated_count)."""
    updated = 0
    out: list[dict[str, Any]] = []
    for row in records:
        patch = repair_record_synopsis(row)
        if patch:
            merged = dict(row)
            merged.update(patch)
            out.append(merged)
            updated += 1
        else:
            out.append(row)
    return out, updated


def _add_count_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "count",
        help="Count synopsis repair states in JSONL (no writes)",
    )
    parser.add_argument("--jsonl", "-i", required=True)


def _add_repair_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "repair-jsonl",
        help="Copy Comments from #summary / record summary where Comments is empty or JSON",
    )
    parser.add_argument("--jsonl", "-i", required=True)
    parser.add_argument(
        "--out",
        "-o",
        help="Output JSONL (default: overwrite --jsonl)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ao3kit synopsis",
        description=(
            "Repair device synopsis fields on work records. Local repair copies "
            "summary text into Comments when Comments is empty or legacy JSON."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    _add_count_parser(sub)
    _add_repair_parser(sub)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    records = load_jsonl_records(args.jsonl)
    if args.command == "count":
        counts = count_synopsis_states(records)
        json.dump(counts, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        print(
            f"{counts.get('needs_local', 0)} need local repair; "
            f"{counts.get('needs_fetch', 0)} need AO3 fetch.",
            file=sys.stderr,
        )
        return 0
    if args.command == "repair-jsonl":
        repaired, n = repair_jsonl_records(records)
        out_path = Path(args.out) if args.out else Path(args.jsonl)
        with out_path.open("w", encoding="utf-8") as handle:
            for row in repaired:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"Updated synopsis fields on {n} work(s). Wrote {out_path}.", file=sys.stderr)
        return 0
    parser.error(f"Unknown command: {args.command}")
    return 2
