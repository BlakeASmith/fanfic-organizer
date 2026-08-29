"""Identify AO3 works from library hints (URL, EPUB, title + author).

Used by the Calibre plugin **Fill from AO3** action and ``python -m ao3kit
identify``. Direct work ids / URLs are unique; title+author search may return
several candidates for the UI to pick from.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from xml.etree import ElementTree as ET

from ao3kit.http import AO3_BASE, create_session
from ao3kit.rate import ensure_rate_limits
from ao3kit.scrape import (
    SearchCriteria,
    WorkRecord,
    scrape_search,
    write_jsonl_dicts,
)

StatusCallback = Callable[[str], None]

WORK_ID_RE = re.compile(
    r"(?:https?://)?(?:www\.)?archiveofourown\.org/works/(\d+)",
    re.IGNORECASE,
)
DOWNLOAD_ID_RE = re.compile(
    r"(?:https?://)?(?:www\.)?archiveofourown\.org/downloads/(\d+)",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
UNKNOWN_AUTHORS = frozenset({"unknown", "unknown author", "anonymous", "anon"})
UNKNOWN_TITLES = frozenset({"unknown", "untitled", "no title"})

STATUS_IDENTIFIED = "identified"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_NOT_FOUND = "not_found"
STATUS_SKIPPED = "skipped"

SOURCE_IDENTIFIER = "identifier"
SOURCE_COMMENTS = "comments"
SOURCE_EPUB = "epub"
SOURCE_SEARCH = "search"

UNIQUE_SCORE = 80
SHOW_SCORE = 40
MAX_SEARCH_RESULTS = 20
PRESERVE_HINT_KEYS = (
    "book_id",
    "calibre_uuid",
    "calibre_book_id",
    "has_epub",
    "epub_file",
    "comments",
    "current_collections",
)


@dataclass
class IdentifyHint:
    """Partial work identity taken from a library row or CLI flags."""

    title: str = ""
    authors: list[str] = field(default_factory=list)
    work_id: str = ""
    url: str = ""
    comments: str = ""
    fandoms: list[str] = field(default_factory=list)
    epub_file: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> IdentifyHint:
        record = record or {}
        authors = _as_names(record.get("authors"))
        if not authors:
            authors = _as_names(record.get("author"))
        work_id = str(record.get("work_id") or "").strip()
        url = str(record.get("url") or "").strip()
        if not work_id:
            work_id = work_id_from_text(url) or ""
        extra = {
            key: value
            for key, value in record.items()
            if key
            not in {
                "title",
                "author",
                "authors",
                "work_id",
                "url",
                "comments",
                "fandoms",
                "epub_file",
                "status",
                "source",
                "reason",
                "candidates",
                "score",
            }
        }
        return cls(
            title=str(record.get("title") or "").strip(),
            authors=authors,
            work_id=work_id,
            url=url,
            comments=str(record.get("comments") or ""),
            fandoms=_as_names(record.get("fandoms")),
            epub_file=str(record.get("epub_file") or "").strip(),
            extra=extra,
        )


@dataclass
class IdentifyResult:
    status: str
    source: str = ""
    reason: str = ""
    record: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    score: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.record)
        data["status"] = self.status
        if self.source:
            data["source"] = self.source
        if self.reason:
            data["reason"] = self.reason
        if self.candidates:
            data["candidates"] = list(self.candidates)
        elif "candidates" in data:
            data.pop("candidates", None)
        if self.score is not None:
            data["score"] = self.score
        return data


def work_url(work_id: str) -> str:
    return f"{AO3_BASE}/works/{str(work_id).strip()}"


def work_id_from_text(value: Any) -> str | None:
    """First AO3 work id found in a URL, identifier, or blob of text."""
    if value is None:
        return None
    if isinstance(value, dict):
        ao3 = str(value.get("ao3") or "").strip()
        if ao3.isdigit():
            return ao3
        for item in value.values():
            found = work_id_from_text(item)
            if found:
                return found
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            found = work_id_from_text(item)
            if found:
                return found
        return None
    text = strip_html(str(value))
    if not text.strip():
        return None
    match = WORK_ID_RE.search(text)
    if match:
        return match.group(1)
    match = DOWNLOAD_ID_RE.search(text)
    if match:
        return match.group(1)
    return None


def strip_html(value: str) -> str:
    text = HTML_TAG_RE.sub(" ", value or "")
    return html.unescape(text)


def _as_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        text = str(value).strip()
        if not text:
            return []
        if "," in text:
            items = [part.strip() for part in text.split(",") if part.strip()]
        else:
            items = [text]
    seen: set[str] = set()
    out: list[str] = []
    for name in items:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def usable_authors(names: Iterable[str]) -> list[str]:
    return [
        name
        for name in _as_names(list(names))
        if name.casefold() not in UNKNOWN_AUTHORS
    ]


def usable_title(title: str) -> str:
    text = str(title or "").strip()
    if not text or text.casefold() in UNKNOWN_TITLES:
        return ""
    return text


def normalize_title(title: str) -> str:
    text = usable_title(title).casefold()
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def titles_match(left: str, right: str) -> bool:
    a = normalize_title(left)
    b = normalize_title(right)
    return bool(a) and a == b


def author_matches(hint_authors: Iterable[str], work_author: str | None) -> bool:
    wanted = [name.casefold() for name in usable_authors(hint_authors)]
    if not wanted:
        return False
    blob = str(work_author or "").casefold()
    if not blob:
        return False
    return any(name in blob or blob in name for name in wanted)


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if tag else ""


def extract_work_id_from_epub_bytes(data: bytes) -> str | None:
    """Scan an EPUB (OPF + text files) for an AO3 works or downloads URL."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    with zf:
        names = zf.namelist()
        opf_names = [name for name in names if name.lower().endswith(".opf")]
        ordered = opf_names + [name for name in names if name not in opf_names]
        for name in ordered:
            lowered = name.lower()
            if lowered.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ttf", ".otf")
            ):
                continue
            try:
                info = zf.getinfo(name)
            except KeyError:
                continue
            if info.file_size > 1_500_000:
                continue
            try:
                raw = zf.read(name)
            except Exception:
                continue
            try:
                text = raw.decode("utf-8", errors="ignore")
            except Exception:
                continue
            found = work_id_from_text(text)
            if found:
                return found
            if lowered.endswith(".opf"):
                found = _work_id_from_opf_xml(raw)
                if found:
                    return found
    return None


