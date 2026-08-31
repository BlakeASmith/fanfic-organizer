"""Omnibus metadata merge and CLI for combining EPUBs."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from ao3kit.epub_merge import (
    MemberSpec,
    append_members,
    extract_member_epub,
    merge_epubs,
    read_omnibus_members,
    read_omnibus_meta,
    rebuild_epubs,
    remove_members,
    reorder_members,
)

COMPLETED_TAG = "Completed"


def series_omnibus_title(series_name: str) -> str:
    """Calibre title for a series omnibus: ``{series name} - Series``."""
    name = str(series_name or "").strip()
    if not name:
        return "Series"
    if name.casefold().endswith(" - series"):
        return name
    return f"{name} - Series"


def member_id_from_record(record: dict[str, Any]) -> str:
    for key in ("work_id", "member_id"):
        text = str(record.get(key) or "").strip()
        if text:
            return text
    ids = record.get("identifiers") or {}
    if isinstance(ids, dict):
        for key in ("ao3", "wikipedia", "url"):
            text = str(ids.get(key) or "").strip()
            if text:
                return text.replace("/", "_")
    return str(uuid.uuid4())


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []


def _ordered_union(*groups: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            text = str(item or "").strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            out.append(text)
    return out


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _word_count(record: dict[str, Any]) -> int:
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    cleaned = record.get("cleaned") if isinstance(record.get("cleaned"), dict) else {}
    for source in (cleaned, meta, record):
        for key in ("word_count", "words", "wordcount"):
            raw = source.get(key) if isinstance(source, dict) else None
            if raw is None:
                continue
            try:
                return int(str(raw).replace(",", "").strip() or 0)
            except ValueError:
                continue
    return 0


def _is_complete(record: dict[str, Any]) -> bool:
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    cleaned = record.get("cleaned") if isinstance(record.get("cleaned"), dict) else {}
    for source in (cleaned, meta, record):
        if not isinstance(source, dict):
            continue
        if "complete" in source:
            return bool(source.get("complete"))
        tags = _as_list(source.get("tags"))
        if any(t.casefold() == COMPLETED_TAG.casefold() for t in tags):
            return True
    return False


def _field_list(record: dict[str, Any], *keys: str) -> list[str]:
    cleaned = record.get("cleaned") if isinstance(record.get("cleaned"), dict) else {}
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    for key in keys:
        for source in (cleaned, meta, record):
            if isinstance(source, dict) and key in source:
                return _as_list(source.get(key))
    return []


def _member_part(record: dict[str, Any]) -> int | None:
    for key in ("series_index", "position", "part"):
        raw = record.get(key)
        if raw in (None, ""):
            continue
        try:
            return int(float(str(raw)))
        except (TypeError, ValueError):
            continue
    for entry in record.get("series") or []:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("position")
        if raw in (None, ""):
            continue
        try:
            return int(float(str(raw)))
        except (TypeError, ValueError):
            continue
    return None


def merge_omnibus_record(
    members: Sequence[dict[str, Any]],
    *,
    omnibus_id: str,
    kind: str,
    title: str,
    series_id: str = "",
    series_name: str = "",
    collection: str = "",
    auto_update: bool = False,
) -> dict[str, Any]:
    """Build one JSONL-shaped work record for an omnibus row."""
    member_ids = [member_id_from_record(m) for m in members]
    member_rows: list[dict[str, Any]] = []
    for m in members:
        mid = member_id_from_record(m)
        member_rows.append(
            {
                "member_id": mid,
                "title": str(m.get("title") or mid),
                "part": _member_part(m),
            }
        )
    authors = _ordered_union(*(_field_list(m, "authors", "author") or _as_list(m.get("author")) for m in members))
    # author field often a string
    if not authors:
        authors = _ordered_union(*(_as_list(m.get("author")) for m in members))
    tags = _ordered_union(*(_field_list(m, "tags") for m in members))
    fandoms = _ordered_union(*(_field_list(m, "fandoms", "fandom") for m in members))
    relationships = _ordered_union(
        *(_field_list(m, "relationships", "relationship") for m in members)
    )
    characters = _ordered_union(*(_field_list(m, "characters", "character") for m in members))
    collections = _ordered_union(*(_field_list(m, "collections") for m in members))
    if collection and collection not in collections:
        collections = [collection] + collections
    originaltags = _ordered_union(*(_field_list(m, "originaltags", "original_tags") for m in members))
    words = sum(_word_count(m) for m in members)
    all_complete = bool(members) and all(_is_complete(m) for m in members)
    if all_complete and COMPLETED_TAG not in tags:
        tags = list(tags) + [COMPLETED_TAG]

    dates = [_parse_date(m.get("published") or (m.get("metadata") or {}).get("published") or (m.get("metadata") or {}).get("date")) for m in members]
    dates = [d for d in dates if d is not None]
    pubdate = min(dates).isoformat() if dates else None

    summary = ""
    if series_name:
        summary = series_name
    elif collection:
        summary = collection
    elif members:
        summary = str(
            members[0].get("summary")
            or (members[0].get("cleaned") or {}).get("summary")
            or (members[0].get("metadata") or {}).get("summary")
            or ""
        )

    identifiers: dict[str, str] = {
        "omnibus": omnibus_id,
        "ao3members": ",".join(member_ids),
    }
    if series_id:
        identifiers["ao3series"] = str(series_id)
    if collection:
        identifiers["omnibuscollection"] = collection

    series_payload = []
    if series_id or series_name:
        series_payload.append(
            {
                "series_id": str(series_id or ""),
                "name": series_name or title,
                "url": f"https://archiveofourown.org/series/{series_id}" if series_id else "",
                "position": 1,
            }
        )

    cleaned = {
        "tags": tags,
        "simplified": tags,
        "fandoms": fandoms,
        "relationships": relationships,
        "characters": characters,
        "collections": collections,
        "originaltags": originaltags,
        "original": originaltags,
        "summary": summary,
        "word_count": words,
        "complete": all_complete,
    }
    return {
        "work_id": f"omnibus-{omnibus_id}",
        "title": title,
        "author": authors[0] if len(authors) == 1 else "",
        "authors": authors,
        "url": "",
        "source": "omnibus",
        "published": pubdate,
        "summary": summary,
        "identifiers": identifiers,
        "series": series_payload,
        "members": member_rows,
        "metadata": {
            "words": words,
            "published": pubdate,
            "complete": all_complete,
            "omnibus": True,
            "omnibus_kind": kind,
            "auto_update": bool(auto_update),
        },
        "cleaned": cleaned,
        "omnibus": {
            "id": omnibus_id,
            "kind": kind,
            "series_id": series_id or "",
            "collection": collection or "",
            "auto_update": bool(auto_update),
            "member_ids": member_ids,
        },
    }


def sort_collection_members(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order: shared series index, else pubdate, else title."""
    series_ids = set()
    for r in records:
        for s in r.get("series") or []:
            if isinstance(s, dict) and s.get("series_id"):
                series_ids.add(str(s["series_id"]))
        ids = r.get("identifiers") or {}
        if isinstance(ids, dict) and ids.get("ao3series"):
            series_ids.add(str(ids["ao3series"]))

    shared = len(series_ids) == 1

    def series_pos(r: dict[str, Any]) -> float:
        for s in r.get("series") or []:
            if isinstance(s, dict) and s.get("position") is not None:
                try:
                    return float(s["position"])
                except (TypeError, ValueError):
                    pass
        return 10**9

    def pub(r: dict[str, Any]) -> str:
        d = _parse_date(
            r.get("published")
            or (r.get("metadata") or {}).get("published")
            or (r.get("metadata") or {}).get("date")
        )
        return d.isoformat() if d else "9999-99-99"

    def title_key(r: dict[str, Any]) -> str:
        return str(r.get("title") or "").casefold()

    rows = list(records)
    if shared:
        rows.sort(key=lambda r: (series_pos(r), pub(r), title_key(r)))
    else:
        rows.sort(key=lambda r: (pub(r), title_key(r)))
    return rows


