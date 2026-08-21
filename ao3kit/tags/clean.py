"""Enrich scrape/work records with cleaned tag metadata (canonical + rules)."""

from __future__ import annotations

from typing import Any, Callable

from ao3kit.config import load_user_config
from ao3kit.rate import current_tag_interval
from ao3kit.tags.metadata import ResolvedTag, TagResolver
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
    return lines


def collect_unique_tag_names(
    records: list[dict[str, Any]],
    *,
    include_fandoms: bool = True,
) -> list[str]:
    """Stable-ordered unique tag/fandom names across a scrape batch."""
    seen: set[str] = set()
    ordered: list[str] = []
    for record in records:
        names = _as_name_list(record.get("tags"))
        if include_fandoms:
            names = names + _as_name_list(record.get("fandoms"))
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
    return ordered


def enrich_record(
    record: dict[str, Any],
    engine: TagRulesEngine,
    *,
    include_fandoms: bool = True,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Return a copy of ``record`` with a ``cleaned`` object from the rules engine."""
    out = dict(record)
    raw_tags = _as_name_list(record.get("tags"))
    tag_result = engine.apply(raw_tags)

    cleaned: dict[str, Any] = {
        "simplified": list(tag_result.simplified),
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

    if on_status:
        title = record.get("title") or record.get("work_id") or "?"
        changes = describe_ruled_result(tag_result)
        if fandom_result is not None:
            for line in describe_ruled_result(fandom_result):
                changes.append(f"(fandom) {line}")
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
        eta_s = len(missing) * pace
        if eta_s >= 90:
            eta = f"~{eta_s / 60:.0f} min"
        else:
            eta = f"~{eta_s:.0f}s"
        on_status(
            f"{len(names)} unique tags across batch "
            f"({cached} already cached, {len(missing)} need AO3"
            + (f", ETA {eta} at {pace:.1f}s/tag)" if missing else ")")
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
    on_status: StatusCallback | None = None,
    delay: float | None = None,
) -> list[dict[str, Any]]:
    """Run tag simplification over scrape records using user rules + AO3 resolver.

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

    if resolver is None:
        request_delay = (
            delay if delay is not None else user_cfg.settings.request_delay
        )
        from ao3kit.tags.metadata import DEFAULT_TAG_CACHE_PATH

        use_cache = user_cfg.settings.tag_cache_enabled
        resolver = TagResolver(
            delay=request_delay,
            on_status=on_status,
            cache_path=DEFAULT_TAG_CACHE_PATH if use_cache else None,
            follow_canonical=user_cfg.settings.follow_canonical,
            persist=use_cache,
            ttl_days=user_cfg.settings.tag_cache_ttl_days,
        )

    engine = TagRulesEngine(rules, resolver)
    try:
        unique = collect_unique_tag_names(records, include_fandoms=include_fandoms)
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
                    on_status=on_status,
                )
            )
        if on_status:
            on_status(f"Cache stats: {resolver.stats.to_dict()}")
        return enriched
    finally:
        if owns_resolver:
            resolver.close()
