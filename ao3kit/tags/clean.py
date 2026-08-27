"""Enrich scrape/work records with cleaned tag metadata (canonical + rules)."""

from __future__ import annotations

from typing import Any, Callable

from ao3kit.config import load_user_config
from ao3kit.rate import current_tag_interval
from ao3kit.tags.metadata import (
    ResolvedTag,
    TagResolver,
    is_relationship_category,
)
from ao3kit.tags.rules import RuledTag, RuledTagsResult, TagRulesConfig, TagRulesEngine


StatusCallback = Callable[[str], None]


def _as_name_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    if isinstance(value, dict) and "simplified" in value:
        return [str(item) for item in (value.get("simplified") or [])]
    return []


def format_ao3_mapping(resolved: ResolvedTag) -> str | None:
    """Human line for an AO3 canonical remap, or None if unchanged."""
    if not resolved.changed:
        return None
    return f"{resolved.original} → {resolved.resolved}"


def format_rule_mapping(item: RuledTag) -> str | None:
    """Human line for a user-rule / drop decision, or None if nothing notable."""
    rule = item.mapping_rule or "rules"
    if item.mapping_action == "map_to" and item.mapped:
        return f"{item.original} → {item.mapped}  [{rule}]"
    if item.mapping_action == "keep_separate":
        return f"{item.original} → {item.original}  [{rule}: keep separate]"
    if item.mapping_action == "drop":
        return f"{item.original} → (dropped)  [{rule}]"
    if item.dropped and item.mapping_action == "default":
        reason = item.status or "policy"
        return f"{item.original} → (dropped)  [{reason}]"
    return None


def format_collection_mapping(item: RuledTag) -> str | None:
    if not item.collections:
        return None
    rules = ", ".join(item.applied_rules) if item.applied_rules else "rules"
    joined = ", ".join(item.collections)
    return f"{item.original} → collections: {joined}  [{rules}]"


def collection_names_from_result(result: RuledTagsResult) -> list[str]:
    """Ordered unique collection names from a rules-engine result."""
    names: list[str] = []
    seen: set[str] = set()
    for name in result.collections:
        text = str(name).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(text)
    return names


def merge_collection_maps(*maps: dict[str, list[str]] | None) -> dict[str, list[str]]:
    """Union collection-name → source-tag maps, preserving first-seen order."""
    out: dict[str, list[str]] = {}
    for mapping in maps:
        if not mapping:
            continue
        for name, sources in mapping.items():
            text = str(name).strip()
            if not text:
                continue
            bucket = out.setdefault(text, [])
            for src in sources or []:
                src_text = str(src).strip()
                if src_text and src_text not in bucket:
                    bucket.append(src_text)
    return out


def describe_ruled_result(result: RuledTagsResult) -> list[str]:
    """Status lines for rule/drop/collection outcomes on one work."""
    lines: list[str] = []
    for item in result.tags:
        mapped = format_rule_mapping(item)
        if mapped:
            lines.append(mapped)
        collected = format_collection_mapping(item)
        if collected:
            lines.append(collected)
        # AO3 synonym collapse when no rule remapped it.
        if (
            item.mapping_action == "default"
            and not item.dropped
            and item.mapped
            and item.mapped != item.original
        ):
            lines.append(f"{item.original} → {item.mapped}  [AO3 {item.status}]")
        for meta in item.metatags:
            if meta:
                source = item.mapped or item.original
                lines.append(f"{source} → +{meta}  [metatag]")
    return lines


def _remap_rows_from_tag_item(item: Any) -> list[tuple[str, str, str]]:
    """Return ``(original, mapped, source)`` rows for remaps and metatag inserts."""
    rows: list[tuple[str, str, str]] = []
    if isinstance(item, RuledTag):
        original = item.original
        mapped = item.mapped
        action = item.mapping_action
        dropped = item.dropped
        status = item.status
        rule = item.mapping_rule
        metatags = list(item.metatags)
    elif isinstance(item, dict):
        original = str(item.get("original") or "").strip()
        mapped = item.get("mapped")
        mapped = str(mapped).strip() if mapped is not None else None
        action = str(item.get("mapping_action") or "default")
        dropped = bool(item.get("dropped"))
        status = str(item.get("status") or "")
        rule = item.get("mapping_rule")
        raw_meta = item.get("metatags") or []
        metatags = [str(name) for name in raw_meta if str(name).strip()]
    else:
        return rows
    if not original:
        return rows
    if dropped or action == "drop":
        rows.append((original, "(dropped)", str(rule or status or "drop")))
    elif mapped and mapped != original:
        if action == "map_to":
            source = str(rule or "rules")
        else:
            source = f"AO3 {status}" if status else "AO3"
        rows.append((original, mapped, source))
    for meta in metatags:
        if meta and meta != original:
            rows.append((original, f"+{meta}", "metatag"))
    return rows