def specs_from_paths(
    paths: Sequence[str | Path],
    *,
    titles: Sequence[str] | None = None,
    member_ids: Sequence[str] | None = None,
    records: Sequence[dict[str, Any]] | None = None,
) -> list[MemberSpec]:
    out: list[MemberSpec] = []
    for i, path in enumerate(paths):
        path = Path(path)
        mid = (
            member_ids[i]
            if member_ids and i < len(member_ids)
            else (member_id_from_record(records[i]) if records and i < len(records) else path.stem)
        )
        title = (
            titles[i]
            if titles and i < len(titles)
            else (
                str(records[i].get("title") or mid)
                if records and i < len(records)
                else path.stem
            )
        )
        record = dict(records[i]) if records and i < len(records) else {"title": title, "work_id": mid}
        out.append(MemberSpec(member_id=str(mid), title=title, epub_path=path, record=record))
    return out


def combine_to_path(
    member_specs: Sequence[MemberSpec],
    dest: str | Path,
    *,
    kind: str = "selected",
    title: str | None = None,
    series_id: str = "",
    series_name: str = "",
    collection: str = "",
    auto_update: bool = False,
    skip_prefaces_after_first: bool = True,
    omnibus_id: str | None = None,
    append_to: str | Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    oid = omnibus_id or str(uuid.uuid4())
    dest = Path(dest)
    book_title = title or (member_specs[0].title if member_specs else "Omnibus")
    if append_to:
        path = append_members(
            append_to,
            member_specs,
            dest,
            skip_prefaces_after_first=skip_prefaces_after_first,
        )
        meta = read_omnibus_meta(path) or {}
        oid = str(meta.get("id") or oid)
        members = [m.record for m in member_specs]
        # merge with existing member snapshots for metadata
        existing = read_omnibus_members(path)
        active = [m for m in existing if m.get("active", True)]
        record = merge_omnibus_record(
            active,
            omnibus_id=oid,
            kind=str(meta.get("kind") or kind),
            title=str(meta.get("title") or book_title),
            series_id=str(meta.get("series_id") or series_id),
            series_name=series_name,
            collection=str(meta.get("collection") or collection),
            auto_update=bool(meta.get("auto_update", auto_update)),
        )
    else:
        path = merge_epubs(
            member_specs,
            dest,
            omnibus_id=oid,
            kind=kind,
            title=book_title,
            series_id=series_id,
            collection=collection,
            auto_update=auto_update,
            skip_prefaces_after_first=skip_prefaces_after_first,
        )
        record = merge_omnibus_record(
            [m.record for m in member_specs],
            omnibus_id=oid,
            kind=kind,
            title=book_title,
            series_id=series_id,
            series_name=series_name,
            collection=collection,
            auto_update=auto_update,
        )
    record["epub_file"] = dest.name
    _maybe_stamp_omnibus_cover(path, record)
    return path, record


def _maybe_stamp_omnibus_cover(epub_path: Path, record: dict[str, Any]) -> None:
    """Stamp a generated cover when cover.enabled (same as native downloads)."""
    try:
        from ao3kit.covers import maybe_stamp_downloaded_epub
    except ImportError:
        return
    maybe_stamp_downloaded_epub(Path(epub_path), record)


def run_combine_manifest(manifest_path: str | Path) -> dict[str, Any]:
    """Execute a plugin-written ``combine.json`` manifest."""
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    work = path.parent
    out_epub = work / "omnibus.epub"
    records: list[dict[str, Any]] = []
    records_jsonl = manifest.get("records_jsonl")
    if records_jsonl and Path(records_jsonl).is_file():
        for line in Path(records_jsonl).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    by_mid = {member_id_from_record(r): r for r in records}
    specs: list[MemberSpec] = []
    for row in manifest.get("members") or []:
        mid = str(row.get("member_id") or "")
        epub_path = Path(str(row.get("epub_path") or ""))
        if not mid or not epub_path.is_file():
            continue
        record = by_mid.get(mid) or {
            "work_id": mid,
            "title": row.get("title") or mid,
        }
        specs.append(
            MemberSpec(
                member_id=mid,
                title=str(row.get("title") or record.get("title") or mid),
                epub_path=epub_path,
                record=record,
            )
        )
    if not specs:
        raise ValueError("combine manifest has no usable members")
    append_to = str(manifest.get("append_epub") or "").strip() or None
    epub_path, record = combine_to_path(
        specs,
        out_epub,
        kind=str(manifest.get("kind") or "selected"),
        title=str(manifest.get("title") or "") or None,
        series_id=str(manifest.get("series_id") or ""),
        series_name=str(manifest.get("series_name") or ""),
        collection=str(manifest.get("collection") or ""),
        auto_update=bool(manifest.get("auto_update")),
        skip_prefaces_after_first=not bool(manifest.get("include_prefaces")),
        omnibus_id=str(manifest.get("omnibus_id") or "") or None,
        append_to=append_to,
    )
    record["epub_file"] = str(epub_path)
    jsonl_out = work / "omnibus.jsonl"
    jsonl_out.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    result = {
        "ok": True,
        "epub": str(epub_path),
        "jsonl": str(jsonl_out),
        "omnibus_id": (record.get("omnibus") or {}).get("id"),
        "remove_book_ids": list(manifest.get("remove_book_ids") or []),
        "existing_omnibus_book_id": manifest.get("existing_omnibus_book_id"),
    }
    (work / "combine_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="ao3kit epub", description="Combine and manage omnibus EPUBs")
    sub = parser.add_subparsers(dest="action", required=True)

    combine = sub.add_parser("combine", help="Merge EPUBs into an omnibus")
    combine.add_argument("--from", dest="sources", nargs="+", help="Input EPUB paths")
    combine.add_argument("--manifest", default="", help="Plugin combine.json manifest")
    combine.add_argument("--out", default="", help="Output omnibus EPUB path")
    combine.add_argument("--title", default="")
    combine.add_argument("--kind", default="selected", choices=("selected", "series", "collection"))
    combine.add_argument("--series-id", default="")
    combine.add_argument("--series-name", default="")
    combine.add_argument("--collection", default="")
    combine.add_argument("--member-id", dest="member_ids", action="append", default=[])
    combine.add_argument("--omnibus-id", default="")
    combine.add_argument("--append-to", default="", help="Existing omnibus to append into")
    combine.add_argument("--auto-update", action="store_true")
    combine.add_argument("--include-prefaces", action="store_true")
    combine.add_argument("--jsonl-out", default="", help="Write omnibus record JSONL")
    combine.add_argument("--records-jsonl", default="", help="Optional per-member metadata JSONL (same order)")

    explode = sub.add_parser("explode", help="Extract members to separate EPUBs")
    explode.add_argument("--from", dest="source", required=True)
    explode.add_argument("--out-dir", required=True)
    explode.add_argument("--jsonl-out", default="")

    rebuild = sub.add_parser("rebuild", help="Full rematerialize (breaks reader notes)")
    rebuild.add_argument("--from", dest="sources", nargs="+", required=True)
    rebuild.add_argument("--out", required=True)
    rebuild.add_argument("--title", default="")
    rebuild.add_argument("--kind", default="selected")
    rebuild.add_argument("--omnibus-id", default="")
    rebuild.add_argument("--include-prefaces", action="store_true")

    reorder = sub.add_parser("reorder", help="Spine/ToC reorder without rewriting paths")
    reorder.add_argument("--from", dest="source", required=True)
    reorder.add_argument("--out", default="")
    reorder.add_argument("--order", nargs="+", required=True, help="Member ids in order")

    shrink = sub.add_parser("remove", help="Remove members from an omnibus")
    shrink.add_argument("--from", dest="source", required=True)
    shrink.add_argument("--out", default="")
    shrink.add_argument("--member-id", dest="member_ids", nargs="+", required=True)

    sync = sub.add_parser("sync-collection", help="Append/remove members for a collection omnibus")
    sync.add_argument("--omnibus", required=True, help="Existing omnibus EPUB")
    sync.add_argument("--out", default="")
    sync.add_argument("--add-from", nargs="*", default=[], help="EPUB paths to append")
    sync.add_argument("--add-ids", nargs="*", default=[], help="Member ids for --add-from")
    sync.add_argument("--remove-ids", nargs="*", default=[], help="Member ids to remove")
    sync.add_argument("--records-jsonl", default="")

    args = parser.parse_args(argv)

    if args.action == "combine":
        if args.manifest:
            result = run_combine_manifest(args.manifest)
            print(json.dumps(result))
            return 0
        if not args.sources or not args.out:
            parser.error("combine requires --from/--out or --manifest")
        records = []
        if args.records_jsonl:
            for line in Path(args.records_jsonl).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        specs = specs_from_paths(
            args.sources,
            member_ids=args.member_ids or None,
            records=records or None,
        )
        path, record = combine_to_path(
            specs,
            args.out,
            kind=args.kind,
            title=args.title or None,
            series_id=args.series_id,
            series_name=args.series_name,
            collection=args.collection,
            auto_update=args.auto_update,
            skip_prefaces_after_first=not args.include_prefaces,
            omnibus_id=args.omnibus_id or None,
            append_to=args.append_to or None,
        )
        if args.jsonl_out:
            Path(args.jsonl_out).write_text(
                json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        print(json.dumps({"ok": True, "epub": str(path), "omnibus": record.get("omnibus")}))
        return 0

    if args.action == "explode":
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        meta = read_omnibus_meta(args.source) or {}
        members = [m for m in read_omnibus_members(args.source) if m.get("active", True)]
        rows = []
        for m in members:
            mid = str(m.get("member_id"))
            dest = out_dir / f"{mid}.epub"
            extract_member_epub(args.source, mid, dest, title=str(m.get("title") or mid))
            row = dict(m)
            row["epub_file"] = str(dest)
            rows.append(row)
        if args.jsonl_out:
            Path(args.jsonl_out).write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8",
            )
        print(json.dumps({"ok": True, "count": len(rows), "omnibus_id": meta.get("id")}))
        return 0

    if args.action == "rebuild":
        specs = specs_from_paths(args.sources)
        path = rebuild_epubs(
            specs,
            args.out,
            omnibus_id=args.omnibus_id or None,
            kind=args.kind,
            title=args.title or None,
            skip_prefaces_after_first=not args.include_prefaces,
        )
        print(json.dumps({"ok": True, "epub": str(path)}))
        return 0

    if args.action == "reorder":
        path = reorder_members(args.source, args.order, args.out or args.source)
        print(json.dumps({"ok": True, "epub": str(path)}))
        return 0

    if args.action == "remove":
        path = remove_members(args.source, args.member_ids, args.out or args.source)
        print(json.dumps({"ok": True, "epub": str(path)}))
        return 0

    if args.action == "sync-collection":
        dest = args.out or args.omnibus
        if args.remove_ids:
            remove_members(args.omnibus, args.remove_ids, dest)
        if args.add_from:
            records = []
            if args.records_jsonl:
                for line in Path(args.records_jsonl).read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            specs = specs_from_paths(
                args.add_from,
                member_ids=args.add_ids or None,
                records=records or None,
            )
            append_members(dest if Path(dest).is_file() else args.omnibus, specs, dest)
        print(json.dumps({"ok": True, "epub": str(dest)}))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
