"""Live tag-graph commands: queue similar searches for the Calibre plugin.

The viewer POSTs here via ``tags graph serve``. The plugin job supervisor
drains the XDG graph inbox and starts a normal scrape/import job.
Does not fetch AO3 or write Calibre itself.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ao3kit.tags.warm import load_jsonl_records

_FACET_KEYS = ("fandoms", "authors", "relationships", "characters", "tags")

def default_graph_inbox() -> Path:
    from ao3kit.paths import graph_inbox_dir

    return graph_inbox_dir()
GRAPH_JOB_ID = "graph"

_INCLUDE_KINDS = frozenset(
    {"fandoms", "relationships", "characters", "tags", "authors"}
)

_TAG_CATEGORY_BUCKET = {
    "fandom": "fandoms",
    "character": "characters",
    "relationship": "relationships",
    "additional tags": "tags",
    "freeform": "tags",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def work_id_of(record: dict[str, Any]) -> str:
    raw = str(record.get("work_id") or "").strip()
    if raw.isdigit():
        return raw
    url = str(record.get("url") or "")
    marker = "/works/"
    idx = url.find(marker)
    if idx < 0:
        return ""
    digits: list[str] = []
    for ch in url[idx + len(marker) :]:
        if ch.isdigit():
            digits.append(ch)
        else:
            break
    return "".join(digits)


def records_for_work_ids(
    jsonl_paths: Iterable[Path | str],
    work_ids: Iterable[str],
) -> list[dict[str, Any]]:
    wanted = {str(item).strip() for item in work_ids if str(item).strip()}
    if not wanted:
        return []
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in jsonl_paths:
        for record in load_jsonl_records(Path(path)):
            work_id = work_id_of(record)
            if work_id in wanted and work_id not in seen:
                seen.add(work_id)
                matched.append(record)
    return matched


def facet_bucket_for(name: str, category: str | None = None) -> str:
    """Which similar-search facet a graph tag belongs in."""
    from ao3kit.similar import _looks_like_relationship

    key = str(category or "").strip().casefold()
    bucket = _TAG_CATEGORY_BUCKET.get(key)
    if bucket:
        return bucket
    if _looks_like_relationship(name):
        return "relationships"
    return "tags"


def record_for_tag(name: str, category: str | None = None) -> dict[str, Any]:
    text = str(name or "").strip()
    if not text:
        return {}
    return {facet_bucket_for(text, category): [text]}


def select_for_tag(name: str, category: str | None = None):
    from ao3kit.similar import SimilarSelect

    text = str(name or "").strip()
    if not text:
        return SimilarSelect()
    return SimilarSelect.from_dict({facet_bucket_for(text, category): [text]})


def records_for_tag_name(
    jsonl_paths: Iterable[Path | str],
    tag_name: str,
) -> list[dict[str, Any]]:
    """Works in the graph dump that already carry this tag."""
    from ao3kit.similar import facets_from_record

    wanted = str(tag_name or "").strip().casefold()
    if not wanted:
        return []
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in jsonl_paths:
        for record in load_jsonl_records(Path(path)):
            facets = facets_from_record(record)
            names = (
                list(facets.fandoms)
                + list(facets.relationships)
                + list(facets.characters)
                + list(facets.tags)
            )
            if not any(str(item).casefold() == wanted for item in names):
                continue
            key = work_id_of(record) or f"row:{len(matched)}"
            if key in seen:
                continue
            seen.add(key)
            matched.append(record)
    return matched


def _facet_items(
    names: list[str],
    counts: dict[str, int] | None,
    selected: Iterable[str],
) -> list[dict[str, Any]]:
    chosen = {str(name).strip().casefold() for name in selected if str(name).strip()}
    bucket = counts or {}
    items: list[dict[str, Any]] = []
    for name in names:
        text = str(name).strip()
        if not text:
            continue
        items.append(
            {
                "name": text,
                "count": int(bucket.get(text, 1) or 1),
                "on": text.casefold() in chosen,
            }
        )
    return items


def _sort_column(value: Any) -> str:
    from ao3kit.scrape import SORT_OPTIONS

    key = str(value or "").strip() or "kudos_count"
    allowed = {item[0] for item in SORT_OPTIONS}
    return key if key in allowed else "kudos_count"


def _complete_choice(value: Any) -> str:
    text = str(value if value is not None else "").strip().lower()
    if text in {"true", "t", "1", "yes"}:
        return "true"
    if text in {"false", "f", "0", "no"}:
        return "false"
    return ""


def similar_search_is_usable(options: dict[str, Any]) -> bool:
    """True when AO3 has at least one search term (same bar as the plugin)."""
    return bool(
        str(options.get("url") or "").strip()
        or str(options.get("tag_id") or "").strip()
        or str(options.get("query") or "").strip()
        or str(options.get("creators") or "").strip()
        or str(options.get("other_tag_names") or "").strip()
    )


def similar_preview(
    records: list[dict[str, Any]],
    *,
    selected: Any | None = None,
) -> dict[str, Any]:
    """Checklist payload for the live viewer similar-search form."""
    from ao3kit.scrape import SORT_OPTIONS
    from ao3kit.similar import SimilarSelect, facets_from_records

    facets = facets_from_records(records)
    chosen = SimilarSelect.default_for(facets) if selected is None else selected
    counts = facets.counts or {}
    on = {
        "fandoms": chosen.fandoms,
        "authors": list(chosen.authors),
        "relationships": list(chosen.relationships),
        "characters": list(chosen.characters),
        "tags": list(chosen.tags),
    }
    payload: dict[str, Any] = {
        "titles": list(facets.titles),
        "work_ids": list(facets.work_ids),
    }
    for key in _FACET_KEYS:
        payload[key] = _facet_items(
            getattr(facets, key), counts.get(key), on[key]
        )
    exclude_names: list[str] = []
    seen: set[str] = set()
    for name in list(facets.tags) + list(facets.relationships):
        folded = name.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        exclude_names.append(name)
    payload["exclude"] = _facet_items(exclude_names, {}, [])
    payload.update(
        {
            "sort_options": [
                {"value": value, "label": label} for value, label in SORT_OPTIONS
            ],
            "sort_column": "kudos_count",
            "complete_options": [
                {"value": "", "label": "Any"},
                {"value": "true", "label": "Complete only"},
                {"value": "false", "label": "In progress only"},
            ],
            "complete": "",
            "language_id": "en",
            "max_results": "25",
            "hint": (
                "AO3 requires every tag you check. Start with a fandom "
                "and add only a ship or two."
            ),
        }
    )
    return payload


def similar_scrape_options(
    records: list[dict[str, Any]],
    *,
    include: Iterable[str] | None = None,
    select: dict[str, Any] | None = None,
    max_results: str = "25",
    sort_column: str = "kudos_count",
    complete: Any = None,
    language_id: str = "en",
    min_score: str = "",
    min_kudos: str = "",
    min_words: str = "",
    complete_only: bool = False,
) -> dict[str, Any]:
    """Plugin ``plan_scrape`` options for a similar-works search."""
    from ao3kit.similar import (
        SimilarSelect,
        build_select,
        facets_from_records,
        selection_to_fields,
    )

    facets = facets_from_records(records)
    if select is not None:
        chosen = SimilarSelect.from_dict(select)
    else:
        kinds = [
            str(item).strip().casefold()
            for item in (include or ("fandoms",))
            if str(item).strip().casefold() in _INCLUDE_KINDS
        ] or ["fandoms"]
        chosen = build_select(facets, include_all=kinds)
    fields = selection_to_fields(chosen)
    return {
        "url": "",
        "use_form_criteria": True,
        "tag_id": fields["tag_id"],
        "query": fields["query"],
        "creators": fields["creators"],
        "sort_column": _sort_column(sort_column),
        "complete": _complete_choice(complete),
        "language_id": str(language_id or "").strip() or "en",
        "words_from": "",
        "words_to": "",
        "date_from": "",
        "date_to": "",
        "other_tag_names": fields["other_tag_names"],
        "excluded_tag_names": fields["excluded_tag_names"],
        "relationship_ids": "",
        "freeform_ids": "",
        "character_ids": "",
        "max_results": str(max_results or "25").strip() or "25",
        "start_page": "1",
        "min_score": str(min_score or "").strip(),
        "min_kudos": str(min_kudos or "").strip(),
        "min_words": str(min_words or "").strip(),
        "complete_only": bool(complete_only),
        "graph_live": True,
    }


def queue_graph_command(
    payload: dict[str, Any],
    *,
    inbox: Path | None = None,
) -> Path:
    directory = Path(inbox or default_graph_inbox())
    directory.mkdir(parents=True, exist_ok=True)
    cmd_id = str(payload.get("id") or uuid.uuid4().hex[:12])
    body = dict(payload)
    body.setdefault("id", cmd_id)
    body.setdefault("status", "pending")
    body.setdefault("created_at", _utc_now())
    path = directory / f"{cmd_id}.json"
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def queue_similar_command(
    *,
    work_ids: Iterable[str],
    records: list[dict[str, Any]],
    include: Iterable[str] | None = None,
    select: dict[str, Any] | None = None,
    max_results: str = "25",
    sort_column: str = "kudos_count",
    complete: Any = None,
    language_id: str = "en",
    min_score: str = "",
    min_kudos: str = "",
    min_words: str = "",
    complete_only: bool = False,
    tag: str | None = None,
    titles: Iterable[str] | None = None,
    inbox: Path | None = None,
) -> dict[str, Any]:
    from ao3kit.similar import SimilarSelect, facets_from_records

    ids = [str(item).strip() for item in work_ids if str(item).strip()]
    tag_name = str(tag or "").strip()
    options = similar_scrape_options(
        records,
        include=include,
        select=select,
        max_results=max_results,
        sort_column=sort_column,
        complete=complete,
        language_id=language_id,
        min_score=min_score,
        min_kudos=min_kudos,
        min_words=min_words,
        complete_only=complete_only,
    )
    facets = facets_from_records(records)
    chosen = SimilarSelect.from_dict(select) if select is not None else None
    title_list = [str(item).strip() for item in (titles or []) if str(item).strip()]
    if not title_list and tag_name:
        title_list = [tag_name]
    if not title_list:
        title_list = list(facets.titles)
    payload = {
        "kind": "similar",
        "work_ids": ids,
        "include": list(include or (("fandoms",) if select is None else ())),
        "select": chosen.to_dict() if chosen is not None else None,
        "options": options,
        "titles": title_list,
        "fandoms": list(chosen.fandoms if chosen is not None else facets.fandoms),
    }
    if tag_name:
        payload["tag"] = tag_name
    path = queue_graph_command(payload, inbox=inbox)
    payload["path"] = str(path)
    payload["id"] = json.loads(path.read_text(encoding="utf-8")).get("id")
    return payload


def pending_inbox_count(inbox: Path | None = None) -> int:
    directory = Path(inbox or default_graph_inbox())
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.glob("*.json") if path.is_file())
