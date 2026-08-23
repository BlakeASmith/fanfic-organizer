"""Declarative tag mappings layered on AO3 canonical resolution.

User-facing keep / rename / drop / collect rows live in
``mappings.yaml`` in the XDG config dir (CLI / ``python -m ao3kit config``). They compile to the same
``TagRule`` built-ins as Python/YAML rule modules and are prepended so they
run first (default priority 1000).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from ao3kit.tags.metadata import is_fandom_category
from ao3kit.tags.rules import (
    CollectRule,
    DropRule,
    KeepSeparateRule,
    MapToRule,
    TagRule,
    TagRulesConfig,
    TagRulesEngine,
)

MatchKind = Literal[
    "tag",
    "tag_ci",
    "canonical",
    "canonical_ci",
    "contains",
    "contains_ci",
    "mentions",
    "is_ci",
]
MappingAction = Literal["keep_separate", "map_to", "drop", "collect"]

MAPPINGS_FILENAME = "mappings.yaml"
UI_MAPPING_PRIORITY = 1000
_SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

MATCH_KINDS: tuple[MatchKind, ...] = (
    "tag",
    "tag_ci",
    "canonical",
    "canonical_ci",
    "contains",
    "contains_ci",
    "mentions",
    "is_ci",
)
ACTIONS: tuple[MappingAction, ...] = (
    "keep_separate",
    "map_to",
    "drop",
    "collect",
)

# Labels for the plugin form (subset of match kinds; case-insensitive).
MATCH_CHOICES: tuple[tuple[MatchKind, str], ...] = (
    ("mentions", "contains"),
    ("is_ci", "is exactly"),
)
ACTION_CHOICES: tuple[tuple[MappingAction, str], ...] = (
    ("collect", "Don't change it"),
    ("keep_separate", "Keep this spelling"),
    ("map_to", "Rename it"),
    ("drop", "Remove it"),
)

_MATCH_KWARG: dict[MatchKind, str] = {
    "tag": "tags",
    "tag_ci": "tags_ci",
    "canonical": "canonical",
    "canonical_ci": "canonical_ci",
    "contains": "contains",
    "contains_ci": "contains_ci",
}

_MATCH_LABELS = {
    **dict(MATCH_CHOICES),
    "tag": "is exactly",
    "tag_ci": "is exactly",
    "canonical": "is exactly",
    "canonical_ci": "is exactly",
    "contains": "contains",
    "contains_ci": "contains",
}
_ACTION_LABELS = dict(ACTION_CHOICES)


def parse_csv(value: str | Sequence[str] | None) -> list[str]:
    """Split a comma-separated string (or pass through a list) into stripped names."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value)
    return [str(part).strip() for part in parts if str(part).strip()]


def infer_collections(
    *,
    action: str,
    values: Sequence[str],
    map_to: str = "",
    collections: str | Sequence[str] | None = "",
) -> list[str]:
    """Resolve the collection list for a mapping row.

    An explicit Collection field always wins. Keep / rename / drop rules do
    **not** invent a collection — those can be tag-only. A collect
    (“don’t change the tag”) row with a blank Collection uses the match text
    (or the rename target) so a collection-only rule still takes one field.
    """
    explicit = parse_csv(collections)
    if explicit:
        return explicit
    if action != "collect":
        return []
    if values:
        return [str(values[0]).strip()]
    return []


def ui_match_kind(match: str) -> MatchKind:
    """Map stored match kinds onto the two plugin dropdown values."""
    if match in {"mentions", "contains", "contains_ci"}:
        return "mentions"
    return "is_ci"


def slugify(text: str, *, fallback: str = "mapping") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = slug[:40] or fallback
    if slug[0].isdigit():
        slug = f"{fallback}-{slug}"
    return slug


def allocate_id(base: str, existing: Iterable[str]) -> str:
    taken = set(existing)
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"