def collect_remapping_lines(records: list[dict[str, Any]]) -> list[str]:
    """Unique ``before → after`` remaps across a batch, with work counts."""
    counts: dict[tuple[str, str, str], int] = {}
    order: list[tuple[str, str, str]] = []
    for record in records:
        cleaned = record.get("cleaned")
        if not isinstance(cleaned, dict):
            continue
        items = [item for item in (cleaned.get("tags") or [])]
        fandom_detail = cleaned.get("fandoms_detail")
        if isinstance(fandom_detail, dict):
            items.extend(fandom_detail.get("tags") or [])
        rel_detail = cleaned.get("relationships_detail")
        if isinstance(rel_detail, dict):
            items.extend(rel_detail.get("tags") or [])
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            for row in _remap_rows_from_tag_item(item):
                if row in seen:
                    continue
                seen.add(row)
                if row not in counts:
                    order.append(row)
                    counts[row] = 0
                counts[row] += 1
    lines: list[str] = []
    for row in order:
        original, mapped, source = row
        extra = f"  ({counts[row]} works)" if counts[row] > 1 else ""
        lines.append(f"{original} → {mapped}  [{source}]{extra}")
    return lines


def emit_remapping_summary(
    records: list[dict[str, Any]],
    on_status: StatusCallback | None,
) -> list[str]:
    lines = collect_remapping_lines(records)
    if on_status is None:
        return lines
    if not lines:
        on_status("Tag remappings: none (all tags already canonical)")
        return lines
    on_status(f"Tag remappings ({len(lines)} unique):")
    for line in lines:
        on_status(f"  {line}")
    return lines


def format_remapping_summary(records: list[dict[str, Any]]) -> str:
    """Multi-line summary of unique remaps across a batch."""
    lines = collect_remapping_lines(records)
    if not lines:
        return "Tag remappings: none (all tags already canonical)"
    return "Tag remappings ({} unique):\n{}".format(
        len(lines),
        "\n".join(f"  {line}" for line in lines),
    )


def collect_unique_tag_names(
    records: list[dict[str, Any]],
    *,
    include_fandoms: bool = True,
    include_relationships: bool = True,
    extra_keys: tuple[str, ...] = (),
) -> list[str]:
    """Stable-ordered unique tag/fandom/relationship names across a scrape batch."""
    keys = ["tags"]
    if include_fandoms:
        keys.append("fandoms")
    if include_relationships:
        keys.append("relationships")
    keys.extend(extra_keys)
    seen: set[str] = set()
    ordered: list[str] = []
    for record in records:
        names: list[str] = []
        for key in keys:
            names.extend(_as_name_list(record.get(key)))
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
    return ordered


def _name_key(value: str) -> str:
    return value.casefold()


def _is_relationship_item(item: RuledTag) -> bool:
    return is_relationship_category(item.category)


def _ruled_result_from_items(
    items: list[RuledTag],
    *,
    original: list[str] | None = None,
    inserted_metatags: list[str] | None = None,
) -> RuledTagsResult:
    originals = list(original) if original is not None else [item.original for item in items]
    simplified: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    collections: dict[str, list[str]] = {}
    for item in items:
        for collection in item.collections:
            bucket = collections.setdefault(collection, [])
            if item.original not in bucket:
                bucket.append(item.original)
        if item.dropped or item.mapped is None:
            dropped.append(item.original)
            continue
        if item.mapped in seen:
            dropped.append(item.original)
            continue
        seen.add(item.mapped)
        simplified.append(item.mapped)
        for meta in item.metatags:
            if not meta or meta in seen:
                continue
            seen.add(meta)
            simplified.append(meta)
    return RuledTagsResult(
        original=originals,
        tags=items,
        simplified=simplified,
        dropped=dropped,
        collections=collections,
        inserted_metatags=list(inserted_metatags or []),
    )