def _work_id_from_opf_xml(raw: bytes) -> str | None:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    for el in root.iter():
        if _local_tag(el.tag) not in {"identifier", "source", "relation"}:
            continue
        blob = " ".join(
            part
            for part in (
                (el.text or ""),
                " ".join(f"{key}={value}" for key, value in el.attrib.items()),
            )
            if part
        )
        found = work_id_from_text(blob)
        if found:
            return found
    return None


def extract_work_id_from_epub(path: str | Path) -> str | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return extract_work_id_from_epub_bytes(data)


def resolve_epub_path(
    hint: IdentifyHint,
    *,
    bundle: str | Path | None = None,
    source: str | Path | None = None,
) -> Path | None:
    rel = str(hint.epub_file or "").strip()
    if not rel:
        return None
    path = Path(rel)
    if path.is_file():
        return path
    roots: list[Path] = []
    if bundle:
        roots.append(Path(bundle))
    if source:
        parent = Path(source).parent
        roots.append(parent)
        roots.append(parent / "bundle")
    for root in roots:
        candidate = root / rel
        if candidate.is_file():
            return candidate
        nested = root / "epubs" / Path(rel).name
        if nested.is_file():
            return nested
    return None


def extract_local_work_id(
    hint: IdentifyHint,
    *,
    bundle: str | Path | None = None,
    source: str | Path | None = None,
) -> tuple[str, str]:
    """Return ``(work_id, source)`` from identifiers, comments, or EPUB.

    ``source`` is empty when nothing unique was found.
    """
    if hint.work_id:
        return hint.work_id, SOURCE_IDENTIFIER
    from_url = work_id_from_text(hint.url)
    if from_url:
        return from_url, SOURCE_IDENTIFIER
    extra_id = work_id_from_text(hint.extra.get("identifiers"))
    if extra_id:
        return extra_id, SOURCE_IDENTIFIER
    from_comments = work_id_from_text(hint.comments)
    if from_comments:
        return from_comments, SOURCE_COMMENTS
    epub_path = resolve_epub_path(hint, bundle=bundle, source=source)
    if epub_path is not None:
        from_epub = extract_work_id_from_epub(epub_path)
        if from_epub:
            return from_epub, SOURCE_EPUB
    return "", ""


