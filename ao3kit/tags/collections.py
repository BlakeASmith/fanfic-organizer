"""Collection membership computed from rules (plus optional per-work pins).

Collections on a work are a **view of rules**, not a separate stored truth.
Recompute anytime. A collection the user adds by hand on one work becomes a
pin rule for that work, so the next recompute puts it back.

Tag-transform mappings (keep / rename / drop) stay in ``mappings.yaml``.
Collection rules live in ``collections.yaml`` under the XDG config dir.
Mapping rows that still
list collections keep working through the tag engine; this module overlays
YAML rules (including pins and excludes) on top.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from ao3kit.tags.mappings import allocate_id, parse_csv, slugify

COLLECTIONS_FILENAME = "collections.yaml"
_SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_WORK_ID_RE = re.compile(r"/works/(\d+)")

CollectionMatch = Literal[
    "mentions",
    "is_ci",
    "fandom_mentions",
    "author_ci",
    "work_id",
    "calibre_uuid",
]
CollectionMode = Literal["include", "exclude"]

MATCH_KINDS: tuple[CollectionMatch, ...] = (
    "mentions",
    "is_ci",
    "fandom_mentions",
    "author_ci",
    "work_id",
    "calibre_uuid",
)
MODES: tuple[CollectionMode, ...] = ("include", "exclude")

MATCH_CHOICES: tuple[tuple[CollectionMatch, str], ...] = (
    ("mentions", "tag contains"),
    ("is_ci", "tag is exactly"),
    ("fandom_mentions", "fandom contains"),
    ("author_ci", "author is"),
    ("work_id", "this AO3 work"),
    ("calibre_uuid", "this book"),
)
MODE_CHOICES: tuple[tuple[CollectionMode, str], ...] = (
    ("include", "Put matching books in"),
    ("exclude", "Never put matching books in"),
)

_MATCH_LABELS = dict(MATCH_CHOICES)
_MODE_LABELS = dict(MODE_CHOICES)


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def unique_names(values: Iterable[Any] | None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        name = str(value).strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _as_name_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return unique_names([value])
    if isinstance(value, dict):
        if "simplified" in value:
            return unique_names(value.get("simplified") or [])
        return unique_names(value.keys())
    if isinstance(value, (list, tuple, set)):
        names: list[str] = []
        for item in value:
            if isinstance(item, dict):
                names.extend(
                    [
                        str(item.get("mapped") or "").strip(),
                        str(item.get("original") or "").strip(),
                    ]
                )
            else:
                names.append(str(item).strip())
        return unique_names(names)
    return unique_names([value])


def work_id_of(record: dict[str, Any]) -> str:
    work_id = str(record.get("work_id") or "").strip()
    if work_id:
        return work_id
    url = str(record.get("url") or "")
    match = _WORK_ID_RE.search(url)
    return match.group(1) if match else ""


def calibre_uuid_of(record: dict[str, Any]) -> str:
    uuid = str(record.get("calibre_uuid") or "").strip()
    if uuid:
        return uuid
    ids = record.get("identifiers") or {}
    if isinstance(ids, dict):
        return str(ids.get("uuid") or "").strip()
    return ""


def authors_of(record: dict[str, Any]) -> list[str]:
    authors = record.get("authors")
    if authors:
        return _as_name_list(authors)
    author = record.get("author")
    return _as_name_list([author] if author else [])


def fandoms_of(record: dict[str, Any]) -> list[str]:
    names = _as_name_list(record.get("fandoms"))
    cleaned = record.get("cleaned")
    if isinstance(cleaned, dict):
        names = unique_names([*names, *_as_name_list(cleaned.get("fandoms"))])
        detail = cleaned.get("fandoms_detail")
        if isinstance(detail, dict):
            names = unique_names([*names, *_as_name_list(detail.get("tags"))])
    return names


def relationships_of(record: dict[str, Any]) -> list[str]:
    names = _as_name_list(record.get("relationships"))
    cleaned = record.get("cleaned")
    if isinstance(cleaned, dict):
        names = unique_names([*names, *_as_name_list(cleaned.get("relationships"))])
        detail = cleaned.get("relationships_detail")
        if isinstance(detail, dict):
            names = unique_names([*names, *_as_name_list(detail.get("tags"))])
    return names


def engine_collection_names(record: dict[str, Any]) -> list[str]:
    cleaned = record.get("cleaned")
    if not isinstance(cleaned, dict):
        return []
    return _as_name_list(cleaned.get("collections"))


def names_for_collection_match(record: dict[str, Any]) -> list[str]:
    """Tag-like names a 'tag contains / is exactly' rule can see."""
    names = [
        * _as_name_list(record.get("tags")),
        *fandoms_of(record),
        *relationships_of(record),
        *_as_name_list(record.get("characters")),
    ]
    cleaned = record.get("cleaned")
    if isinstance(cleaned, dict):
        names.extend(_as_name_list(cleaned.get("simplified")))
        names.extend(_as_name_list(cleaned.get("original")))
        names.extend(_as_name_list(cleaned.get("tags")))
        names.extend(_as_name_list(cleaned.get("fandoms")))
        names.extend(_as_name_list(cleaned.get("relationships")))
    return unique_names(names)


def current_collections_of(record: dict[str, Any]) -> list[str] | None:
    """Calibre column snapshot when present (None = do not capture pins)."""
    if "current_collections" not in record:
        return None
    return _as_name_list(record.get("current_collections"))


def _contains(names: Sequence[str], needle: str) -> bool:
    n = _norm(needle)
    if not n:
        return False
    for name in names:
        hay = _norm(name)
        if n == hay or n in hay:
            return True
    return False


def _exact(names: Sequence[str], needle: str) -> bool:
    n = _norm(needle)
    return bool(n) and any(_norm(name) == n for name in names)


@dataclass
class CollectionRule:
    """One include/exclude rule that maps matching works onto a collection."""

    id: str
    match: CollectionMatch = "mentions"
    values: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    mode: CollectionMode = "include"
    enabled: bool = True
    pin: bool = False
    description: str = ""

    def validate(self) -> None:
        if not self.id or not _SAFE_ID.match(self.id):
            raise ValueError(
                f"Invalid collection rule id {self.id!r}: use letters, numbers, _ or -"
            )
        if self.match not in MATCH_KINDS:
            raise ValueError(f"Unknown collection match {self.match!r}")
        if self.mode not in MODES:
            raise ValueError(f"Unknown collection mode {self.mode!r}")
        if not self.values:
            raise ValueError("Collection rule needs something to match")
        if not self.collections:
            raise ValueError("Collection rule needs a collection name")

    def suggested_id(self) -> str:
        prefix = "pin" if self.pin else ("never" if self.mode == "exclude" else "in")
        key = self.values[0] if self.values else self.id
        coll = self.collections[0] if self.collections else "collection"
        return f"{prefix}-{self.match}-{slugify(str(key))}-{slugify(coll)}"

    def match_label(self) -> str:
        return _MATCH_LABELS.get(self.match, self.match)

    def mode_label(self) -> str:
        return _MODE_LABELS.get(self.mode, self.mode)

    def when_display(self) -> str:
        joined = ", ".join(self.values)
        if self.pin:
            who = self.description or joined
            return f"always this work ({who})"
        return f"{self.match_label()} “{joined}”"

    def then_display(self) -> str:
        names = ", ".join(self.collections)
        if self.mode == "exclude":
            return f"never {names}"
        return names

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "match": self.match,
            "values": list(self.values),
            "collections": list(self.collections),
            "mode": self.mode,
            "enabled": self.enabled,
            "pin": self.pin,
            "description": self.description,
            "match_label": self.match_label(),
            "mode_label": self.mode_label(),
            "when": self.when_display(),
            "then": self.then_display(),
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data.get("mode") == "include":
            data.pop("mode", None)
        if not data.get("pin"):
            data.pop("pin", None)
        if not data.get("description"):
            data.pop("description", None)
        if data.get("enabled") is True:
            data.pop("enabled", None)
        return data

    def matches(self, record: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        values = [str(item).strip() for item in self.values if str(item).strip()]
        if not values:
            return False
        if self.match == "work_id":
            work_id = work_id_of(record)
            return bool(work_id) and any(str(item) == work_id for item in values)
        if self.match == "calibre_uuid":
            uuid = calibre_uuid_of(record)
            return bool(uuid) and any(_norm(item) == _norm(uuid) for item in values)
        if self.match == "author_ci":
            return any(_exact(authors_of(record), item) for item in values)
        if self.match == "fandom_mentions":
            fandoms = fandoms_of(record)
            return any(_contains(fandoms, item) for item in values)
        names = names_for_collection_match(record)
        if self.match == "is_ci":
            return any(_exact(names, item) for item in values)
        return any(_contains(names, item) for item in values)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollectionRule:
        known = {item.name for item in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known}
        if "values" in payload:
            payload["values"] = parse_csv(payload["values"])
        elif "value" in data:
            payload["values"] = parse_csv(data.get("value"))
        if "collections" in payload:
            payload["collections"] = parse_csv(payload["collections"])
        elif "collection" in data:
            payload["collections"] = parse_csv(data.get("collection"))
        rule = cls(**payload)
        rule.validate()
        return rule


@dataclass
class ComputedCollections:
    included: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    included_sources: dict[str, list[str]] = field(default_factory=dict)
    excluded_sources: dict[str, list[str]] = field(default_factory=dict)
    matched_rule_ids: list[str] = field(default_factory=list)

    def names(self) -> list[str]:
        skip = {_norm(name) for name in self.excluded}
        return [name for name in self.included if _norm(name) not in skip]


def compute_collections(
    record: dict[str, Any],
    rules: Sequence[CollectionRule],
) -> ComputedCollections:
    included: dict[str, list[str]] = {}
    excluded: dict[str, list[str]] = {}
    matched: list[str] = []
    for rule in rules:
        if not rule.matches(record):
            continue
        matched.append(rule.id)
        target = excluded if rule.mode == "exclude" else included
        for name in rule.collections:
            text = str(name).strip()
            if not text:
                continue
            bucket = target.setdefault(text, [])
            if rule.id not in bucket:
                bucket.append(rule.id)
    return ComputedCollections(
        included=list(included),
        excluded=list(excluded),
        included_sources=included,
        excluded_sources=excluded,
        matched_rule_ids=matched,
    )


def overlay_record_collections(
    record: dict[str, Any],
    rules: Sequence[CollectionRule],
) -> dict[str, Any]:
    """Replace ``cleaned.collections`` with engine ∪ YAML includes − excludes."""
    computed = compute_collections(record, rules)
    cleaned = dict(record.get("cleaned") or {})
    engine = engine_collection_names(record)
    existing_map = cleaned.get("collections")
    sources: dict[str, list[str]] = {}
    if isinstance(existing_map, dict):
        for key, value in existing_map.items():
            sources[str(key)] = list(value or [])
    for name, srcs in computed.included_sources.items():
        bucket = sources.setdefault(name, [])
        for src in srcs:
            label = f"rule:{src}"
            if label not in bucket:
                bucket.append(label)
    names = unique_names([*engine, *computed.included])
    skip = {_norm(name) for name in computed.excluded}
    final = [name for name in names if _norm(name) not in skip]
    cleaned["collections"] = {name: sources.get(name, []) for name in final}
    out = dict(record)
    out["cleaned"] = cleaned
    return out


def overlay_records(
    records: Sequence[dict[str, Any]],
    rules: Sequence[CollectionRule],
) -> list[dict[str, Any]]:
    return [overlay_record_collections(record, rules) for record in records]


def engine_collection_sources(record: dict[str, Any]) -> dict[str, list[str]]:
    cleaned = record.get("cleaned")
    if not isinstance(cleaned, dict):
        return {}
    raw = cleaned.get("collections")
    if not isinstance(raw, dict):
        return {}
    sources: dict[str, list[str]] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(value, (list, tuple)):
            sources[name] = [str(item) for item in value if str(item).strip()]
        elif value:
            sources[name] = [str(value)]
        else:
            sources[name] = []
    return sources


def _map_get_ci(mapping: dict[str, list[str]], name: str) -> list[str]:
    key = _norm(name)
    for src, values in mapping.items():
        if _norm(src) == key:
            return list(values or [])
    return []


def explain_record_collections(
    record: dict[str, Any],
    rules: Sequence[CollectionRule],
) -> dict[str, Any]:
    """Describe how this work got each collection (rules, pins, leftover mappings)."""
    computed = compute_collections(record, rules)
    current = current_collections_of(record)
    current_names = current if current is not None else []
    engine = engine_collection_names(record)
    engine_src = engine_collection_sources(record)
    rules_by_id = {rule.id: rule for rule in rules}
    names = unique_names(
        [*current_names, *computed.included, *computed.excluded, *engine]
    )
    overlay_names = unique_names([*engine, *computed.included])
    overlay_keys = {
        _norm(name) for name in overlay_names if _norm(name) not in {_norm(item) for item in computed.excluded}
    }
    current_keys = {_norm(name) for name in current_names}
    excluded_keys = {_norm(name) for name in computed.excluded}
    memberships: list[dict[str, Any]] = []
    for name in names:
        key = _norm(name)
        include_ids = _map_get_ci(computed.included_sources, name)
        exclude_ids = _map_get_ci(computed.excluded_sources, name)
        includes = [
            rules_by_id[rule_id].to_api_dict()
            for rule_id in include_ids
            if rule_id in rules_by_id
        ]
        excludes = [
            rules_by_id[rule_id].to_api_dict()
            for rule_id in exclude_ids
            if rule_id in rules_by_id
        ]
        in_overlay = key in overlay_keys
        in_current = key in current_keys
        in_excluded = key in excluded_keys
        if in_excluded and not in_overlay:
            status = "excluded"
        elif in_overlay and in_current:
            status = "in"
        elif in_overlay:
            status = "pending"
        elif in_current:
            status = "unexplained"
        else:
            status = "out"
        memberships.append(
            {
                "name": name,
                "status": status,
                "computed": in_overlay,
                "current": in_current,
                "includes": includes,
                "excludes": excludes,
                "include_pins": [row for row in includes if row.get("pin")],
                "exclude_pins": [row for row in excludes if row.get("pin")],
                "shared_includes": [row for row in includes if not row.get("pin")],
                "shared_excludes": [row for row in excludes if not row.get("pin")],
                "engine_sources": _map_get_ci(engine_src, name),
            }
        )
    book_id = record.get("book_id")
    return {
        "title": str(record.get("title") or ""),
        "work_id": work_id_of(record),
        "calibre_uuid": calibre_uuid_of(record),
        "book_id": book_id,
        "current": list(current_names),
        "computed": computed.names(),
        "engine": engine,
        "memberships": memberships,
    }


def explain_records(
    records: Sequence[dict[str, Any]],
    rules: Sequence[CollectionRule],
) -> list[dict[str, Any]]:
    return [explain_record_collections(record, rules) for record in records]


def empty_membership(name: str) -> dict[str, Any]:
    """Placeholder row when a filtered collection is not on a book."""
    return {
        "name": name,
        "status": "out",
        "computed": False,
        "current": False,
        "includes": [],
        "excludes": [],
        "include_pins": [],
        "exclude_pins": [],
        "shared_includes": [],
        "shared_excludes": [],
        "engine_sources": [],
    }


def _pin_identity(rule: CollectionRule) -> tuple[str, str, str, str] | None:
    if not rule.pin or not rule.values or not rule.collections:
        return None
    return (
        rule.match,
        _norm(rule.values[0]),
        _norm(rule.collections[0]),
        rule.mode,
    )


def upsert_pin(
    rules: Sequence[CollectionRule],
    *,
    collection: str,
    work_id: str = "",
    calibre_uuid: str = "",
    mode: CollectionMode = "include",
    description: str = "",
) -> tuple[list[CollectionRule], CollectionRule | None]:
    """Ensure a per-work pin exists. Returns (rules, new_or_updated_or_None)."""
    collection = str(collection).strip()
    work_id = str(work_id or "").strip()
    calibre_uuid = str(calibre_uuid or "").strip()
    if not collection or (not work_id and not calibre_uuid):
        return list(rules), None
    match: CollectionMatch = "work_id" if work_id else "calibre_uuid"
    value = work_id or calibre_uuid
    identity = (match, _norm(value), _norm(collection), mode)
    updated = list(rules)
    for index, rule in enumerate(updated):
        if _pin_identity(rule) != identity:
            continue
        if rule.enabled and collection in rule.collections:
            return updated, None
        updated[index] = CollectionRule(
            id=rule.id,
            match=rule.match,
            values=list(rule.values),
            collections=unique_names([*rule.collections, collection]),
            mode=rule.mode,
            enabled=True,
            pin=True,
            description=description or rule.description,
        )
        return updated, updated[index]
    pin = CollectionRule(
        id="pin",
        match=match,
        values=[value],
        collections=[collection],
        mode=mode,
        enabled=True,
        pin=True,
        description=description,
    )
    pin.id = allocate_id(pin.suggested_id(), [item.id for item in updated])
    pin.validate()
    updated.append(pin)
    return updated, pin


def remove_pin(
    rules: Sequence[CollectionRule],
    *,
    collection: str,
    work_id: str = "",
    calibre_uuid: str = "",
    mode: CollectionMode | None = None,
) -> tuple[list[CollectionRule], list[CollectionRule]]:
    """Remove per-work pins for this collection. ``mode=None`` removes both."""
    collection = str(collection).strip()
    work_id = str(work_id or "").strip()
    calibre_uuid = str(calibre_uuid or "").strip()
    if not collection or (not work_id and not calibre_uuid):
        return list(rules), []
    match: CollectionMatch = "work_id" if work_id else "calibre_uuid"
    value_key = _norm(work_id or calibre_uuid)
    collection_key = _norm(collection)
    kept: list[CollectionRule] = []
    removed: list[CollectionRule] = []
    for rule in rules:
        identity = _pin_identity(rule)
        if identity is None:
            kept.append(rule)
            continue
        rule_match, rule_value, rule_collection, rule_mode = identity
        if (
            rule_match != match
            or rule_value != value_key
            or rule_collection != collection_key
        ):
            kept.append(rule)
            continue
        if mode is not None and rule_mode != mode:
            kept.append(rule)
            continue
        leftover = [
            name for name in rule.collections if _norm(name) != collection_key
        ]
        if leftover:
            kept.append(
                CollectionRule(
                    id=rule.id,
                    match=rule.match,
                    values=list(rule.values),
                    collections=leftover,
                    mode=rule.mode,
                    enabled=rule.enabled,
                    pin=True,
                    description=rule.description,
                )
            )
        else:
            removed.append(rule)
    return kept, removed


def capture_manual_adds(
    rules: Sequence[CollectionRule],
    record: dict[str, Any],
    current: Sequence[str],
) -> tuple[list[CollectionRule], list[CollectionRule]]:
    """Turn Calibre collections not explained by shared rules into pin includes."""
    shared = [rule for rule in rules if not rule.pin]
    computed = compute_collections(record, shared)
    explained = unique_names([*engine_collection_names(record), *computed.included])
    explained_keys = {_norm(name) for name in explained}
    skip_exclude = {_norm(name) for name in computed.excluded}
    updated = list(rules)
    added: list[CollectionRule] = []
    work_id = work_id_of(record)
    uuid = calibre_uuid_of(record)
    title = str(record.get("title") or "").strip()
    for name in unique_names(current):
        key = _norm(name)
        if key in explained_keys or key in skip_exclude:
            continue
        updated, pin = upsert_pin(
            updated,
            collection=name,
            work_id=work_id,
            calibre_uuid=uuid,
            description=title,
        )
        if pin is not None:
            added.append(pin)
    return updated, added


def recompute_records(
    records: Sequence[dict[str, Any]],
    rules: Sequence[CollectionRule],
    *,
    remember_adds: bool = True,
) -> tuple[list[dict[str, Any]], list[CollectionRule], list[CollectionRule]]:
    """Capture manual adds as pins (optional), then overlay computed collections.

    Returns ``(records, rules, new_pins)``.
    """
    updated_rules = list(rules)
    new_pins: list[CollectionRule] = []
    if remember_adds:
        for record in records:
            current = current_collections_of(record)
            if current is None:
                continue
            updated_rules, added = capture_manual_adds(
                updated_rules, record, current
            )
            new_pins.extend(added)
    overlayed = overlay_records(records, updated_rules)
    return overlayed, updated_rules, new_pins


def collection_rule_from_form(
    *,
    match: str,
    values: str | Sequence[str],
    collections: str | Sequence[str],
    mode: str = "include",
    enabled: bool = True,
    pin: bool = False,
    rule_id: str = "",
    description: str = "",
    existing_ids: Iterable[str] = (),
) -> CollectionRule:
    if match not in MATCH_KINDS:
        raise ValueError(f"Unknown collection match {match!r}")
    if mode not in MODES:
        raise ValueError(f"Unknown collection mode {mode!r}")
    parsed_values = parse_csv(values)
    parsed_collections = parse_csv(collections)
    if not parsed_collections and parsed_values and match in {"mentions", "is_ci", "fandom_mentions", "author_ci"}:
        parsed_collections = [parsed_values[0]]
    rule = CollectionRule(
        id=rule_id.strip() or "collection",
        match=match,  # type: ignore[arg-type]
        values=parsed_values,
        collections=parsed_collections,
        mode=mode,  # type: ignore[arg-type]
        enabled=bool(enabled),
        pin=bool(pin),
        description=description,
    )
    if not rule_id.strip():
        rule.id = allocate_id(rule.suggested_id(), existing_ids)
    rule.validate()
    return rule


def replace_collection_rule(
    rules: list[CollectionRule], rule: CollectionRule
) -> list[CollectionRule]:
    updated = list(rules)
    for index, existing in enumerate(updated):
        if existing.id == rule.id:
            updated[index] = rule
            return updated
    raise KeyError(f"No collection rule named {rule.id!r}")


def remove_collection_rule(
    rules: list[CollectionRule], rule_id: str
) -> list[CollectionRule]:
    kept = [item for item in rules if item.id != rule_id]
    if len(kept) == len(rules):
        raise KeyError(f"No collection rule named {rule_id!r}")
    return kept


def move_collection_rule(
    rules: list[CollectionRule], rule_id: str, *, direction: str
) -> list[CollectionRule]:
    updated = list(rules)
    index = next((i for i, item in enumerate(updated) if item.id == rule_id), None)
    if index is None:
        raise KeyError(f"No collection rule named {rule_id!r}")
    delta = -1 if direction == "up" else 1 if direction == "down" else 0
    if delta == 0:
        raise ValueError("direction must be 'up' or 'down'")
    target = index + delta
    if target < 0 or target >= len(updated):
        return updated
    updated[index], updated[target] = updated[target], updated[index]
    return updated


def toggle_collection_rule(
    rules: list[CollectionRule],
    rule_id: str,
    *,
    enabled: bool | None = None,
) -> list[CollectionRule]:
    updated = list(rules)
    for index, existing in enumerate(updated):
        if existing.id != rule_id:
            continue
        flag = (not existing.enabled) if enabled is None else bool(enabled)
        updated[index] = CollectionRule(
            id=existing.id,
            match=existing.match,
            values=list(existing.values),
            collections=list(existing.collections),
            mode=existing.mode,
            enabled=flag,
            pin=existing.pin,
            description=existing.description,
        )
        return updated
    raise KeyError(f"No collection rule named {rule_id!r}")


def rules_from_payload(data: Any) -> list[CollectionRule]:
    if data is None:
        return []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("rules") or data.get("collections") or []
    else:
        raise ValueError("Collections file must be a list or a mapping with 'rules'")
    if not isinstance(rows, list):
        raise ValueError("collection rules must be a list")
    return [CollectionRule.from_dict(item) for item in rows if isinstance(item, dict)]


def load_collection_rules(path: Path | str) -> list[CollectionRule]:
    path = Path(path)
    if not path.is_file():
        return []
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML required: pip install pyyaml") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return rules_from_payload(data)


def save_collection_rules(path: Path | str, rules: Sequence[CollectionRule]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML required: pip install pyyaml") from exc
    payload = {"rules": [rule.to_dict() | {"id": rule.id} for rule in rules]}
    text = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path