@dataclass
class TagMapping:
    """One extra mapping rule applied after AO3 canonical resolve."""

    id: str
    match: MatchKind = "tag_ci"
    values: list[str] = field(default_factory=list)
    action: MappingAction = "map_to"
    map_to: str = ""
    collections: list[str] = field(default_factory=list)
    stop: bool = False
    enabled: bool = True
    priority: int = UI_MAPPING_PRIORITY
    description: str = ""

    def validate(self) -> None:
        if not self.id or not _SAFE_ID.match(self.id):
            raise ValueError(
                f"Invalid mapping id {self.id!r}: use letters, numbers, _ or -"
            )
        if self.match not in MATCH_KINDS:
            raise ValueError(f"Unknown match kind {self.match!r}")
        if self.action not in ACTIONS:
            raise ValueError(f"Unknown mapping action {self.action!r}")
        if not self.values:
            raise ValueError("Mapping needs at least one match value")
        if self.action == "map_to" and not self.map_to.strip():
            raise ValueError("Rename mappings need a target tag")
        if self.action == "collect" and not self.collections:
            raise ValueError("Collection mappings need a collection name")

    def suggested_id(self) -> str:
        return f"{self.action}-{slugify(self.values[0] if self.values else self.id)}"

    def match_label(self) -> str:
        return _MATCH_LABELS.get(self.match, self.match)

    def action_label(self) -> str:
        return _ACTION_LABELS.get(self.action, self.action)

    def values_display(self) -> str:
        return ", ".join(self.values)

    def then_display(self) -> str:
        if self.action == "map_to":
            return f"Rename to {self.map_to}"
        if self.action == "collect":
            return "Don't change it"
        return self.action_label()

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "match": self.match,
            "values": list(self.values),
            "action": self.action,
            "map_to": self.map_to,
            "collections": list(self.collections),
            "stop": self.stop,
            "enabled": self.enabled,
            "priority": self.priority,
            "description": self.description,
            "match_label": self.match_label(),
            "action_label": self.action_label(),
            "then": self.then_display(),
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data.get("map_to"):
            data.pop("map_to", None)
        if not data.get("description"):
            data.pop("description", None)
        if data.get("priority") == UI_MAPPING_PRIORITY:
            data.pop("priority", None)
        if not data.get("collections"):
            data.pop("collections", None)
        if not data.get("stop"):
            data.pop("stop", None)
        if data.get("enabled") is True:
            data.pop("enabled", None)
        return data

    def to_rule(self) -> TagRule:
        self.validate()
        kwargs: dict[str, Any] = {
            "id": self.id,
            "priority": self.priority,
            "enabled": self.enabled,
            "description": self.description,
            "collections": list(self.collections),
            "stop": self.stop,
            **self._match_kwargs(),
        }
        if self.action == "keep_separate":
            return KeepSeparateRule(**kwargs)
        if self.action == "map_to":
            return MapToRule(map_to=self.map_to.strip(), **kwargs)
        if self.action == "drop":
            return DropRule(**kwargs)
        return CollectRule(**kwargs)

    def _match_kwargs(self) -> dict[str, Any]:
        values = list(self.values)
        if self.match == "mentions":
            return {
                "contains_ci": values,
                "canonical_ci": values,
                "tags_ci": values,
                "match_any": True,
            }
        if self.match == "is_ci":
            return {
                "tags_ci": values,
                "canonical_ci": values,
                "match_any": True,
            }
        return {_MATCH_KWARG[self.match]: values}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TagMapping:
        known = {item.name for item in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known}
        if "values" in payload:
            payload["values"] = parse_csv(payload["values"])
        elif "value" in data:
            payload["values"] = parse_csv(data.get("value"))
        if "collections" in payload:
            payload["collections"] = parse_csv(payload["collections"])
        if "priority" in payload:
            payload["priority"] = int(payload["priority"])
        mapping = cls(**payload)
        mapping.validate()
        return mapping


def mappings_from_payload(data: Any) -> list[TagMapping]:
    if data is None:
        return []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("mappings") or []
    else:
        raise ValueError("Mappings file must be a list or a mapping with 'mappings'")
    if not isinstance(rows, list):
        raise ValueError("mappings must be a list")
    return [TagMapping.from_dict(item) for item in rows if isinstance(item, dict)]


def load_mappings(path: Path | str) -> list[TagMapping]:
    path = Path(path)
    if not path.is_file():
        return []
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML required: pip install pyyaml") from exc
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return mappings_from_payload(raw)