def search_query_for_title(title: str) -> str:
    cleaned = usable_title(title).replace('"', " ").strip()
    if not cleaned:
        return ""
    return f'title: "{cleaned}"'


def score_candidate(hint: IdentifyHint, work: WorkRecord) -> int:
    title_ok = titles_match(hint.title, work.title)
    author_ok = author_matches(hint.authors, work.author)
    fandom_ok = False
    wanted_fandoms = {name.casefold() for name in hint.fandoms if name.strip()}
    if wanted_fandoms:
        have = {name.casefold() for name in work.fandoms}
        fandom_ok = bool(wanted_fandoms & have)
    if title_ok and author_ok:
        return 100
    if title_ok and fandom_ok:
        return 85
    if title_ok:
        return 70
    hint_norm = normalize_title(hint.title)
    work_norm = normalize_title(work.title)
    if hint_norm and work_norm and (hint_norm in work_norm or work_norm in hint_norm):
        if author_ok:
            return 55
        return 45
    if author_ok and fandom_ok:
        return 35
    if author_ok:
        return 25
    return 0


def classify_search_matches(
    hint: IdentifyHint,
    works: list[WorkRecord],
) -> IdentifyResult:
    scored: list[tuple[int, WorkRecord]] = []
    for work in works:
        score = score_candidate(hint, work)
        if score >= SHOW_SCORE:
            scored.append((score, work))
    scored.sort(key=lambda item: (-item[0], item[1].title.casefold()))
    record = _hint_record(hint)
    if not scored:
        return IdentifyResult(
            status=STATUS_NOT_FOUND,
            source=SOURCE_SEARCH,
            reason="no AO3 work matched that title and author",
            record=record,
        )
    best_score, best = scored[0]
    unique = best_score >= UNIQUE_SCORE and (
        len(scored) == 1 or scored[1][0] < UNIQUE_SCORE
    )
    if unique and titles_match(hint.title, best.title):
        merged = _merge_work(record, best)
        return IdentifyResult(
            status=STATUS_IDENTIFIED,
            source=SOURCE_SEARCH,
            record=merged,
            score=best_score,
        )
    return IdentifyResult(
        status=STATUS_AMBIGUOUS,
        source=SOURCE_SEARCH,
        reason="several AO3 works match; pick one",
        record=record,
        candidates=[work.to_dict() for _score, work in scored],
        score=best_score,
    )


def _hint_record(hint: IdentifyHint) -> dict[str, Any]:
    record = dict(hint.extra)
    if hint.title:
        record["title"] = hint.title
    if hint.authors:
        record["authors"] = list(hint.authors)
        record.setdefault("author", hint.authors[0])
    if hint.work_id:
        record["work_id"] = hint.work_id
    if hint.url:
        record["url"] = hint.url
    if hint.comments:
        record["comments"] = hint.comments
    if hint.fandoms:
        record["fandoms"] = list(hint.fandoms)
    if hint.epub_file:
        record["epub_file"] = hint.epub_file
    return record


def _merge_work(record: dict[str, Any], work: WorkRecord) -> dict[str, Any]:
    merged = dict(record)
    data = work.to_dict()
    merged.update(data)
    for key in PRESERVE_HINT_KEYS:
        if key in record:
            merged[key] = record[key]
    return merged


def _identified_from_id(hint: IdentifyHint, work_id: str, source: str) -> IdentifyResult:
    record = _hint_record(hint)
    record["work_id"] = work_id
    record["url"] = work_url(work_id)
    if not record.get("title"):
        record["title"] = hint.title or f"AO3 work {work_id}"
    return IdentifyResult(status=STATUS_IDENTIFIED, source=source, record=record)


