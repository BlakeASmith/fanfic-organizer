#!/usr/bin/env python3
"""Download AO3's native EPUB files for works listed in a JSONL scrape."""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urljoin

import requests
from ao3kit.htmlsoup import parse_html

from ao3kit.http import AO3_BASE, Ao3HttpError, create_session, get, is_login_wall
from ao3kit.rate import apply_request_delay

EPUB_DIRNAME = "epubs"
MANIFEST_NAME = "results.jsonl"
ZIP_NAME = "ao3-import.zip"
TRANSIENT_EPUB_ERRORS = frozenset({"http"})
MAX_WORK_ATTEMPTS = 3


class DownloadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class DownloadOutcome:
    record: dict[str, Any]
    status: str
    epub_file: str | None = None
    error: str | None = None


@dataclass
class DownloadReport:
    outcomes: list[DownloadOutcome] = field(default_factory=list)

    @property
    def downloaded(self) -> int:
        return sum(1 for item in self.outcomes if item.status == "downloaded")

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.outcomes if item.status == "skipped")

    @property
    def failed(self) -> int:
        return sum(1 for item in self.outcomes if item.status == "failed")


DOWNLOAD_STATUS_LABELS = {
    "downloaded": "Downloaded",
    "skipped": "Skipped",
    "failed": "Failed",
}

DOWNLOAD_ERROR_LABELS = {
    "no_epub": "no EPUB download link on the work page",
    "locked": "restricted — log in to AO3 and retry",
    "deleted": "not found (deleted or hidden)",
    "hidden": "hidden or unrevealed",
    "adult": "adult confirmation still required",
    "not_epub": "AO3 did not return an EPUB file",
    "http": "network error",
    "missing_id": "missing work id and URL",
}


def format_download_work_label(record: dict[str, Any]) -> str:
    title = str(record.get("title") or "").strip()
    if title:
        return title
    work_id = str(record.get("work_id") or "").strip()
    if work_id:
        return f"AO3 work {work_id}"
    return "unknown work"


def format_download_outcome_line(
    outcome: DownloadOutcome, index: int, total: int
) -> str:
    """Human status line for one EPUB download (no temp paths)."""
    status = DOWNLOAD_STATUS_LABELS.get(outcome.status, str(outcome.status).capitalize())
    name = format_download_work_label(outcome.record)
    prefix = f"[{index}/{total}] {status} {name}"
    if outcome.status == "failed":
        code = outcome.error or "unknown error"
        detail = DOWNLOAD_ERROR_LABELS.get(code, code)
        return f"{prefix}: {detail}"
    if outcome.status == "skipped":
        return f"{prefix} (already on disk)"
    return prefix


def format_download_report_line(
    report: DownloadReport, dest: str | Path | None = None
) -> str:
    """Summary of a download batch. Omits zero skipped/failed counts."""
    n = report.downloaded
    noun = "EPUB" if n == 1 else "EPUBs"
    parts = [f"Downloaded {n} {noun}"]
    if report.skipped:
        parts.append(f"skipped {report.skipped}")
    if report.failed:
        parts.append(f"failed {report.failed}")
    text = ", ".join(parts)
    if dest is not None:
        text += f" → {dest}"
    return text


def parse_jsonl_line(line: str, *, source: str, line_no: int) -> dict[str, Any]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}:{line_no}: invalid JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError(f"{source}:{line_no}: expected a JSON object")
    if not record.get("work_id") and not record.get("url"):
        raise ValueError(f"{source}:{line_no}: missing work_id and url")
    return record


def parse_jsonl_text(text: str, *, source: str = "<jsonl>") -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        records.append(parse_jsonl_line(line, source=source, line_no=line_no))
    return records


def iter_jsonl_records(path: str | Path) -> Iterator[dict[str, Any]]:
    path = Path(path)
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            yield parse_jsonl_line(line, source=str(path), line_no=line_no)


def load_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    return list(iter_jsonl_records(path))


def work_url_for_record(record: dict[str, Any]) -> str:
    url = (record.get("url") or "").strip()
    if url:
        return url.split("?")[0].rstrip("/")
    work_id = str(record.get("work_id") or "").strip()
    if not work_id:
        raise DownloadError("missing_id", "Record has neither url nor work_id")
    return f"{AO3_BASE}/works/{work_id}"


def work_id_for_record(record: dict[str, Any]) -> str:
    work_id = str(record.get("work_id") or "").strip()
    if work_id:
        return work_id
    url = work_url_for_record(record)
    return url.rstrip("/").split("/")[-1]