def dump_mappings(mappings: Sequence[TagMapping]) -> str:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML required: pip install pyyaml") from exc
    for mapping in mappings:
        mapping.validate()
    payload = {
        "version": 1,
        "mappings": [mapping.to_dict() for mapping in mappings],
    }
    return yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def save_mappings(path: Path | str, mappings: Sequence[TagMapping]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dump_mappings(mappings)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def merge_mapping_rules(
    config: TagRulesConfig,
    mappings: Sequence[TagMapping],
) -> TagRulesConfig:
    """Prepend enabled mappings so they run before Python/YAML rules."""
    extra = [mapping.to_rule() for mapping in mappings if mapping.enabled]
    if not extra:
        return config
    return TagRulesConfig(
        version=config.version,
        resolve_canonical=config.resolve_canonical,
        drop_unmarked=config.drop_unmarked,
        drop_errors=config.drop_errors,
        include_metatags=config.include_metatags,
        rules=list(extra) + list(config.rules),
    )


def mapping_from_form(
    *,
    match: str,
    values: str | Sequence[str],
    action: str,
    map_to: str = "",
    collections: str | Sequence[str] = "",
    stop: bool = False,
    enabled: bool = True,
    mapping_id: str = "",
    existing_ids: Iterable[str] = (),
    priority: int = UI_MAPPING_PRIORITY,
    description: str = "",
) -> TagMapping:
    if match not in MATCH_KINDS:
        raise ValueError(f"Unknown match kind {match!r}")
    if action not in ACTIONS:
        raise ValueError(f"Unknown mapping action {action!r}")
    parsed_values = parse_csv(values)
    mapping = TagMapping(
        id=mapping_id.strip() or "mapping",
        match=match,
        values=parsed_values,
        action=action,
        map_to=(map_to or "").strip(),
        collections=infer_collections(
            action=action,
            values=parsed_values,
            map_to=map_to,
            collections=collections,
        ),
        stop=bool(stop),
        enabled=bool(enabled),
        priority=int(priority),
        description=description,
    )
    if not mapping_id.strip():
        mapping.id = allocate_id(mapping.suggested_id(), existing_ids)
    mapping.validate()
    return mapping


def replace_mapping(
    mappings: list[TagMapping], mapping: TagMapping
) -> list[TagMapping]:
    updated = list(mappings)
    for index, existing in enumerate(updated):
        if existing.id == mapping.id:
            updated[index] = mapping
            return updated
    raise KeyError(f"No mapping named {mapping.id!r}")


def remove_mapping(mappings: list[TagMapping], mapping_id: str) -> list[TagMapping]:
    kept = [item for item in mappings if item.id != mapping_id]
    if len(kept) == len(mappings):
        raise KeyError(f"No mapping named {mapping_id!r}")
    return kept


def move_mapping(
    mappings: list[TagMapping], mapping_id: str, *, direction: str
) -> list[TagMapping]:
    updated = list(mappings)
    index = next(
        (i for i, item in enumerate(updated) if item.id == mapping_id), None
    )
    if index is None:
        raise KeyError(f"No mapping named {mapping_id!r}")
    delta = -1 if direction == "up" else 1 if direction == "down" else 0
    if delta == 0:
        raise ValueError("direction must be 'up' or 'down'")
    target = index + delta
    if target < 0 or target >= len(updated):
        return updated
    updated[index], updated[target] = updated[target], updated[index]
    return updated


def toggle_mapping(
    mappings: list[TagMapping], mapping_id: str, *, enabled: bool | None = None
) -> list[TagMapping]:
    updated = list(mappings)
    for index, existing in enumerate(updated):
        if existing.id != mapping_id:
            continue
        flag = (not existing.enabled) if enabled is None else bool(enabled)
        updated[index] = TagMapping(
            id=existing.id,
            match=existing.match,
            values=list(existing.values),
            action=existing.action,
            map_to=existing.map_to,
            collections=list(existing.collections),
            stop=existing.stop,
            enabled=flag,
            priority=existing.priority,
            description=existing.description,
        )
        return updated
    raise KeyError(f"No mapping named {mapping_id!r}")


def preview_tag(name: str, engine: TagRulesEngine) -> dict[str, Any]:
    """AO3 canonical + rule outcome for one tag (for Settings / API / CLI)."""
    resolved = engine.resolver.resolve_one(name)
    item = engine.apply_one(name)
    metatags = (
        engine.resolver.metatags_for(name)
        if is_fandom_category(resolved.category)
        else []
    )
    return {
        "original": resolved.original,
        "canonical": resolved.resolved,
        "status": resolved.status,
        "ao3_changed": resolved.changed,
        "mapped": item.mapped,
        "dropped": item.dropped,
        "mapping_action": item.mapping_action,
        "mapping_rule": item.mapping_rule,
        "collections": list(item.collections),
        "applied_rules": list(item.applied_rules),
        "metatags": list(metatags),
    }
