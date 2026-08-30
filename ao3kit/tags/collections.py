"""Collection membership computed from rules (plus optional per-work pins).

Collections on a work are a **view of rules**, not a separate stored truth.
Recompute anytime. A collection the user adds by hand on one work becomes a
pin rule for that work, so the next recompute puts it back.

Tag-transform mappings (keep / rename / drop) stay in ``mappings.yaml``.
Collection rules live in ``collections.yaml`` under the XDG config dir.
Mapping rows that still
list collections keep working through the tag engine; this module overlays
YAML rules (including pins and excludes) on top.

Rules match with an AND list of conditions (``all:``). Legacy single
``match`` + ``values`` rows desugar to one condition on load.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field as dc_field, fields
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from ao3kit.tags.mappings import allocate_id, parse_csv, slugify

logger = logging.getLogger(__name__)

COLLECTIONS_FILENAME = "collections.yaml"
_SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_WORK_ID_RE = re.compile(r"/works/(\d+)")
_INVALID_REGEX: set[str] = set()

# Legacy single-field match kinds (still accepted on load / simple forms).
CollectionMatch = Literal[
    "mentions",
    "is_ci",
    "fandom_mentions",
    "author_ci",
    "work_id",
    "calibre_uuid",
]
CollectionMode = Literal["include", "exclude"]

CollectionField = Literal[
    "tag",
    "fandom",
    "relationship",
    "character",
    "author",
    "title",
    "summary",
    "series",
    "words",
    "complete",
    "work_id",
    "calibre_uuid",
]
CollectionOp = Literal[
    "contains",
    "is",
    "wildcard",
    "regex",
    "eq",
    "gt",
    "gte",
    "lt",
    "lte",
]

MATCH_KINDS: tuple[CollectionMatch, ...] = (
    "mentions",
    "is_ci",
    "fandom_mentions",
    "author_ci",
    "work_id",
    "calibre_uuid",
)
MODES: tuple[CollectionMode, ...] = ("include", "exclude")
FIELD_KINDS: tuple[CollectionField, ...] = (
    "tag",
    "fandom",
    "relationship",
    "character",
    "author",
    "title",
    "summary",
    "series",
    "words",
    "complete",
    "work_id",
    "calibre_uuid",
)
TEXT_OPS: tuple[CollectionOp, ...] = ("contains", "is", "wildcard", "regex")
NUMERIC_OPS: tuple[CollectionOp, ...] = ("eq", "gt", "gte", "lt", "lte")
BOOL_OPS: tuple[CollectionOp, ...] = ("is",)
OP_KINDS: tuple[CollectionOp, ...] = (
    "contains",
    "is",
    "wildcard",
    "regex",
    "eq",
    "gt",
    "gte",
    "lt",
    "lte",
)

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
FIELD_CHOICES: tuple[tuple[CollectionField, str], ...] = (
    ("tag", "tag"),
    ("fandom", "fandom"),
    ("relationship", "relationship"),
    ("character", "character"),
    ("author", "author"),
    ("title", "title"),
    ("summary", "summary"),
    ("series", "series"),
    ("words", "word count"),
    ("complete", "complete"),
    ("work_id", "AO3 work id"),
    ("calibre_uuid", "Calibre book UUID"),
)
OP_CHOICES: tuple[tuple[CollectionOp, str], ...] = (
    ("contains", "contains"),
    ("is", "is exactly"),
    ("wildcard", "matches wildcard"),
    ("regex", "matches regex"),
    ("eq", "="),
    ("gt", ">"),
    ("gte", "≥"),
    ("lt", "<"),
    ("lte", "≤"),
)

_MATCH_LABELS = dict(MATCH_CHOICES)
_MODE_LABELS = dict(MODE_CHOICES)
_FIELD_LABELS = dict(FIELD_CHOICES)
_OP_LABELS = dict(OP_CHOICES)

_LEGACY_TO_CONDITION: dict[CollectionMatch, tuple[CollectionField, CollectionOp]] = {
    "mentions": ("tag", "contains"),
    "is_ci": ("tag", "is"),
    "fandom_mentions": ("fandom", "contains"),
    "author_ci": ("author", "is"),
    "work_id": ("work_id", "is"),
    "calibre_uuid": ("calibre_uuid", "is"),
}

_TEXT_FIELDS = frozenset(
    {
        "tag",
        "fandom",
        "relationship",
        "character",
        "author",
        "title",
        "summary",
        "series",
        "work_id",
        "calibre_uuid",
    }
)
_AUTO_COLLECTION_FIELDS = frozenset(
    {"tag", "fandom", "relationship", "character", "author"}
)


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


def characters_of(record: dict[str, Any]) -> list[str]:
    names = _as_name_list(record.get("characters"))
    cleaned = record.get("cleaned")
    if isinstance(cleaned, dict):
        names = unique_names([*names, *_as_name_list(cleaned.get("characters"))])
    return names


def series_names_of(record: dict[str, Any]) -> list[str]:
    names: list[str] = []
    series = record.get("series")
    if isinstance(series, list):
        for item in series:
            if isinstance(item, dict):
                names.append(str(item.get("name") or "").strip())
            else:
                names.append(str(item).strip())
    elif isinstance(series, str):
        names.append(series.strip())
    cleaned = record.get("cleaned")
    if isinstance(cleaned, dict):
        names.extend(_as_name_list(cleaned.get("series")))
    return unique_names(names)


def title_of(record: dict[str, Any]) -> str:
    return str(record.get("title") or "").strip()


def summary_of(record: dict[str, Any]) -> str:
    return str(record.get("summary") or "").strip()


def words_of(record: dict[str, Any]) -> int | None:
    meta = record.get("metadata")
    if isinstance(meta, dict) and meta.get("words") is not None:
        try:
            return int(str(meta.get("words")).replace(",", ""))
        except (TypeError, ValueError):
            pass
    for key in ("wordcount", "word_count", "words"):
        if record.get(key) is None:
            continue
        try:
            return int(str(record.get(key)).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return None


def complete_of(record: dict[str, Any]) -> bool | None:
    meta = record.get("metadata")
    if isinstance(meta, dict):
        chapters = meta.get("chapters")
        if isinstance(chapters, dict) and "is_complete" in chapters:
            return bool(chapters.get("is_complete"))
        if "is_complete" in meta:
            return bool(meta.get("is_complete"))
    if "is_complete" in record:
        return bool(record.get("is_complete"))
    tags = {_norm(tag) for tag in _as_name_list(record.get("tags"))}
    if "completed" in tags or "complete" in tags:
        return True
    return None


def engine_collection_names(record: dict[str, Any]) -> list[str]:
    cleaned = record.get("cleaned")
    if not isinstance(cleaned, dict):
        return []
    return _as_name_list(cleaned.get("collections"))


def names_for_collection_match(record: dict[str, Any]) -> list[str]:
    """Tag-like names a 'tag contains / is exactly' rule can see."""
    names = [
        *_as_name_list(record.get("tags")),
        *fandoms_of(record),
        *relationships_of(record),
        *characters_of(record),
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


def _prep_text(value: str, *, casefold: bool) -> str:
    text = str(value or "").strip()
    return text.casefold() if casefold else text


def _contains(names: Sequence[str], needle: str, *, casefold: bool = True) -> bool:
    n = _prep_text(needle, casefold=casefold)
    if not n:
        return False
    for name in names:
        hay = _prep_text(name, casefold=casefold)
        if n == hay or n in hay:
            return True
    return False


def _exact(names: Sequence[str], needle: str, *, casefold: bool = True) -> bool:
    n = _prep_text(needle, casefold=casefold)
    return bool(n) and any(_prep_text(name, casefold=casefold) == n for name in names)


def _wildcard_to_regex(pattern: str) -> str:
    parts: list[str] = []
    for char in pattern:
        if char == "*":
            parts.append(".*")
        elif char == "?":
            parts.append(".")
        else:
            parts.append(re.escape(char))
    return "".join(parts)


def _compile_pattern(pattern: str, *, casefold: bool, wildcard: bool) -> re.Pattern[str] | None:
    flags = re.IGNORECASE if casefold else 0
    source = _wildcard_to_regex(pattern) if wildcard else pattern
    try:
        return re.compile(source, flags)
    except re.error as exc:
        key = f"{'w' if wildcard else 'r'}:{casefold}:{pattern}"
        if key not in _INVALID_REGEX:
            _INVALID_REGEX.add(key)
            logger.warning(
                "Invalid collection %s pattern %r: %s",
                "wildcard" if wildcard else "regex",
                pattern,
                exc,
            )
        return None


def _regex_match(names: Sequence[str], pattern: str, *, casefold: bool, wildcard: bool) -> bool:
    compiled = _compile_pattern(pattern, casefold=casefold, wildcard=wildcard)
    if compiled is None:
        return False
    return any(bool(compiled.search(str(name or ""))) for name in names)


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in {"1", "true", "yes", "complete", "completed", "t"}:
        return True
    if text in {"0", "false", "no", "incomplete", "wip", "f"}:
        return False
    return None


def _parse_number(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


@dataclass
class CollectionCondition:
    """One predicate in a collection rule (AND'd with siblings)."""

    field: CollectionField = "tag"
    op: CollectionOp = "contains"
    values: list[str] = dc_field(default_factory=list)
    value: str | int | bool | None = None
    casefold: bool = True

    def validate(self) -> None:
        if self.field not in FIELD_KINDS:
            raise ValueError(f"Unknown collection field {self.field!r}")
        if self.op not in OP_KINDS:
            raise ValueError(f"Unknown collection op {self.op!r}")
        if self.field == "words":
            if self.op not in NUMERIC_OPS:
                raise ValueError(f"Word-count conditions need a numeric op, not {self.op!r}")
            if self._numeric_value() is None:
                raise ValueError("Word-count condition needs a number")
            return
        if self.field == "complete":
            if self.op not in BOOL_OPS:
                raise ValueError("Complete conditions use op 'is'")
            if self._bool_value() is None:
                raise ValueError("Complete condition needs true or false")
            return
        if self.op not in TEXT_OPS:
            raise ValueError(f"Text field {self.field!r} cannot use op {self.op!r}")
        if not self.text_values():
            raise ValueError("Collection condition needs something to match")

    def text_values(self) -> list[str]:
        values = [str(item).strip() for item in self.values if str(item).strip()]
        if values:
            return values
        if self.value is None or isinstance(self.value, bool):
            return []
        text = str(self.value).strip()
        return [text] if text else []

    def _numeric_value(self) -> int | None:
        if self.value is not None and not isinstance(self.value, bool):
            parsed = _parse_number(self.value)
            if parsed is not None:
                return parsed
        for item in self.values:
            parsed = _parse_number(item)
            if parsed is not None:
                return parsed
        return None

    def _bool_value(self) -> bool | None:
        if self.value is not None:
            parsed = _parse_bool(self.value)
            if parsed is not None:
                return parsed
        for item in self.values:
            parsed = _parse_bool(item)
            if parsed is not None:
                return parsed
        return None

    def display(self) -> str:
        field_label = _FIELD_LABELS.get(self.field, self.field)
        op_label = _OP_LABELS.get(self.op, self.op)
        if self.field == "words":
            number = self._numeric_value()
            return f"{field_label} {op_label} {number}"
        if self.field == "complete":
            flag = self._bool_value()
            return "complete" if flag else "incomplete"
        joined = ", ".join(self.text_values())
        suffix = "" if self.casefold else " (case-sensitive)"
        return f"{field_label} {op_label} “{joined}”{suffix}"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"field": self.field, "op": self.op}
        if self.field == "words":
            data["value"] = self._numeric_value()
        elif self.field == "complete":
            data["value"] = self._bool_value()
        else:
            values = self.text_values()
            if len(values) == 1:
                data["value"] = values[0]
            else:
                data["values"] = values
            if not self.casefold:
                data["casefold"] = False
        return data

    def to_api_dict(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload["field_label"] = _FIELD_LABELS.get(self.field, self.field)
        payload["op_label"] = _OP_LABELS.get(self.op, self.op)
        payload["display"] = self.display()
        payload["casefold"] = self.casefold
        if self.field not in {"words", "complete"}:
            payload["values"] = self.text_values()
        return payload

    def matches(self, record: dict[str, Any]) -> bool:
        if self.field == "words":
            words = words_of(record)
            target = self._numeric_value()
            if words is None or target is None:
                return False
            if self.op == "eq":
                return words == target
            if self.op == "gt":
                return words > target
            if self.op == "gte":
                return words >= target
            if self.op == "lt":
                return words < target
            if self.op == "lte":
                return words <= target
            return False
        if self.field == "complete":
            actual = complete_of(record)
            wanted = self._bool_value()
            if actual is None or wanted is None:
                return False
            return actual is wanted

        needles = self.text_values()
        if not needles:
            return False
        haystacks = self._text_haystacks(record)
        casefold = self.casefold
        if self.field == "work_id":
            # Work ids are numeric strings; compare exactly.
            casefold = False
            haystacks = [work_id_of(record)] if work_id_of(record) else []
        if self.op == "contains":
            return any(_contains(haystacks, needle, casefold=casefold) for needle in needles)
        if self.op == "is":
            if self.field == "work_id":
                return any(str(item) == work_id_of(record) for item in needles)
            return any(_exact(haystacks, needle, casefold=casefold) for needle in needles)
        if self.op == "wildcard":
            return any(
                _regex_match(haystacks, needle, casefold=casefold, wildcard=True)
                for needle in needles
            )
        if self.op == "regex":
            return any(
                _regex_match(haystacks, needle, casefold=casefold, wildcard=False)
                for needle in needles
            )
        return False

    def _text_haystacks(self, record: dict[str, Any]) -> list[str]:
        if self.field == "tag":
            return names_for_collection_match(record)
        if self.field == "fandom":
            return fandoms_of(record)
        if self.field == "relationship":
            return relationships_of(record)
        if self.field == "character":
            return characters_of(record)
        if self.field == "author":
            return authors_of(record)
        if self.field == "series":
            return series_names_of(record)
        if self.field == "title":
            title = title_of(record)
            return [title] if title else []
        if self.field == "summary":
            summary = summary_of(record)
            return [summary] if summary else []
        if self.field == "work_id":
            work_id = work_id_of(record)
            return [work_id] if work_id else []
        if self.field == "calibre_uuid":
            uuid = calibre_uuid_of(record)
            return [uuid] if uuid else []
        return []

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollectionCondition:
        field_name = str(data.get("field") or "tag").strip()
        op = str(data.get("op") or "contains").strip()
        casefold = data.get("casefold", True)
        if isinstance(casefold, str):
            casefold = casefold.strip().casefold() not in {"0", "false", "no"}
        values = parse_csv(data.get("values")) if "values" in data else []
        if not values and "value" in data and data.get("value") is not None:
            raw = data.get("value")
            if isinstance(raw, bool):
                values = ["true" if raw else "false"]
            elif isinstance(raw, (int, float)):
                values = [str(int(raw))]
            else:
                values = parse_csv(raw)
        cond = cls(
            field=field_name,  # type: ignore[arg-type]
            op=op,  # type: ignore[arg-type]
            values=values,
            value=data.get("value"),
            casefold=bool(casefold),
        )
        # Normalize numeric/bool into value for stable saves.
        if cond.field == "words":
            cond.value = cond._numeric_value()
            cond.values = []
        elif cond.field == "complete":
            flag = cond._bool_value()
            cond.value = flag
            cond.values = []
        else:
            cond.value = None
            text_values = [str(item).strip() for item in values if str(item).strip()]
            if not text_values and data.get("value") is not None:
                text = str(data.get("value")).strip()
                if text:
                    text_values = [text]
            cond.values = text_values
        cond.validate()
        return cond

    @classmethod
    def from_legacy(cls, match: CollectionMatch, values: Sequence[str]) -> CollectionCondition:
        field_name, op = _LEGACY_TO_CONDITION[match]
        return cls(field=field_name, op=op, values=[str(item).strip() for item in values if str(item).strip()])

    @classmethod
    def from_when_spec(cls, spec: str, *, casefold: bool = True) -> CollectionCondition:
        """Parse ``field:op:value`` (value may contain colons)."""
        text = str(spec or "").strip()
        parts = text.split(":", 2)
        if len(parts) < 3 or not parts[0].strip() or not parts[1].strip():
            raise ValueError(
                "When spec must look like field:op:value "
                "(example: fandom:contains:Harry Potter)"
            )
        field_name, op, raw_value = parts[0].strip(), parts[1].strip(), parts[2]
        return cls.from_dict(
            {
                "field": field_name,
                "op": op,
                "value": raw_value,
                "casefold": casefold,
            }
        )


def conditions_from_legacy(match: CollectionMatch, values: Sequence[str]) -> list[CollectionCondition]:
    return [CollectionCondition.from_legacy(match, values)]


def legacy_from_conditions(
    conditions: Sequence[CollectionCondition],
) -> tuple[CollectionMatch, list[str]] | None:
    """Map a single simple condition back to legacy match/values when possible."""
    if len(conditions) != 1:
        return None
    cond = conditions[0]
    if not cond.casefold and cond.field in _TEXT_FIELDS:
        return None
    for match, (field_name, op) in _LEGACY_TO_CONDITION.items():
        if cond.field == field_name and cond.op == op:
            return match, cond.text_values()
    return None


@dataclass
class CollectionRule:
    """One include/exclude rule that maps matching works onto a collection."""

    id: str
    match: CollectionMatch = "mentions"
    values: list[str] = dc_field(default_factory=list)
    collections: list[str] = dc_field(default_factory=list)
    mode: CollectionMode = "include"
    enabled: bool = True
    pin: bool = False
    description: str = ""
    all: list[CollectionCondition] = dc_field(default_factory=list)

    def __post_init__(self) -> None:
        if self.all:
            legacy = legacy_from_conditions(self.all)
            if legacy is not None:
                self.match, self.values = legacy
            elif not self.values:
                # Keep match/values meaningful for pin helpers when possible.
                first = self.all[0]
                if first.field == "work_id" and first.op == "is":
                    self.match = "work_id"
                    self.values = first.text_values()
                elif first.field == "calibre_uuid" and first.op == "is":
                    self.match = "calibre_uuid"
                    self.values = first.text_values()
        elif self.values:
            if self.match not in MATCH_KINDS:
                self.match = "mentions"
            self.all = conditions_from_legacy(self.match, self.values)

    def conditions(self) -> list[CollectionCondition]:
        if self.all:
            return list(self.all)
        if self.values:
            return conditions_from_legacy(self.match, self.values)
        return []

    def validate(self) -> None:
        if not self.id or not _SAFE_ID.match(self.id):
            raise ValueError(
                f"Invalid collection rule id {self.id!r}: use letters, numbers, _ or -"
            )
        if self.mode not in MODES:
            raise ValueError(f"Unknown collection mode {self.mode!r}")
        if not self.collections:
            raise ValueError("Collection rule needs a collection name")
        conds = self.conditions()
        if not conds:
            raise ValueError("Collection rule needs something to match")
        if self.match not in MATCH_KINDS:
            raise ValueError(f"Unknown collection match {self.match!r}")
        for cond in conds:
            cond.validate()

    def suggested_id(self) -> str:
        prefix = "pin" if self.pin else ("never" if self.mode == "exclude" else "in")
        conds = self.conditions()
        if conds:
            first = conds[0]
            if first.field == "words":
                key = f"words-{first.op}-{first._numeric_value()}"
            elif first.field == "complete":
                key = "complete" if first._bool_value() else "incomplete"
            else:
                key = first.text_values()[0] if first.text_values() else first.field
            field_part = first.field
        else:
            key = self.values[0] if self.values else self.id
            field_part = self.match
        coll = self.collections[0] if self.collections else "collection"
        return f"{prefix}-{field_part}-{slugify(str(key))}-{slugify(coll)}"

    def match_label(self) -> str:
        conds = self.conditions()
        if len(conds) == 1:
            return conds[0].display().split(" “", 1)[0]
        if self.match in _MATCH_LABELS:
            return _MATCH_LABELS[self.match]
        return "match"

    def mode_label(self) -> str:
        return _MODE_LABELS.get(self.mode, self.mode)

    def when_display(self) -> str:
        if self.pin:
            who = self.description or ", ".join(self.values) or "this work"
            return f"always this work ({who})"
        conds = self.conditions()
        if not conds:
            return ""
        return " AND ".join(cond.display() for cond in conds)

    def then_display(self) -> str:
        names = ", ".join(self.collections)
        if self.mode == "exclude":
            return f"never {names}"
        return names

    def to_api_dict(self) -> dict[str, Any]:
        conds = self.conditions()
        payload = {
            "id": self.id,
            "match": self.match,
            "values": list(self.values),
            "collections": list(self.collections),
            "mode": self.mode,
            "enabled": self.enabled,
            "pin": self.pin,
            "description": self.description,
            "all": [cond.to_api_dict() for cond in conds],
            "match_label": self.match_label(),
            "mode_label": self.mode_label(),
            "when": self.when_display(),
            "then": self.then_display(),
        }
        return payload

    def to_dict(self) -> dict[str, Any]:
        conds = self.conditions()
        data: dict[str, Any] = {"id": self.id, "collections": list(self.collections)}
        legacy = legacy_from_conditions(conds)
        # Prefer all: for compound / non-legacy; keep match/values for simple legacy.
        if legacy is not None and len(conds) == 1:
            match, values = legacy
            data["match"] = match
            data["values"] = values
        else:
            data["all"] = [cond.to_dict() for cond in conds]
            # Preserve pin identity helpers.
            if self.pin and self.values:
                data["match"] = self.match
                data["values"] = list(self.values)
        if self.mode != "include":
            data["mode"] = self.mode
        if self.pin:
            data["pin"] = True
        if self.description:
            data["description"] = self.description
        if not self.enabled:
            data["enabled"] = False
        return data

    def matches(self, record: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        conds = self.conditions()
        if not conds:
            return False
        return all(cond.matches(record) for cond in conds)

    def copy_with(self, **changes: Any) -> CollectionRule:
        payload = {
            "id": self.id,
            "match": self.match,
            "values": list(self.values),
            "collections": list(self.collections),
            "mode": self.mode,
            "enabled": self.enabled,
            "pin": self.pin,
            "description": self.description,
            "all": [
                CollectionCondition(
                    field=cond.field,
                    op=cond.op,
                    values=list(cond.values),
                    value=cond.value,
                    casefold=cond.casefold,
                )
                for cond in self.conditions()
            ],
        }
        payload.update(changes)
        return CollectionRule(**payload)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollectionRule:
        known = {item.name for item in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known and key != "all"}
        if "values" in payload:
            payload["values"] = parse_csv(payload["values"])
        elif "value" in data:
            payload["values"] = parse_csv(data.get("value"))
        if "collections" in payload:
            payload["collections"] = parse_csv(payload["collections"])
        elif "collection" in data:
            payload["collections"] = parse_csv(data.get("collection"))
        conditions: list[CollectionCondition] = []
        raw_all = data.get("all")
        if isinstance(raw_all, list) and raw_all:
            conditions = [
                CollectionCondition.from_dict(item)
                for item in raw_all
                if isinstance(item, dict)
            ]
        rule = cls(**payload)
        if conditions:
            rule.all = conditions
            rule.__post_init__()
        elif rule.values:
            rule.all = conditions_from_legacy(rule.match, rule.values)
        rule.validate()
        return rule


@dataclass
class ComputedCollections:
    included: list[str] = dc_field(default_factory=list)
    excluded: list[str] = dc_field(default_factory=list)
    included_sources: dict[str, list[str]] = dc_field(default_factory=dict)
    excluded_sources: dict[str, list[str]] = dc_field(default_factory=dict)
    matched_rule_ids: list[str] = dc_field(default_factory=list)

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
        _norm(name)
        for name in overlay_names
        if _norm(name) not in {_norm(item) for item in computed.excluded}
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
        updated[index] = rule.copy_with(
            collections=unique_names([*rule.collections, collection]),
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
        all=conditions_from_legacy(match, [value]),
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
            kept.append(rule.copy_with(collections=leftover, pin=True))
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
    match: str = "mentions",
    values: str | Sequence[str] = (),
    collections: str | Sequence[str],
    mode: str = "include",
    enabled: bool = True,
    pin: bool = False,
    rule_id: str = "",
    description: str = "",
    existing_ids: Iterable[str] = (),
    conditions: Sequence[CollectionCondition | dict[str, Any]] | None = None,
) -> CollectionRule:
    if mode not in MODES:
        raise ValueError(f"Unknown collection mode {mode!r}")
    parsed_conditions: list[CollectionCondition] = []
    if conditions:
        for item in conditions:
            if isinstance(item, CollectionCondition):
                parsed_conditions.append(item)
            else:
                parsed_conditions.append(CollectionCondition.from_dict(dict(item)))
    parsed_values = parse_csv(values)
    if not parsed_conditions:
        if match not in MATCH_KINDS:
            raise ValueError(f"Unknown collection match {match!r}")
        if not parsed_values:
            raise ValueError("Collection rule needs something to match")
        parsed_conditions = conditions_from_legacy(match, parsed_values)  # type: ignore[arg-type]
    parsed_collections = parse_csv(collections)
    if not parsed_collections and parsed_conditions:
        first = parsed_conditions[0]
        if first.field in _AUTO_COLLECTION_FIELDS and first.text_values():
            parsed_collections = [first.text_values()[0]]
    legacy = legacy_from_conditions(parsed_conditions)
    rule_match: CollectionMatch = legacy[0] if legacy else (
        "work_id"
        if parsed_conditions and parsed_conditions[0].field == "work_id"
        else "calibre_uuid"
        if parsed_conditions and parsed_conditions[0].field == "calibre_uuid"
        else "mentions"
    )
    rule_values = legacy[1] if legacy else (
        parsed_conditions[0].text_values()
        if parsed_conditions
        and parsed_conditions[0].field in {"work_id", "calibre_uuid"}
        else []
    )
    if pin and not rule_values and parsed_conditions:
        rule_values = parsed_conditions[0].text_values()
    rule = CollectionRule(
        id=rule_id.strip() or "collection",
        match=rule_match,
        values=rule_values,
        collections=parsed_collections,
        mode=mode,  # type: ignore[arg-type]
        enabled=bool(enabled),
        pin=bool(pin),
        description=description,
        all=parsed_conditions,
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
        updated[index] = existing.copy_with(enabled=flag)
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
    payload = {"rules": [rule.to_dict() for rule in rules]}
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
