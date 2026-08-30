"""GitHub Actions helpers for preview, PR, and canonical plugin builds."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from calibre_dev.changelog import (
    ChangelogError,
    format_version,
    next_0x_version,
    read_plugin_version,
)
from calibre_dev.preview import annotate_superseded_previews
from calibre_dev.release_urls import GITHUB_REPO
from calibre_dev.versioning import (
    format_pr_version,
    format_preview_version,
    plugin_zip_name,
    pr_release_notes,
    preview_release_notes,
    release_tag_name,
)

ROOT = Path(__file__).resolve().parents[1]


def _write_github_output(values: dict[str, str]) -> None:
    dest = (os.environ.get("GITHUB_OUTPUT") or "").strip()
    lines = [f"{key}={value}" for key, value in values.items()]
    text = "\n".join(lines) + "\n"
    if dest:
        with open(dest, "a", encoding="utf-8") as handle:
            handle.write(text)
        return
    sys.stdout.write(text)


def _next_base_version() -> str:
    return format_version(next_0x_version(read_plugin_version()))


def cmd_preview_meta(args: argparse.Namespace) -> int:
    version = format_preview_version(_next_base_version(), args.build, args.sha)
    notes = preview_release_notes(
        git_hash=args.sha,
        latest_release_url=f"https://github.com/{GITHUB_REPO}/releases/latest",
    )
    notes_path = Path(args.notes_file)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(notes, encoding="utf-8")
    zip_name = plugin_zip_name(version)
    _write_github_output(
        {
            "version": version,
            "tag": release_tag_name(version),
            "zip_name": zip_name,
            "notes_file": str(notes_path),
        }
    )
    return 0


def cmd_pr_meta(args: argparse.Namespace) -> int:
    version = format_pr_version(_next_base_version(), args.pr, args.sha)
    zip_name = plugin_zip_name(version)
    tag = release_tag_name(version)
    notes = pr_release_notes(
        pr_number=args.pr,
        git_hash=args.sha,
        pr_url=args.pr_url
        or f"https://github.com/{GITHUB_REPO}/pull/{int(args.pr)}",
        latest_release_url=f"https://github.com/{GITHUB_REPO}/releases/latest",
    )
    notes_path = Path(args.notes_file)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(notes, encoding="utf-8")
    _write_github_output(
        {
            "version": version,
            "tag": tag,
            "zip_name": zip_name,
            "notes_file": str(notes_path),
        }
    )
    return 0


def cmd_annotate_previews(args: argparse.Namespace) -> int:
    updated = annotate_superseded_previews(
        args.version,
        repo=args.repo,
        cwd=args.cwd or ROOT,
    )
    if updated:
        print("Annotated preview releases: " + ", ".join(updated))
    else:
        print("No preview releases needed annotation.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m calibre_dev.ci",
        description="Emit GitHub Actions metadata for plugin preview/PR/release jobs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("preview-meta", help="Version, tag, zip name, notes file.")
    preview.add_argument("--build", required=True, help="GitHub run number.")
    preview.add_argument("--sha", required=True, help="Full or short git commit SHA.")
    preview.add_argument(
        "--notes-file",
        default="preview-notes.md",
        help="Write preview release notes here (read-only changelog scrape).",
    )
    preview.set_defaults(func=cmd_preview_meta)

    pr_meta = sub.add_parser("pr-meta", help="PR zip name, tag, and release notes.")
    pr_meta.add_argument("--pr", required=True, help="Pull request number.")
    pr_meta.add_argument("--sha", required=True, help="Full or short git commit SHA.")
    pr_meta.add_argument(
        "--pr-url",
        default="",
        help="Pull request URL for release notes (optional).",
    )
    pr_meta.add_argument(
        "--notes-file",
        default="pr-notes.md",
        help="Write PR release notes here.",
    )
    pr_meta.set_defaults(func=cmd_pr_meta)

    annotate = sub.add_parser(
        "annotate-previews",
        help="Mark preview GitHub releases whose commits are in a standard tag.",
    )
    annotate.add_argument("--version", required=True, help="Standard X.Y.Z just published.")
    annotate.add_argument("--repo", default=GITHUB_REPO)
    annotate.add_argument("--cwd", default="", help="Git checkout (default: repo root).")
    annotate.set_defaults(func=cmd_annotate_previews)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ChangelogError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