def enrich_record(
    record: dict[str, Any],
    engine: TagRulesEngine,
    *,
    include_fandoms: bool = True,
    include_relationships: bool = True,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Return a copy of ``record`` with a ``cleaned`` object from the rules engine.

    Tags, fandoms, and relationships each get AO3 canonical collapse plus user
    rules. Fandom metatags are appended to the fandom list only. Relationship
    tags found in the work's tag list are moved onto ``cleaned.relationships``.
    """
    out = dict(record)
    raw_tags = _as_name_list(record.get("tags"))
    # Metatags belong on the fandom list / Calibre Fandom column, never Tags.
    tag_result = engine.apply(raw_tags, include_metatags=False)

    kept_tag_items: list[RuledTag] = []
    rel_from_tags: list[RuledTag] = []
    for item in tag_result.tags:
        if include_relationships and _is_relationship_item(item):
            rel_from_tags.append(item)
        else:
            kept_tag_items.append(item)
    tag_view = _ruled_result_from_items(
        kept_tag_items, original=list(tag_result.original)
    )

    cleaned: dict[str, Any] = {
        "simplified": list(tag_view.simplified),
        "collections": dict(tag_result.collections),
        "dropped": list(tag_result.dropped),
        "original": list(tag_result.original),
        "tags": [item.to_dict() for item in tag_result.tags],
        "source": "rules",
    }

    fandom_result: RuledTagsResult | None = None
    if include_fandoms:
        raw_fandoms = _as_name_list(record.get("fandoms"))
        if raw_fandoms:
            fandom_result = engine.apply(raw_fandoms)
            cleaned["fandoms"] = list(fandom_result.simplified)
            cleaned["fandoms_detail"] = fandom_result.to_dict()
        else:
            cleaned["fandoms"] = []

    rel_extra: RuledTagsResult | None = None
    rel_result: RuledTagsResult | None = None
    if include_relationships:
        raw_rels = _as_name_list(record.get("relationships"))
        seen_rel = {_name_key(item.original) for item in rel_from_tags}
        extra_names = [name for name in raw_rels if _name_key(name) not in seen_rel]
        if extra_names:
            rel_extra = engine.apply(extra_names, include_metatags=False)
        rel_items = list(rel_from_tags)
        if rel_extra is not None:
            rel_items.extend(
                item for item in rel_extra.tags if _is_relationship_item(item)
            )
        rel_originals = [item.original for item in rel_from_tags] + extra_names
        if rel_items or raw_rels or rel_from_tags:
            rel_result = _ruled_result_from_items(
                rel_items, original=rel_originals
            )
            cleaned["relationships"] = list(rel_result.simplified)
            # Only extra (column-only) items go in detail so remapping
            # summaries do not double-count ships that already appear in tags.
            if rel_extra is not None:
                cleaned["relationships_detail"] = rel_extra.to_dict()
        else:
            cleaned["relationships"] = []

    cleaned["collections"] = merge_collection_maps(
        tag_result.collections,
        None if fandom_result is None else fandom_result.collections,
        None if rel_result is None else rel_result.collections,
    )

    if on_status:
        title = record.get("title") or record.get("work_id") or "?"
        changes = describe_ruled_result(tag_result)
        if fandom_result is not None:
            for line in describe_ruled_result(fandom_result):
                changes.append(f"(fandom) {line}")
        if rel_extra is not None:
            for line in describe_ruled_result(rel_extra):
                changes.append(f"(relationship) {line}")
        if changes:
            on_status(f"Mappings for {title}:")
            for line in changes:
                on_status(f"  {line}")
        else:
            on_status(f"Mappings for {title}: (no remaps)")

    cleaned["work_id"] = record.get("work_id")
    cleaned["title"] = record.get("title")
    out["cleaned"] = cleaned
    return out


def _prefetch_unique_tags(
    resolver: TagResolver,
    names: list[str],
    *,
    on_status: StatusCallback | None = None,
) -> tuple[int, int]:
    """Resolve unique names once up front. Returns (cached_hits, network_fetches_planned)."""
    cached = 0
    missing: list[str] = []
    for name in names:
        if resolver.cache.lookup(name) is not None:
            cached += 1
        else:
            missing.append(name)

    if on_status:
        pace = current_tag_interval()
        eta_s = len(missing) * (pace + 1.0)
        if eta_s >= 90:
            eta = f"~{eta_s / 60:.0f} min"
        else:
            eta = f"~{eta_s:.0f}s"
        on_status(
            f"{len(names)} unique tags across batch "
            f"({cached} already cached, {len(missing)} need AO3"
            + (
                f", ETA {eta} at ~{pace:.1f}s between tag requests)"
                if missing
                else ")"
            )
        )

    total_missing = len(missing)
    for index, name in enumerate(missing, start=1):
        if on_status:
            on_status(f"Resolving tags [{index}/{total_missing}] {name}")
        resolved = resolver.resolve_one(name)
        line = format_ao3_mapping(resolved)
        if on_status and line:
            on_status(f"  {line}  [{resolved.status}]")
        # Persist incrementally so a cancel mid-run still keeps progress.
        if resolver.persist and index % 20 == 0:
            resolver.cache.save()
            if on_status:
                on_status(f"Saved tag cache checkpoint ({index}/{total_missing})")

    if resolver.persist and missing:
        resolver.cache.save()

    # Report remaps for the whole unique set (includes cache hits).
    if on_status:
        remaps: list[str] = []
        for name in names:
            resolved = resolver.resolve_one(name)
            line = format_ao3_mapping(resolved)
            if line:
                remaps.append(f"{line}  [{resolved.status}]")
        if remaps:
            on_status(f"AO3 remapped {len(remaps)}/{len(names)} unique tags:")
            for line in remaps:
                on_status(f"  {line}")
        else:
            on_status("AO3 remapped 0 unique tags (all already canonical or unmarked)")

    return cached, total_missing


def enrich_records(
    records: list[dict[str, Any]],
    *,
    rules: TagRulesConfig | None = None,
    resolver: TagResolver | None = None,
    include_fandoms: bool = True,
    include_relationships: bool = True,
    on_status: StatusCallback | None = None,
) -> list[dict[str, Any]]:
    """Run tag/fandom/relationship simplification using user rules + AO3 resolver.

    Cost is dominated by **unique uncached tags**, not book count: names are
    prefetched once, then each work is cleaned from cache/memory.
    """
    owns_resolver = resolver is None
    user_cfg = load_user_config(ensure=True)
    if rules is None:
        rules = user_cfg.load_active_rules()
        rules.resolve_canonical = user_cfg.settings.resolve_canonical
        rules.drop_unmarked = user_cfg.settings.drop_unmarked
        rules.drop_errors = user_cfg.settings.drop_errors
        rules.include_metatags = user_cfg.settings.include_metatags

    if resolver is None:
        from ao3kit.tags.cache import default_tag_cache_path

        use_cache = user_cfg.settings.tag_cache_enabled
        resolver = TagResolver(
            on_status=on_status,
            cache_path=default_tag_cache_path() if use_cache else None,
            follow_canonical=user_cfg.settings.follow_canonical,
            persist=use_cache,
            ttl_days=user_cfg.settings.tag_cache_ttl_days,
        )

    engine = TagRulesEngine(rules, resolver)
    try:
        unique = collect_unique_tag_names(
            records,
            include_fandoms=include_fandoms,
            include_relationships=include_relationships,
        )
        if unique:
            _prefetch_unique_tags(resolver, unique, on_status=on_status)

        enriched: list[dict[str, Any]] = []
        total = len(records)
        for index, record in enumerate(records, start=1):
            title = record.get("title") or record.get("work_id") or "?"
            if on_status:
                on_status(f"Applying rules [{index}/{total}] {title}")
            enriched.append(
                enrich_record(
                    record,
                    engine,
                    include_fandoms=include_fandoms,
                    include_relationships=include_relationships,
                    on_status=on_status,
                )
            )
        from ao3kit.tags.collections import recompute_records

        overlayed, coll_rules, pins = recompute_records(
            enriched,
            user_cfg.load_collection_rules(),
            remember_adds=bool(user_cfg.settings.collections_remember_manual_adds),
        )
        if pins:
            user_cfg.save_collection_rules(coll_rules)
            if on_status:
                on_status(
                    f"Saved {len(pins)} collection pin(s) from hand-added membership."
                )
        enriched = overlayed
        if on_status:
            on_status(f"Cache stats: {resolver.stats.to_dict()}")
        emit_remapping_summary(enriched, on_status)
        return enriched
    finally:
        if owns_resolver:
            resolver.close()