def search_candidates(
    hint: IdentifyHint,
    *,
    session,
    max_results: int = MAX_SEARCH_RESULTS,
) -> list[WorkRecord]:
    title = usable_title(hint.title)
    authors = usable_authors(hint.authors)
    if not title:
        return []
    query = search_query_for_title(title)
    creators = authors[0] if authors else ""
    criteria = SearchCriteria(
        query=query,
        creators=creators,
        language_id="",
        sort_column="title_to_sort_on",
    )
    works = scrape_search(
        criteria,
        max_results=max_results,
        session=session,
    )
    if works or not creators:
        return works
    # Author filter can miss pseuds; retry on title only.
    return scrape_search(
        SearchCriteria(
            query=query,
            language_id="",
            sort_column="title_to_sort_on",
        ),
        max_results=max_results,
        session=session,
    )


def identify_hint(
    hint: IdentifyHint,
    *,
    session=None,
    search: bool = True,
    bundle: str | Path | None = None,
    source: str | Path | None = None,
    max_results: int = MAX_SEARCH_RESULTS,
) -> IdentifyResult:
    work_id, origin = extract_local_work_id(hint, bundle=bundle, source=source)
    if work_id:
        return _identified_from_id(hint, work_id, origin)
    title = usable_title(hint.title)
    authors = usable_authors(hint.authors)
    if not title:
        return IdentifyResult(
            status=STATUS_SKIPPED,
            reason="no AO3 URL, EPUB, or title to identify from",
            record=_hint_record(hint),
        )
    if not search:
        reason = "needs AO3 search (title and author)"
        if not authors:
            reason = "needs AO3 search (title)"
        return IdentifyResult(
            status=STATUS_NOT_FOUND,
            reason=reason,
            record=_hint_record(hint),
        )
    session = session or create_session()
    ensure_rate_limits()
    works = search_candidates(hint, session=session, max_results=max_results)
    return classify_search_matches(hint, works)


def identify_records(
    records: list[dict[str, Any]],
    *,
    session=None,
    search: bool = True,
    bundle: str | Path | None = None,
    source: str | Path | None = None,
    max_results: int = MAX_SEARCH_RESULTS,
    on_status: StatusCallback | None = None,
) -> list[IdentifyResult]:
    session = session or (create_session() if search else None)
    if search:
        ensure_rate_limits()
    results: list[IdentifyResult] = []
    total = len(records)
    for index, record in enumerate(records, start=1):
        hint = IdentifyHint.from_record(record)
        label = hint.title or hint.work_id or f"row {index}"
        if on_status:
            on_status(f"[{index}/{total}] Identifying {label}…")
        needs_network = search and not extract_local_work_id(
            hint, bundle=bundle, source=source
        )[0]
        result = identify_hint(
            hint,
            session=session if needs_network else None,
            search=search,
            bundle=bundle,
            source=source,
            max_results=max_results,
        )
        results.append(result)
        if on_status:
            if result.status == STATUS_IDENTIFIED:
                work_id = result.record.get("work_id")
                on_status(f"Identified {label} as AO3 work {work_id}.")
            elif result.status == STATUS_AMBIGUOUS:
                n = len(result.candidates)
                on_status(f"{label}: {n} possible AO3 works; needs a pick.")
            else:
                on_status(f"{label}: {result.reason or result.status}.")
    return results