def epub_relpath(work_id: str) -> str:
    return f"{EPUB_DIRNAME}/{work_id}.epub"


def absolute_url(href: str, base: str = AO3_BASE) -> str:
    return urljoin(base + "/", href)


def parse_epub_download_href(html: str) -> str | None:
    soup = parse_html(html)
    for anchor in soup.select("li.download a"):
        if anchor.get_text(strip=True).upper() != "EPUB":
            continue
        href = anchor.get("href")
        if href and href != "#":
            return str(href)
    return None


def is_deleted(html: str) -> bool:
    soup = parse_html(html)
    main = soup.find("div", id="main")
    classes = main.get("class", []) if main else []
    lowered = html.lower()
    return (
        "error-404" in classes
        or "couldn't find the work" in lowered
        or "couldn&#x27;t find the work" in lowered
        or "couldn&#39;t find the work" in lowered
    )


def is_hidden(html: str) -> bool:
    return "will be revealed soon" in html.lower()


def is_adult_caution(html: str) -> bool:
    soup = parse_html(html)
    return soup.find("p", class_="caution") is not None


PROCEED_LABELS = {
    "proceed",
    "yes, continue",
    "yes continue",
    "continue",
}


def proceed_href(html: str) -> str | None:
    soup = parse_html(html)
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        if "view_adult=true" in href:
            return href
    for anchor in soup.find_all("a", href=True):
        label = " ".join(anchor.get_text(" ", strip=True).lower().split())
        if label in PROCEED_LABELS:
            return str(anchor["href"])
    return None


def classify_work_page(html: str) -> str | None:
    if is_login_wall(html):
        return "locked"
    if is_deleted(html):
        return "deleted"
    if is_hidden(html):
        return "hidden"
    if is_adult_caution(html):
        return "adult"
    return None


def fetch_work_html(session: requests.Session, url: str) -> str:
    response = get(session, url, view_adult=True, timeout=60)
    return response.text


def resolve_work_html(session: requests.Session, url: str) -> str:
    # Prefer ?view_adult=true up front; fall back to Proceed link if needed.
    html = fetch_work_html(session, url)
    kind = classify_work_page(html)
    if kind == "adult":
        href = proceed_href(html)
        if not href:
            raise DownloadError("adult", "Adult confirmation required but no Proceed link found")
        html = fetch_work_html(session, absolute_url(href, url))
        kind = classify_work_page(html)
    if kind == "locked":
        raise DownloadError("locked", "Work is restricted; log in to AO3 and retry")
    if kind == "deleted":
        raise DownloadError("deleted", "Work was not found (deleted or hidden)")
    if kind == "hidden":
        raise DownloadError("hidden", "Work is hidden or unrevealed")
    if kind == "adult":
        raise DownloadError("adult", "Adult confirmation still required after Proceed")
    return html


