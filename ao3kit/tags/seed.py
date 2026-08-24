"""Bundled tag-cache seed: popular AO3 synonym trees for first-run setup.

The release zip ships ``ao3kit/data/tag_cache_seed.json``. On first cache open
(or merge), trees are imported so simplify / enrich skips thousands of AO3 tag
profile fetches for common fandoms, ships, and freeforms.

Maintain with::

    python -m ao3kit tags seed build -o ao3kit/data/tag_cache_seed.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ao3kit.tags.cache import TagCache, _utc_now
from ao3kit.tags.metadata import (
    TagProfile,
    TagResolver,
    TagSearchCriteria,
    fetch_tag_search,
)

SEED_FORMAT = "ao3kit-tag-cache-seed"
SEED_VERSION = 3
SEED_REL_PATH = Path("data") / "tag_cache_seed.json"

TagType = Literal["Fandom", "Character", "Relationship", "Freeform", ""]
SEARCH_TYPES: tuple[TagType, ...] = ("Fandom", "Relationship", "Freeform", "Character")


@dataclass
class SeedBuildLimits:
    """How many canonical tags to discover per AO3 category."""

    fandoms: int = 60
    relationships: int = 80
    freeforms: int = 120
    characters: int = 60
    search_pages: int = 3


@dataclass
class SeedBuildStats:
    searched: dict[str, int] = field(default_factory=dict)
    canonical_targets: int = 0
    trees_written: int = 0
    entries_written: int = 0
    fetch_errors: list[str] = field(default_factory=list)


def bundled_seed_path() -> Path | None:
    """Path to the seed file shipped inside the ao3kit package."""
    try:
        import ao3kit

        candidate = Path(ao3kit.__file__).resolve().parent / SEED_REL_PATH
        if candidate.is_file():
            return candidate
    except ImportError:
        pass
    # repo layout during dev
    repo_candidate = Path(__file__).resolve().parent.parent / SEED_REL_PATH
    if repo_candidate.is_file():
        return repo_candidate
    return None


def load_seed_payload(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("seed file must be a JSON object")
    return data


def tree_from_profile(profile: TagProfile, *, fetched_at: str | None = None) -> dict[str, Any]:
    """One synonym tree from a fetched canonical (or unmarked) profile."""
    ts = fetched_at or _utc_now().isoformat()
    if profile.synonym_of is not None:
        canonical_name = profile.synonym_of.name
        root = canonical_name
        entries: list[dict[str, Any]] = [
            {
                "name": profile.name,
                "canonical": canonical_name,
                "status": "synonym",
                "category": profile.category,
            }
        ]
        return {"root": root, "fetched_at": ts, "entries": entries}

    if profile.canonical:
        root = profile.name
        mapping = profile.synonym_map()
        meta_names = profile.metatag_names()
        entries = []
        for name, canonical in mapping.items():
            status = "canonical" if name == canonical else "synonym"
            row: dict[str, Any] = {
                "name": name,
                "canonical": canonical,
                "status": status,
                "category": profile.category,
            }
            if status == "canonical":
                row["metatags"] = meta_names
            entries.append(row)
        if profile.name not in mapping:
            entries.append(
                {
                    "name": profile.name,
                    "canonical": profile.name,
                    "status": "canonical",
                    "category": profile.category,
                    "metatags": meta_names,
                }
            )
        return {"root": root, "fetched_at": ts, "entries": entries}

    root = profile.name
    return {
        "root": root,
        "fetched_at": ts,
        "entries": [
            {
                "name": profile.name,
                "canonical": profile.name,
                "status": "unmarked",
                "category": profile.category,
                "metatags": profile.metatag_names(),
            }
        ],
    }


def payload_from_cache(cache: TagCache, roots: list[str]) -> dict[str, Any]:
    """Serialize selected trees already stored in a cache."""
    trees: list[dict[str, Any]] = []
    entries_total = 0
    for root in sorted(set(roots)):
        rows = [
            r
            for r in cache.rows_for_canonical(root)
            if r.name == root or r.status != "canonical"
        ]
        if not rows and cache.get_row(root) is not None:
            rows = cache.rows_for_canonical(root)
        if not rows:
            single = cache.get_row(root)
            if single is None:
                continue
            rows = [single]
        fetched_at = _utc_now().isoformat()
        tree_entries: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {
                "name": row.name,
                "canonical": row.canonical,
                "status": row.status,
                "category": row.category,
            }
            if row.status == "canonical" and row.metatags is not None:
                item["metatags"] = row.metatags
            tree_entries.append(item)
        trees.append({"root": root, "fetched_at": fetched_at, "entries": tree_entries})
        entries_total += len(tree_entries)
    return {
        "version": SEED_VERSION,
        "format": SEED_FORMAT,
        "generated_at": _utc_now().isoformat(),
        "ttl_days": cache.ttl_days,
        "stats": {"trees": len(trees), "entries": entries_total},
        "trees": trees,
    }


def import_seed_payload(
    cache: TagCache,
    data: dict[str, Any],
    *,
    merge: bool = True,
) -> dict[str, int]:
    """Import tree-grouped seed data into a TagCache.

    When ``merge`` is true, existing names are left unchanged (user or warmer
  data wins). Returns counts of inserted rows and skipped names.
    """
    fmt = str(data.get("format") or "")
    version = int(data.get("version") or 0)
    if fmt == SEED_FORMAT and version == SEED_VERSION:
        trees = data.get("trees") or []
    elif version == 1 and not fmt:
        cache._import_json_payload(data)
        return {"inserted": -1, "skipped": 0, "trees": 0}
    else:
        raise ValueError(
            f"unsupported seed format (format={fmt!r}, version={version})"
        )

    inserted = 0
    skipped = 0
    trees_merged = 0
    conn = cache._open()
    rows: list[tuple[str, str, str, str | None, str, str, str | None]] = []

    for tree in trees:
        if not isinstance(tree, dict):
            continue
        root = str(tree.get("root") or "").strip()
        fetched_at = str(tree.get("fetched_at") or data.get("generated_at") or "")
        if not root:
            continue
        tree_entries = tree.get("entries") or []
        if not tree_entries:
            continue
        tree_had_insert = False
        for entry in tree_entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            canonical = str(entry.get("canonical") or "").strip()
            status = str(entry.get("status") or "").strip()
            if not name or not canonical or status not in {
                "canonical",
                "synonym",
                "unmarked",
            }:
                continue
            if merge and cache.lookup(name) is not None:
                skipped += 1
                continue
            category = entry.get("category")
            cat = str(category) if category else None
            meta = entry.get("metatags")
            meta_json: str | None = None
            if status == "canonical" and meta is not None:
                if isinstance(meta, list):
                    meta_json = json.dumps(
                        [str(m) for m in meta if str(m).strip()],
                        ensure_ascii=False,
                    )
                else:
                    meta_json = json.dumps([], ensure_ascii=False)
            entry_root = root if status != "unmarked" or name == root else name
            rows.append(
                (
                    name,
                    canonical,
                    status,
                    cat,
                    entry_root,
                    fetched_at or _utc_now().isoformat(),
                    meta_json,
                )
            )
            inserted += 1
            tree_had_insert = True
        if tree_had_insert:
            trees_merged += 1

    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO entries
              (name, canonical, status, category, root, fetched_at, metatags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        cache.dirty = True

    return {"inserted": inserted, "skipped": skipped, "trees": trees_merged}


def import_seed_file(
    cache: TagCache,
    path: Path,
    *,
    merge: bool = True,
) -> dict[str, int]:
    return import_seed_payload(cache, load_seed_payload(path), merge=merge)


def _search_canonical_names(
    tag_type: TagType,
    limit: int,
    *,
    max_pages: int,
    on_status: Any = None,
) -> list[str]:
    if limit <= 0:
        return []
    criteria = TagSearchCriteria(
        name="",
        type=tag_type,
        wrangling_status="canonical",
        sort_column="uses",
        sort_direction="desc",
    )
    names: list[str] = []
    seen: set[str] = set()
    page = 1
    while page <= max_pages and len(names) < limit:
        if on_status:
            on_status(f"Searching canonical {tag_type or 'tags'} (page {page})…")
        try:
            result = fetch_tag_search(criteria, page=page)
        except Exception as exc:  # noqa: BLE001 - continue with partial discovery
            if on_status:
                on_status(f"  search failed on page {page}: {exc}")
            break
        if not result.hits:
            break
        for hit in result.hits:
            if not hit.canonical or not hit.name:
                continue
            if hit.name in seen:
                continue
            seen.add(hit.name)
            names.append(hit.name)
            if len(names) >= limit:
                break
        page += 1
    return names


def discover_popular_canonicals(
    limits: SeedBuildLimits,
    *,
    on_status: Any = None,
) -> dict[str, list[str]]:
    """Return canonical tag names per category (sorted by AO3 uses)."""
    out: dict[str, list[str]] = {}
    type_limits = {
        "Fandom": limits.fandoms,
        "Relationship": limits.relationships,
        "Freeform": limits.freeforms,
        "Character": limits.characters,
    }
    for tag_type in SEARCH_TYPES:
        cap = type_limits.get(tag_type, 0)
        names = _search_canonical_names(
            tag_type,
            cap,
            max_pages=limits.search_pages,
            on_status=on_status,
        )
        out[tag_type or "all"] = names
        if on_status:
            on_status(f"  {tag_type or 'all'}: {len(names)} canonical names")
        time.sleep(2.0)
    return out


def build_seed_payload(
    limits: SeedBuildLimits | None = None,
    *,
    on_status: Any = None,
    resume_from: Path | None = None,
    output_path: Path | None = None,
) -> tuple[dict[str, Any], SeedBuildStats]:
    """Fetch popular canonical profiles and assemble a seed payload."""
    limits = limits or SeedBuildLimits()
    stats = SeedBuildStats()
    existing_roots: set[str] = set()
    existing_trees: list[dict[str, Any]] = []
    generated_at = _utc_now().isoformat()
    if resume_from is not None and resume_from.is_file():
        prior = load_seed_payload(resume_from)
        for tree in prior.get("trees") or []:
            if not isinstance(tree, dict):
                continue
            root = str(tree.get("root") or "").strip()
            if root:
                existing_roots.add(root)
                existing_trees.append(tree)
        generated_at = str(prior.get("generated_at") or generated_at)
        if on_status:
            on_status(f"Resuming: {len(existing_roots)} trees already built")

    discovered = discover_popular_canonicals(limits, on_status=on_status)
    for key, names in discovered.items():
        stats.searched[key] = len(names)

    targets: list[str] = []
    seen: set[str] = set()
    for names in discovered.values():
        for name in names:
            if name not in seen:
                seen.add(name)
                targets.append(name)
    stats.canonical_targets = len(targets)

    trees: list[dict[str, Any]] = list(existing_trees)
    tree_roots: set[str] = set(existing_roots)
    stats.trees_written = len(existing_trees)
    stats.entries_written = sum(
        len(t.get("entries") or []) for t in existing_trees if isinstance(t, dict)
    )

    with TagResolver(
        persist=False,
        follow_canonical=True,
        on_status=on_status,
    ) as resolver:
        for idx, name in enumerate(targets, start=1):
            if on_status:
                on_status(f"Fetching profile {idx}/{len(targets)}: {name}")
            profile = resolver._fetch_profile(name)
            if profile is None:
                err = resolver._errors.get(name, "fetch failed")
                stats.fetch_errors.append(f"{name}: {err}")
                continue
            if profile.synonym_of is not None:
                canonical = profile.synonym_of.name
                followed = resolver._follow_canonical(canonical)
                if followed is not None:
                    profile = followed
                elif canonical in resolver._profiles:
                    profile = resolver._profiles[canonical]
                else:
                    refetched = resolver._fetch_profile(canonical, followed=True)
                    if refetched is not None:
                        profile = refetched
                    else:
                        stats.fetch_errors.append(
                            f"{name}: could not follow canonical {canonical}"
                        )
                        continue

            if profile.canonical:
                root = profile.name
            elif profile.synonym_of is not None:
                root = profile.synonym_of.name
            else:
                root = profile.name

            if root in tree_roots:
                continue
            tree = tree_from_profile(profile, fetched_at=generated_at)
            actual_root = str(tree.get("root") or root)
            if actual_root in tree_roots:
                continue
            tree_roots.add(actual_root)
            trees.append(tree)
            stats.trees_written += 1
            stats.entries_written += len(tree.get("entries") or [])
            if output_path is not None:
                partial = {
                    "version": SEED_VERSION,
                    "format": SEED_FORMAT,
                    "generated_at": generated_at,
                    "ttl_days": 90,
                    "stats": {
                        "searched": stats.searched,
                        "canonical_targets": stats.canonical_targets,
                        "trees": stats.trees_written,
                        "entries": stats.entries_written,
                        "fetch_errors": len(stats.fetch_errors),
                        "partial": True,
                    },
                    "trees": trees,
                }
                write_seed_payload(output_path, partial)

    payload = {
        "version": SEED_VERSION,
        "format": SEED_FORMAT,
        "generated_at": generated_at,
        "ttl_days": 90,
        "stats": {
            "searched": stats.searched,
            "canonical_targets": stats.canonical_targets,
            "trees": stats.trees_written,
            "entries": stats.entries_written,
            "fetch_errors": len(stats.fetch_errors),
        },
        "trees": trees,
    }
    return payload, stats


def write_seed_payload(path: Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def seed_stats_payload(path: Path | None = None) -> dict[str, Any]:
    seed_path = path or bundled_seed_path()
    if seed_path is None:
        return {"path": None, "exists": False}
    data = load_seed_payload(seed_path)
    trees = data.get("trees") or []
    entries = sum(len(t.get("entries") or []) for t in trees if isinstance(t, dict))
    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for tree in trees:
        if not isinstance(tree, dict):
            continue
        for entry in tree.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "")
            by_status[status] = by_status.get(status, 0) + 1
            cat = str(entry.get("category") or "Unknown")
            by_category[cat] = by_category.get(cat, 0) + 1
    return {
        "path": str(seed_path),
        "exists": True,
        "version": data.get("version"),
        "format": data.get("format"),
        "generated_at": data.get("generated_at"),
        "stats": data.get("stats"),
        "trees": len(trees),
        "entries": entries,
        "by_status": by_status,
        "by_category": by_category,
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Bundled AO3 tag-cache seed")
    sub = parser.add_subparsers(dest="command", required=True)

    build_p = sub.add_parser(
        "build",
        help="Fetch popular canonical tags from AO3 and write a seed JSON file",
    )
    build_p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent.parent / SEED_REL_PATH,
        help="Output path (default: ao3kit/data/tag_cache_seed.json)",
    )
    build_p.add_argument("--max-fandoms", type=int, default=60)
    build_p.add_argument("--max-relationships", type=int, default=80)
    build_p.add_argument("--max-freeforms", type=int, default=120)
    build_p.add_argument("--max-characters", type=int, default=60)
    build_p.add_argument("--search-pages", type=int, default=3)
    build_p.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="Resume from a partial seed file (skip roots already present)",
    )
    build_p.add_argument("--verbose", action="store_true")

    import_p = sub.add_parser("import", help="Import a seed file into the tag cache")
    import_p.add_argument("seed", type=Path, nargs="?", default=None)
    import_p.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Tag cache SQLite path (default: XDG cache)",
    )
    import_p.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing names (default: merge, skip cached names)",
    )

    stats_p = sub.add_parser("stats", help="Show bundled or file seed statistics")
    stats_p.add_argument("seed", type=Path, nargs="?", default=None)

    args = parser.parse_args(argv)
    on_status = (lambda msg: print(msg, file=sys.stderr)) if getattr(args, "verbose", False) else None

    if args.command == "build":
        limits = SeedBuildLimits(
            fandoms=args.max_fandoms,
            relationships=args.max_relationships,
            freeforms=args.max_freeforms,
            characters=args.max_characters,
            search_pages=args.search_pages,
        )
        payload, stats = build_seed_payload(
            limits,
            on_status=on_status,
            resume_from=args.resume,
            output_path=args.output,
        )
        out = write_seed_payload(args.output, payload)
        print(f"Wrote {out} ({stats.trees_written} trees, {stats.entries_written} entries)")
        if stats.fetch_errors and on_status:
            print(f"{len(stats.fetch_errors)} fetch errors", file=sys.stderr)
        return 0

    if args.command == "import":
        from ao3kit.tags.cache import default_tag_cache_path

        seed_path = args.seed or bundled_seed_path()
        if seed_path is None or not seed_path.is_file():
            print("No seed file found", file=sys.stderr)
            return 1
        cache_path = args.cache or default_tag_cache_path()
        cache = TagCache.load(cache_path)
        result = import_seed_file(cache, seed_path, merge=not args.replace)
        cache.save()
        cache.close()
        print(json.dumps({"path": str(seed_path), "cache": str(cache_path), **result}))
        return 0

    if args.command == "stats":
        info = seed_stats_payload(args.seed)
        json.dump(info, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