def split_identify_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split identify JSONL into ``(identified, ambiguous, failed)``."""
    identified: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for record in records:
        status = str(record.get("status") or "").strip()
        work_id = str(record.get("work_id") or "").strip()
        if status == STATUS_IDENTIFIED and work_id:
            identified.append(record)
        elif status == STATUS_AMBIGUOUS:
            ambiguous.append(record)
        else:
            failed.append(record)
    return identified, ambiguous, failed


def apply_identify_choices(
    records: list[dict[str, Any]],
    choices: dict[Any, Any],
) -> list[dict[str, Any]]:
    """Apply ``{book_id: work_id}`` picks onto ambiguous identify rows.

    Empty / missing work ids skip that book. Identified rows pass through.
    """
    mapped = {
        str(key).strip(): str(value).strip()
        for key, value in (choices or {}).items()
        if str(key).strip()
    }
    out: list[dict[str, Any]] = []
    for record in records:
        status = str(record.get("status") or "").strip()
        if status == STATUS_IDENTIFIED and str(record.get("work_id") or "").strip():
            out.append(dict(record))
            continue
        if status != STATUS_AMBIGUOUS:
            out.append(dict(record))
            continue
        book_id = record.get("book_id")
        if book_id is None:
            book_id = record.get("calibre_book_id")
        picked = mapped.get(str(book_id or "").strip(), "")
        if not picked:
            continue
        chosen = None
        for candidate in record.get("candidates") or []:
            if str((candidate or {}).get("work_id") or "").strip() == picked:
                chosen = candidate
                break
        updated = dict(record)
        if isinstance(chosen, dict):
            for key, value in chosen.items():
                if key in {"status", "source", "reason", "candidates", "score"}:
                    continue
                updated[key] = value
        updated["work_id"] = picked
        updated["url"] = str(updated.get("url") or "").strip() or work_url(picked)
        updated["status"] = STATUS_IDENTIFIED
        updated["source"] = SOURCE_SEARCH
        updated.pop("candidates", None)
        updated.pop("reason", None)
        out.append(updated)
    return out


def load_hint_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_no}: expected a JSON object")
            records.append(record)
    return records


def format_identify_summary(results: list[IdentifyResult]) -> str:
    identified = sum(1 for item in results if item.status == STATUS_IDENTIFIED)
    ambiguous = sum(1 for item in results if item.status == STATUS_AMBIGUOUS)
    failed = len(results) - identified - ambiguous
    noun = "book" if len(results) == 1 else "books"
    parts = [f"Identified {identified} of {len(results)} {noun}"]
    if ambiguous:
        parts.append(f"{ambiguous} need a pick")
    if failed:
        parts.append(f"{failed} could not be matched")
    return "; ".join(parts) + "."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Identify AO3 works from URLs, EPUBs, or title + author.",
    )
    parser.add_argument(
        "--from",
        dest="hints",
        help="JSONL of library hints (title, author, url, epub_file, …)",
    )
    parser.add_argument("-o", "--output", help="Write identify JSONL here")
    parser.add_argument("--title", default="", help="Identify a single title")
    parser.add_argument("--author", default="", help="Author for --title")
    parser.add_argument("--url", default="", help="AO3 work URL if already known")
    parser.add_argument("--epub", default="", help="EPUB to scan for an AO3 URL")
    parser.add_argument(
        "--bundle",
        help="Directory that --from epub_file paths are relative to",
    )
    parser.add_argument(
        "--no-search",
        action="store_true",
        help="Only use identifiers / EPUB URLs; do not search AO3",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=MAX_SEARCH_RESULTS,
        help="AO3 search cap per title+author lookup (default: 20)",
    )
    parser.add_argument("--username", help="AO3 username (or set AO3_USERNAME)")
    parser.add_argument("--password", help="AO3 password (or set AO3_PASSWORD)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    records: list[dict[str, Any]] = []
    source_path = args.hints
    if args.hints:
        try:
            records = load_hint_jsonl(args.hints)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        if not records:
            parser.error("No hints found in --from")
    else:
        record: dict[str, Any] = {}
        if args.title:
            record["title"] = args.title
        if args.author:
            record["author"] = args.author
        if args.url:
            record["url"] = args.url
        if args.epub:
            record["epub_file"] = args.epub
        if not record:
            parser.error("Pass --from JSONL or --title / --url / --epub")
        records = [record]

    import os

    username = args.username or os.environ.get("AO3_USERNAME")
    password = args.password or os.environ.get("AO3_PASSWORD")
    if (username and not password) or (password and not username):
        parser.error("Both username and password are required to log in to AO3")

    search = not args.no_search
    session = None
    if search:
        session = create_session(
            username,
            password,
            on_status=(lambda msg: print(msg, file=sys.stderr)) if args.verbose else None,
        )

    def on_status(message: str) -> None:
        if args.verbose:
            print(message, file=sys.stderr)

    results = identify_records(
        records,
        session=session,
        search=search,
        bundle=args.bundle,
        source=source_path,
        max_results=args.max_results,
        on_status=on_status,
    )
    payload = [item.to_dict() for item in results]
    if args.output:
        write_jsonl_dicts(payload, args.output)
    else:
        json.dump(payload if len(payload) != 1 else payload[0], sys.stdout, indent=2)
        sys.stdout.write("\n")
    if args.verbose:
        print(format_identify_summary(results), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