def download_epub_to_path(
    session: requests.Session,
    href: str,
    page_url: str,
    dest: Path,
) -> None:
    """Stream AO3's EPUB to disk so large files don't sit in memory."""
    url = absolute_url(href, page_url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    response = get(session, url, stream=True, timeout=180)
    magic = b""
    try:
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(65536):
                if not chunk:
                    continue
                if len(magic) < 2:
                    magic += chunk[: 2 - len(magic)]
                handle.write(chunk)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        response.close()
    if magic != b"PK":
        tmp.unlink(missing_ok=True)
        raise DownloadError("not_epub", "AO3 did not return an EPUB file")
    tmp.replace(dest)


def write_epub(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def download_record_epub(
    record: dict[str, Any],
    dest_dir: Path,
    session: requests.Session,
    *,
    skip_existing: bool = True,
    cover: bool | None = None,
    cover_settings=None,
) -> DownloadOutcome:
    updated = dict(record)
    try:
        work_id = work_id_for_record(updated)
        relpath = epub_relpath(work_id)
        dest = dest_dir / relpath
        if skip_existing and dest.exists() and dest.stat().st_size > 0:
            updated["epub_file"] = relpath
            updated.pop("epub_error", None)
            return DownloadOutcome(record=updated, status="skipped", epub_file=relpath)

        page_url = work_url_for_record(updated)
        html = resolve_work_html(session, page_url)
        href = parse_epub_download_href(html)
        if not href:
            raise DownloadError("no_epub", "Work page has no EPUB download link")
        download_epub_to_path(session, href, page_url, dest)
        from ao3kit.covers import maybe_stamp_downloaded_epub

        cover_error = maybe_stamp_downloaded_epub(
            dest, updated, cover=cover, settings=cover_settings
        )
        if cover_error:
            updated["cover_error"] = cover_error
        updated["epub_file"] = relpath
        updated.pop("epub_error", None)
        return DownloadOutcome(record=updated, status="downloaded", epub_file=relpath)
    except DownloadError as exc:
        updated["epub_error"] = exc.code
        updated.pop("epub_file", None)
        return DownloadOutcome(record=updated, status="failed", error=exc.code)
    except (Ao3HttpError, requests.RequestException):
        updated["epub_error"] = "http"
        updated.pop("epub_file", None)
        return DownloadOutcome(record=updated, status="failed", error="http")


def write_manifest(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(path)


class JsonlWriter:
    """Atomically rewrite a JSONL file as records are added or updated.

    Readers always see a complete file (tmp + replace). Used so Calibre can
    import metadata as soon as a work is scraped, then attach the EPUB when
    ``epub_file`` appears on that row.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.records: list[dict[str, Any]] = []
        self._index: dict[str, int] = {}
        write_manifest(self.records, self.path)

    def _work_id(self, record: dict[str, Any]) -> str:
        return str(record.get("work_id") or "").strip()

    def upsert(self, record: dict[str, Any]) -> None:
        work_id = self._work_id(record)
        if work_id and work_id in self._index:
            self.records[self._index[work_id]] = record
        else:
            if work_id:
                self._index[work_id] = len(self.records)
            self.records.append(record)
        write_manifest(self.records, self.path)

    def add_work(self, work: Any, *, score_config: Any = None) -> None:
        to_dict = getattr(work, "to_dict", None)
        if callable(to_dict):
            record = to_dict(score_config=score_config)
        else:
            record = dict(work)
        self.upsert(record)

    def replace_all(self, records: list[dict[str, Any]]) -> None:
        self.records = [dict(item) for item in records]
        self._index = {}
        for index, record in enumerate(self.records):
            work_id = self._work_id(record)
            if work_id:
                self._index[work_id] = index
        write_manifest(self.records, self.path)


def pack_import_zip(dest_dir: Path, zip_path: Path | None = None) -> Path:
    dest_dir = dest_dir.resolve()
    zip_path = zip_path or (dest_dir / ZIP_NAME)
    manifest = dest_dir / MANIFEST_NAME
    epub_dir = dest_dir / EPUB_DIRNAME
    if not manifest.exists():
        raise FileNotFoundError(f"Missing manifest: {manifest}")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(manifest, arcname=MANIFEST_NAME)
        if epub_dir.exists():
            for epub in sorted(epub_dir.glob("*.epub")):
                zf.write(epub, arcname=f"{EPUB_DIRNAME}/{epub.name}")
    return zip_path


def download_records(
    records: list[dict[str, Any]],
    dest_dir: str | Path,
    session: requests.Session,
    *,
    request_delay: float | None = None,
    skip_existing: bool = True,
    make_zip: bool = True,
    zip_path: str | Path | None = None,
    on_outcome: Callable[[DownloadOutcome, int, int], None] | None = None,
    simplify_tags: bool = True,
    on_status: Callable[[str], None] | None = None,
    cover: bool | None = None,
    cover_settings=None,
) -> DownloadReport:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    apply_request_delay(request_delay)
    report = DownloadReport()
    total = len(records)
    manifest_path = dest / MANIFEST_NAME
    # Keep every work in the manifest from the start so metadata is visible
    # before its EPUB lands; update the matching row after each download.
    manifest_records = [dict(item) for item in records]
    write_manifest(manifest_records, manifest_path)

    for index, record in enumerate(records, start=1):
        outcome = download_record_epub(
            record,
            dest,
            session,
            skip_existing=skip_existing,
            cover=cover,
            cover_settings=cover_settings,
        )
        attempts = 1
        while (
            outcome.status == "failed"
            and outcome.error in TRANSIENT_EPUB_ERRORS
            and attempts < MAX_WORK_ATTEMPTS
        ):
            attempts += 1
            outcome = download_record_epub(
                record,
                dest,
                session,
                skip_existing=False,
                cover=cover,
                cover_settings=cover_settings,
            )
        report.outcomes.append(outcome)
        manifest_records[index - 1] = outcome.record
        write_manifest(manifest_records, manifest_path)
        if on_outcome:
            on_outcome(outcome, index, total)

    if simplify_tags and report.outcomes:
        if on_status:
            on_status("Simplifying tags, fandoms, and relationships with user rules…")
        from ao3kit.tags.clean import enrich_records

        enriched = enrich_records(
            [item.record for item in report.outcomes],
            delay=request_delay,
            on_status=on_status,
        )
        for outcome, record in zip(report.outcomes, enriched, strict=True):
            outcome.record = record
        write_manifest([item.record for item in report.outcomes], manifest_path)

    if make_zip:
        pack_import_zip(dest, Path(zip_path) if zip_path else None)
    return report


def download_from_jsonl(
    jsonl_path: str | Path,
    dest_dir: str | Path,
    session: requests.Session,
    *,
    request_delay: float | None = None,
    skip_existing: bool = True,
    make_zip: bool = True,
    zip_path: str | Path | None = None,
    on_outcome: Callable[[DownloadOutcome, int, int], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    simplify_tags: bool = True,
    cover: bool | None = None,
    cover_settings=None,
) -> DownloadReport:
    return download_records(
        load_jsonl_records(jsonl_path),
        dest_dir,
        session,
        request_delay=request_delay,
        skip_existing=skip_existing,
        make_zip=make_zip,
        zip_path=zip_path,
        on_outcome=on_outcome,
        on_status=on_status,
        simplify_tags=simplify_tags,
        cover=cover,
        cover_settings=cover_settings,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download AO3 native EPUB files for works listed in a JSONL scrape."
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help="Input JSONL from python -m ao3kit scrape",
    )
    parser.add_argument(
        "-d",
        "--dir",
        help="Output directory (default: <input-stem>-epubs next to the JSONL)",
    )
    parser.add_argument(
        "--zip",
        nargs="?",
        const=ZIP_NAME,
        default=ZIP_NAME,
        metavar="PATH",
        help="Write an import zip (default: ao3-import.zip inside the output directory). "
        "Pass --no-zip to skip.",
    )
    parser.add_argument("--no-zip", action="store_true", help="Do not write an import zip")
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help=(
            "Seconds between AO3 requests (default: config request_delay, 1.5). "
            "Tag profiles use a faster adaptive lane."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download EPUBs even if they already exist",
    )
    parser.add_argument("--username", help="AO3 username (or set AO3_USERNAME)")
    parser.add_argument("--password", help="AO3 password (or set AO3_PASSWORD)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--simplify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run tag/fandom/relationship enrich after download (default: yes). Pass --no-simplify to skip.",
    )
    parser.add_argument(
        "--cover",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Stamp a generated cover into each EPUB (default: config cover.enabled). "
            "Style: python -m ao3kit config set cover.<key> …"
        ),
    )
    args = parser.parse_args(argv)

    jsonl_path = Path(args.input)
    dest_dir = Path(args.dir) if args.dir else jsonl_path.with_name(f"{jsonl_path.stem}-epubs")
    make_zip = not args.no_zip
    zip_path = None
    if make_zip:
        zip_arg = Path(args.zip)
        zip_path = zip_arg if zip_arg.is_absolute() else dest_dir / zip_arg

    username = args.username or os.environ.get("AO3_USERNAME")
    password = args.password or os.environ.get("AO3_PASSWORD")
    if (username and not password) or (password and not username):
        parser.error("Both username and password are required to log in to AO3")

    session = create_session(username, password)

    def on_outcome(outcome: DownloadOutcome, index: int, total: int) -> None:
        if not args.verbose:
            return
        print(format_download_outcome_line(outcome, index, total), file=sys.stderr)

    if args.verbose and username:
        print("Logged in to AO3", file=sys.stderr)

    on_status = (lambda msg: print(msg, file=sys.stderr)) if args.verbose else None
    report = download_from_jsonl(
        jsonl_path,
        dest_dir,
        session,
        request_delay=args.delay,
        skip_existing=not args.force,
        make_zip=make_zip,
        zip_path=zip_path,
        on_outcome=on_outcome,
        on_status=on_status,
        simplify_tags=args.simplify,
        cover=args.cover,
    )
    print(format_download_report_line(report, dest_dir), file=sys.stderr)
    if make_zip:
        print(f"Import zip: {zip_path or dest_dir / ZIP_NAME}", file=sys.stderr)
    if args.simplify:
        from ao3kit.tags.clean import emit_remapping_summary

        emit_remapping_summary(
            [item.record for item in report.outcomes],
            lambda msg: print(msg, file=sys.stderr),
        )
    return 1 if report.failed and not report.downloaded and not report.skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
