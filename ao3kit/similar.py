"""Build an AO3 similar-works search from one or more work records.

Classifies fandoms, authors, relationships, characters, and additional tags,
merges multiple records (union, first-seen order), and turns a user selection
into ``SearchCriteria`` fields. No network I/O.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# AO3 archive warnings / ratings / work categories — not useful "similar" tags.
ARCHIVE_WARNINGS = frozenset(
    {
        "creator chose not to use archive warnings",
        "choose not to use archive warnings",
        "no archive warnings apply",
        "graphic depictions of violence",
        "major character death",
        "rape/non-con",
        "underage",
        "underage sex",
    }
)
RATINGS = frozenset(
    {
        "not rated",
        "general audiences",
        "teen and up audiences",
        "mature",
        "explicit",
    }
)
WORK_CATEGORIES = frozenset({"f/f", "f/m", "gen", "m/m", "multi", "other"})
SKIP_TAGS = frozenset(
    {
        "fanfiction",
        "completed",
        "complete",
        "in-progress",
        "in progress",
    }
)
UNKNOWN_AUTHORS = frozenset({"unknown", "unknown author", "anonymous", "anon"})

_REL_SPLIT = re.compile(r"\s+&\s+|\s*/\s*")
_CATEGORY_TO_BUCKET = {
    "fandom": "fandoms",
    "character": "characters",
    "relationship": "relationships",
    "additional tags": "tags",
    "freeform": "tags",
    "warning": None,
    "archive warning": None,
    "rating": None,
    "category": None,
}


@dataclass
class SimilarFacets:
    """Unique values merged from one or more works, in first-seen order."""

    authors: list[str] = field(default_factory=list)
    fandoms: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    work_ids: list[str] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "authors": list(self.authors),
            "fandoms": list(self.fandoms),
            "relationships": list(self.relationships),
            "characters": list(self.characters),
            "tags": list(self.tags),
            "titles": list(self.titles),
            "work_ids": list(self.work_ids),
            "counts": {key: dict(value) for key, value in self.counts.items()},
        }

    def has_any(self) -> bool:
        return bool(
            self.authors
            or self.fandoms
            or self.relationships
            or self.characters
            or self.tags
        )


@dataclass
class SimilarSelect:
    """Which facet values to send to AO3 (AND of every selected tag)."""

    authors: list[str] = field(default_factory=list)
    fandoms: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    excluded_tags: list[str] = field(default_factory=list)
    extra_query: str = ""

    @classmethod
    def default_for(cls, facets: SimilarFacets) -> SimilarSelect:
        """Start from fandoms only so the search is not over-constrained."""
        return cls(fandoms=list(facets.fandoms))

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SimilarSelect:
        data = data or {}
        return cls(
            authors=_unique_names(data.get("authors")),
            fandoms=_unique_names(data.get("fandoms")),
            relationships=_unique_names(data.get("relationships")),
            characters=_unique_names(data.get("characters")),
            tags=_unique_names(data.get("tags")),
            excluded_tags=_unique_names(data.get("excluded_tags")),
            extra_query=str(data.get("extra_query") or data.get("query") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "authors": list(self.authors),
            "fandoms": list(self.fandoms),
            "relationships": list(self.relationships),
            "characters": list(self.characters),
            "tags": list(self.tags),
            "excluded_tags": list(self.excluded_tags),
            "extra_query": self.extra_query,
        }

    def has_any(self) -> bool:
        return bool(
            self.authors
            or self.fandoms
            or self.relationships
            or self.characters
            or self.tags
            or self.excluded_tags
            or self.extra_query
        )


def _unique_names(values: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    if values is None:
        return names
    if isinstance(values, str):
        values = [values]
    for value in values:
        name = str(value).strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def join_tag_names(names: Iterable[str]) -> str:
    return ", ".join(_unique_names(list(names)))


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _is_skip_tag(name: str) -> bool:
    key = name.casefold()
    return (
        key in ARCHIVE_WARNINGS
        or key in RATINGS
        or key in WORK_CATEGORIES
        or key in SKIP_TAGS
    )


def _looks_like_relationship(name: str) -> bool:
    if _is_skip_tag(name):
        return False
    folded = name.casefold()
    if folded.startswith("alternate universe"):
        return False
    if " - freeform" in folded:
        return False
    if " & " in name:
        return True
    if "/" not in name:
        return False
    parts = [part.strip() for part in name.split("/") if part.strip()]
    if len(parts) < 2 or len(parts) > 6:
        return False
    if any(_is_skip_tag(part) for part in parts):
        return False
    return all(_looks_like_character_name(part) for part in parts)


def _looks_like_character_name(part: str) -> bool:
    words = [word for word in part.replace('"', " ").split() if word]
    if not words or len(words) > 8:
        return False
    letters = sum(1 for char in part if char.isalpha())
    return letters >= 2


def characters_from_relationship(name: str) -> list[str]:
    parts = [part.strip() for part in _REL_SPLIT.split(name) if part.strip()]
    return [part for part in parts if _looks_like_character_name(part) and not _is_skip_tag(part)]


def _as_name_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict) and "simplified" in value:
        return _unique_names(value.get("simplified"))
    return _unique_names(value)


def _bucket_for_category(category: Any) -> str | None:
    if category is None:
        return ""
    key = str(category).strip().casefold()
    if key in _CATEGORY_TO_BUCKET:
        return _CATEGORY_TO_BUCKET[key]
    return ""


def _detail_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cleaned = record.get("cleaned")
    if isinstance(cleaned, dict):
        tags = cleaned.get("tags")
        if isinstance(tags, list):
            items.extend(item for item in tags if isinstance(item, dict))
    tags = record.get("tags")
    if isinstance(tags, dict):
        nested = tags.get("tags")
        if isinstance(nested, list):
            items.extend(item for item in nested if isinstance(item, dict))
    return items


def _tag_groups(record: dict[str, Any]) -> dict[str, list[str]]:
    groups = record.get("tag_groups")
    if not isinstance(groups, dict):
        return {}
    return {
        "warnings": _as_name_list(groups.get("warnings")),
        "relationships": _as_name_list(groups.get("relationships")),
        "characters": _as_name_list(groups.get("characters")),
        "freeforms": _as_name_list(groups.get("freeforms")),
    }


def _authors_from_record(record: dict[str, Any]) -> list[str]:
    names = _as_name_list(record.get("authors"))
    if not names:
        names = _as_name_list(record.get("author"))
    return [name for name in names if name.casefold() not in UNKNOWN_AUTHORS]


def _fandoms_from_record(record: dict[str, Any]) -> list[str]:
    cleaned = record.get("cleaned")
    if isinstance(cleaned, dict):
        nested = cleaned.get("fandoms")
        if isinstance(nested, list) and nested:
            return _unique_names(nested)
        if isinstance(nested, dict) and nested.get("simplified"):
            return _as_name_list(nested)
    return _as_name_list(record.get("fandoms"))


def _flat_tags(record: dict[str, Any]) -> list[str]:
    cleaned = record.get("cleaned")
    if isinstance(cleaned, dict):
        original = cleaned.get("original")
        if isinstance(original, list) and original:
            return _unique_names(original)
    tags = record.get("tags")
    if isinstance(tags, dict):
        original = tags.get("original")
        if isinstance(original, list) and original:
            return _unique_names(original)
        return _as_name_list(tags.get("simplified"))
    return _as_name_list(tags)


class _FacetAcc:
    def __init__(self) -> None:
        self.order: dict[str, list[str]] = {
            "authors": [],
            "fandoms": [],
            "relationships": [],
            "characters": [],
            "tags": [],
        }
        self.seen: dict[str, set[str]] = {key: set() for key in self.order}
        self.counts: dict[str, dict[str, int]] = {key: {} for key in self.order}
        self.titles: list[str] = []
        self.work_ids: list[str] = []

    def add(self, bucket: str, names: Iterable[str], *, counted: set[str]) -> None:
        for name in _unique_names(list(names)):
            if _is_skip_tag(name) and bucket != "authors":
                continue
            key = name.casefold()
            if key not in self.seen[bucket]:
                self.seen[bucket].add(key)
                self.order[bucket].append(name)
                display = name
            else:
                display = next(item for item in self.order[bucket] if item.casefold() == key)
            count_key = f"{bucket}:{key}"
            if count_key not in counted:
                counted.add(count_key)
                self.counts[bucket][display] = self.counts[bucket].get(display, 0) + 1

    def facets(self) -> SimilarFacets:
        return SimilarFacets(
            authors=list(self.order["authors"]),
            fandoms=list(self.order["fandoms"]),
            relationships=list(self.order["relationships"]),
            characters=list(self.order["characters"]),
            tags=list(self.order["tags"]),
            titles=list(self.titles),
            work_ids=list(self.work_ids),
            counts={key: dict(value) for key, value in self.counts.items()},
        )


def _classify_record(record: dict[str, Any]) -> dict[str, list[str]]:
    fandoms = _fandoms_from_record(record)
    fandom_keys = {name.casefold() for name in fandoms}
    relationships: list[str] = []
    characters: list[str] = []
    leftover: list[str] = []
    used: set[str] = set(fandom_keys)

    def claim(name: str) -> str | None:
        key = name.casefold()
        if not name or key in used or _is_skip_tag(name):
            return None
        used.add(key)
        return name

    def add_rel(name: str) -> None:
        claimed = claim(name)
        if claimed:
            relationships.append(claimed)

    def add_char(name: str) -> None:
        claimed = claim(name)
        if claimed:
            characters.append(claimed)

    for name in _as_name_list(record.get("relationships")):
        add_rel(name)
    groups = _tag_groups(record)
    for name in groups.get("relationships") or []:
        add_rel(name)
    for name in _as_name_list(record.get("characters")):
        add_char(name)
    for name in groups.get("characters") or []:
        add_char(name)

    for item in _detail_items(record):
        if item.get("dropped"):
            continue
        name = str(item.get("mapped") or item.get("original") or "").strip()
        bucket = _bucket_for_category(item.get("category"))
        if bucket == "fandoms":
            if name and name.casefold() not in fandom_keys and not _is_skip_tag(name):
                fandoms.append(name)
                fandom_keys.add(name.casefold())
                used.add(name.casefold())
            continue
        if bucket == "relationships":
            add_rel(name)
        elif bucket == "characters":
            add_char(name)
        elif bucket == "tags":
            leftover.append(name)

    for name in groups.get("freeforms") or []:
        leftover.append(name)

    for name in _flat_tags(record):
        if name.casefold() in fandom_keys or _is_skip_tag(name):
            continue
        if name.casefold() in used:
            continue
        if _looks_like_relationship(name):
            add_rel(name)
            continue
        leftover.append(name)

    for rel in relationships:
        for part in characters_from_relationship(rel):
            add_char(part)

    tags: list[str] = []
    for name in leftover:
        if name.casefold() in used or _is_skip_tag(name) or name.casefold() in fandom_keys:
            continue
        claimed = claim(name)
        if claimed:
            tags.append(claimed)

    return {
        "authors": _authors_from_record(record),
        "fandoms": _unique_names(fandoms),
        "relationships": _unique_names(relationships),
        "characters": _unique_names(characters),
        "tags": _unique_names(tags),
    }


def facets_from_record(record: dict[str, Any]) -> SimilarFacets:
    return facets_from_records([record])


def facets_from_records(records: Iterable[dict[str, Any]]) -> SimilarFacets:
    """Union facets across works. Later works add new names; counts increment."""
    acc = _FacetAcc()
    for record in records:
        if not isinstance(record, dict):
            continue
        counted: set[str] = set()
        classified = _classify_record(record)
        acc.add("authors", classified["authors"], counted=counted)
        acc.add("fandoms", classified["fandoms"], counted=counted)
        acc.add("relationships", classified["relationships"], counted=counted)
        acc.add("characters", classified["characters"], counted=counted)
        acc.add("tags", classified["tags"], counted=counted)
        title = str(record.get("title") or "").strip()
        if title:
            acc.titles.append(title)
        work_id = str(record.get("work_id") or "").strip()
        if work_id:
            acc.work_ids.append(work_id)
    return acc.facets()


def filter_records(
    records: list[dict[str, Any]],
    work_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    wanted = {str(item).strip() for item in (work_ids or []) if str(item).strip()}
    if not wanted:
        return [record for record in records if isinstance(record, dict)]
    matched: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        work_id = str(record.get("work_id") or "").strip()
        if work_id in wanted:
            matched.append(record)
    return matched


def records_from_jsonl_text(text: str, *, source: str = "<jsonl>") -> list[dict[str, Any]]:
    """Parse JSONL work objects. Work id is optional (unlike download JSONL)."""
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{source}:{line_no}: expected a JSON object")
        records.append(data)
    return records


def load_similar_records(path: str | Path) -> list[dict[str, Any]]:
    dest = Path(path)
    return records_from_jsonl_text(dest.read_text(encoding="utf-8"), source=str(dest))


def selection_to_fields(select: SimilarSelect) -> dict[str, str]:
    """Map a crafted selection onto AO3 search form fields (names, not IDs)."""
    fandoms = _unique_names(select.fandoms)
    tag_id = fandoms[0] if fandoms else ""
    extra_fandoms = fandoms[1:]
    other = _unique_names(
        list(extra_fandoms)
        + list(select.relationships)
        + list(select.characters)
        + list(select.tags)
    )
    return {
        "tag_id": tag_id,
        "creators": join_tag_names(select.authors),
        "other_tag_names": join_tag_names(other),
        "excluded_tag_names": join_tag_names(select.excluded_tags),
        "query": str(select.extra_query or "").strip(),
    }


def build_select(
    facets: SimilarFacets,
    *,
    include_all: Iterable[str] | None = None,
    picks: SimilarSelect | None = None,
) -> SimilarSelect:
    """Combine 'include every value of this type' with explicit picks.

    Fandoms always default to every seed fandom unless the caller listed
    specific fandom names.
    """
    picks = picks or SimilarSelect()
    include = {str(item).strip().casefold() for item in (include_all or []) if str(item).strip()}
    explicit_fandoms = bool(picks.fandoms)
    result = SimilarSelect(
        excluded_tags=list(picks.excluded_tags),
        extra_query=picks.extra_query,
    )

    def fill(kind: str, all_values: list[str], picked: list[str]) -> list[str]:
        if picked:
            return _unique_names(picked)
        if kind in include:
            return list(all_values)
        return []

    result.authors = fill("authors", facets.authors, picks.authors)
    result.relationships = fill("relationships", facets.relationships, picks.relationships)
    result.characters = fill("characters", facets.characters, picks.characters)
    result.tags = fill("tags", facets.tags, picks.tags)

    if explicit_fandoms:
        result.fandoms = _unique_names(picks.fandoms)
    else:
        result.fandoms = list(facets.fandoms)
    return result


def criteria_from_selection(
    select: SimilarSelect,
    *,
    sort_column: str = "kudos_count",
    complete: bool | None = None,
    language_id: str | None = "en",
    words_from: int | None = None,
    words_to: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    crossover: str = "",
):
    from ao3kit.scrape import SearchCriteria

    fields = selection_to_fields(select)
    return SearchCriteria(
        tag_id=fields["tag_id"] or None,
        sort_column=sort_column or "kudos_count",
        complete=complete,
        words_from=words_from,
        words_to=words_to,
        date_from=date_from or None,
        date_to=date_to or None,
        query=fields["query"] or None,
        language_id=language_id,
        other_tag_names=fields["other_tag_names"],
        excluded_tag_names=fields["excluded_tag_names"],
        crossover=crossover or "",
        creators=fields["creators"],
    )


def similar_payload(
    records: list[dict[str, Any]],
    select: SimilarSelect | None = None,
    *,
    include_all: Iterable[str] | None = None,
    sort_column: str = "kudos_count",
    complete: bool | None = None,
    language_id: str | None = "en",
    words_from: int | None = None,
    words_to: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    crossover: str = "",
) -> dict[str, Any]:
    """Facets + criteria JSON for CLI ``--parse-similar`` and ``POST /scrape/similar``."""
    from dataclasses import asdict as _asdict

    from ao3kit.scrape import build_search_url

    facets = facets_from_records(records)
    resolved = build_select(facets, include_all=include_all, picks=select)
    criteria = criteria_from_selection(
        resolved,
        sort_column=sort_column,
        complete=complete,
        language_id=language_id,
        words_from=words_from,
        words_to=words_to,
        date_from=date_from,
        date_to=date_to,
        crossover=crossover,
    )
    works = []
    for record in records:
        if not isinstance(record, dict):
            continue
        works.append(
            {
                "work_id": str(record.get("work_id") or "") or None,
                "title": str(record.get("title") or "") or None,
                "author": str(record.get("author") or "") or None,
            }
        )
    return {
        "works": works,
        "facets": facets.to_dict(),
        "select": resolved.to_dict(),
        "criteria": _asdict(criteria),
        "search_url": build_search_url(criteria),
    }
