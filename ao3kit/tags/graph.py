"""Tag-relationship graph from the AO3 wrangling cache.

Nodes are works and tags. Edges are work → tag (every tag on that work),
synonym → canonical, and tag → metatag. Tags that share a work are
connected through the work node.

Seed names (library dump, JSONL, or argv) keep the graph to tags you
actually have; related canonicals and metatag ancestors are pulled in as
connectors. JSONL also places each work as a node.

CLI::

    python -m ao3kit tags graph --names-file tags.txt -o tag-graph.html --open
    python -m ao3kit tags graph --jsonl results.jsonl -o graph.json
    python -m ao3kit tags graph serve
    python -m ao3kit tags graph reload
"""

from __future__ import annotations

import argparse
import atexit
import json
import math
import os
import sys
import webbrowser
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

from ao3kit.tags.cache import (
    DEFAULT_TAG_CACHE_TTL_DAYS,
    CacheRow,
    TagCache,
    default_tag_cache_path,
)
from ao3kit.tags.clean import collect_unique_tag_names
from ao3kit.tags.warm import EXTRA_NAME_KEYS, collect_warm_names, load_jsonl_records

SynonymMode = Literal["seed", "all", "none"]
GraphFormat = Literal["html", "json", "dot"]

DEFAULT_GRAPH_PORT = 8767


def default_graph_html() -> Path:
    from ao3kit.paths import graph_html_file

    return graph_html_file()


def default_graph_jsonl() -> Path:
    from ao3kit.paths import graph_jsonl_file

    return graph_jsonl_file()


def default_graph_json() -> Path:
    from ao3kit.paths import graph_json_file

    return graph_json_file()


def default_graph_serve_stamp() -> Path:
    from ao3kit.paths import graph_serve_stamp_file

    return graph_serve_stamp_file()

# Calibre / FanFicFare status labels, plus AO3 archive warnings (not content tags).
_SKIP_WORK_TAGS = frozenset(
    {
        "completed",
        "complete",
        "fanfiction",
        "creator chose not to use archive warnings",
        "no archive warnings apply",
        "graphic depictions of violence",
        "major character death",
        "rape/non-con",
        "underage",
    }
)
_RELATED_FANDOMS_SUFFIX = " & related fandoms"


@dataclass
class TagGraphNode:
    id: str
    name: str
    status: str
    category: str | None
    in_seed: bool
    canonical: str
    rank: int = 0
    degree: int = 0
    component: int = 0
    kind: str = "tag"
    url: str | None = None
    x: float = 0.0
    y: float = 0.0
    cluster: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "category": self.category,
            "in_seed": self.in_seed,
            "canonical": self.canonical,
            "rank": self.rank,
            "degree": self.degree,
            "component": self.component,
            "kind": self.kind,
            "url": self.url,
            "x": self.x,
            "y": self.y,
            "cluster": self.cluster,
        }


@dataclass(frozen=True)
class GraphWork:
    """A library/JSONL work to place as a node linked to its tags."""

    work_id: str
    title: str
    tags: tuple[str, ...]
    url: str | None = None


@dataclass(frozen=True)
class TagGraphEdge:
    source: str
    target: str
    kind: str
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "weight": self.weight,
        }


def edge_weight(kind: str, *, hub_degree: int = 1) -> float:
    """Line weight in the viewer. Busy works keep strong spokes."""
    if kind == "synonym":
        return 2.0
    if kind == "metatag":
        return 0.7
    return 1.0 + 0.1 * math.log(max(int(hub_degree), 1))


@dataclass
class TagGraphComponent:
    id: int
    size: int
    edge_count: int
    sample: list[str] = field(default_factory=list)
    hub: str = ""
    title: str = ""
    library_count: int = 0
    category: str | None = None
    work_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "size": self.size,
            "edge_count": self.edge_count,
            "sample": list(self.sample),
            "hub": self.hub,
            "title": self.title,
            "library_count": self.library_count,
            "category": self.category,
            "work_count": self.work_count,
        }


@dataclass
class TagGraph:
    nodes: list[TagGraphNode]
    edges: list[TagGraphEdge]
    components: list[TagGraphComponent]
    seed_count: int
    cached_seed: int
    missing_seed: int
    synonym_edges: int
    metatag_edges: int
    work_count: int = 0
    work_edges: int = 0
    hubs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "components": [item.to_dict() for item in self.components],
            "hubs": list(self.hubs),
            "seed_count": self.seed_count,
            "cached_seed": self.cached_seed,
            "missing_seed": self.missing_seed,
            "synonym_edges": self.synonym_edges,
            "metatag_edges": self.metatag_edges,
            "work_count": self.work_count,
            "work_edges": self.work_edges,
        }


def _ao3_work_id(record: dict[str, Any]) -> str:
    """Numeric AO3 work id, or empty if this is not an AO3 work."""
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


def works_from_records(records: Sequence[dict[str, Any]]) -> list[GraphWork]:
    """Turn scrape/library JSON objects into work nodes.

    Skips Calibre rows that are not AO3 works (no numeric work id / works URL)
    so news items and other library books are not mixed into the graph.
    """
    works: list[GraphWork] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        work_id = _ao3_work_id(record)
        if not work_id:
            continue
        if work_id in seen:
            continue
        seen.add(work_id)
        title = str(record.get("title") or "").strip() or f"AO3 work {work_id}"
        url = str(record.get("url") or "").strip() or (
            f"https://archiveofourown.org/works/{work_id}"
        )
        tags = tuple(
            name
            for name in collect_unique_tag_names(
                [record], include_fandoms=True, extra_keys=EXTRA_NAME_KEYS
            )
            if name.casefold() not in _SKIP_WORK_TAGS
        )
        works.append(
            GraphWork(work_id=work_id, title=title, tags=tags, url=url)
        )
    return works


def _missing_row(name: str) -> CacheRow:
    return CacheRow(
        name=name,
        canonical=name,
        status="missing",
        category=None,
        metatags=None,
    )


def _metatags_of(row: CacheRow, rows: dict[str, CacheRow]) -> list[str]:
    if row.status == "synonym":
        parent = rows.get(row.canonical)
        if parent is not None and parent.metatags is not None:
            return list(parent.metatags)
        return []
    return list(row.metatags or [])


def _collect_rows(
    cache: TagCache,
    seed: Sequence[str],
    *,
    synonyms: SynonymMode,
    include_metatags: bool,
) -> dict[str, CacheRow]:
    """Load seed rows plus related canonical / synonym / metatag rows."""
    wanted: set[str] = {name for name in seed if name}
    rows = dict(cache.get_rows(wanted))

    extra: set[str] = set()
    canonicals: set[str] = set()
    for name in list(wanted):
        row = rows.get(name)
        if row is None:
            continue
        if row.canonical and row.canonical != name:
            extra.add(row.canonical)
        if row.canonical:
            canonicals.add(row.canonical)
    if synonyms == "all":
        for canonical in canonicals:
            for syn in cache.rows_for_canonical(canonical):
                extra.add(syn.name)

    if extra:
        rows.update(cache.get_rows(extra))

    if include_metatags:
        pending: deque[str] = deque()
        seen_meta: set[str] = set()
        for row in list(rows.values()):
            for meta in _metatags_of(row, rows):
                if meta not in rows and meta not in seen_meta:
                    seen_meta.add(meta)
                    pending.append(meta)
        while pending:
            name = pending.popleft()
            found = cache.get_rows([name])
            if name in found:
                rows[name] = found[name]
                for meta in _metatags_of(rows[name], rows):
                    if meta not in rows and meta not in seen_meta:
                        seen_meta.add(meta)
                        pending.append(meta)
            else:
                rows[name] = _missing_row(name)

    for name in seed:
        if name and name not in rows:
            rows[name] = _missing_row(name)
    return rows


def _union_find(ids: Sequence[str], edges: Sequence[TagGraphEdge]) -> dict[str, str]:
    parent = {node_id: node_id for node_id in ids}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    for edge in edges:
        if edge.source in parent and edge.target in parent:
            union(edge.source, edge.target)
    return {node_id: find(node_id) for node_id in ids}


def _assign_ranks(nodes: dict[str, TagGraphNode], edges: Sequence[TagGraphEdge]) -> None:
    parents: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.kind != "metatag":
            continue
        parents[edge.source].append(edge.target)

    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def rank_of(name: str) -> int:
        if name in memo:
            return memo[name]
        if name in visiting:
            return 0
        pts = parents.get(name) or []
        if not pts:
            memo[name] = 0
            return 0
        visiting.add(name)
        memo[name] = 1 + max(rank_of(parent) for parent in pts)
        visiting.remove(name)
        return memo[name]

    for node in nodes.values():
        node.rank = rank_of(node.id)
    for node in nodes.values():
        if node.status == "synonym" and node.canonical in nodes:
            node.rank = nodes[node.canonical].rank


def _metatag_ancestors(start: str, parents: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(parents.get(start, ()))
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        stack.extend(parents.get(name, ()))
    return seen


def reduce_metatag_edges(edges: Sequence[TagGraphEdge]) -> list[TagGraphEdge]:
    """Keep only immediate metatag parents.

    AO3 profile pages list the whole ancestor chain. Tag→grandparent edges
    flatten the graph into a star; drop any parent that is already reachable
    through a more specific parent.
    """
    parents: dict[str, set[str]] = defaultdict(set)
    meta: list[TagGraphEdge] = []
    other: list[TagGraphEdge] = []
    for edge in edges:
        if edge.kind != "metatag":
            other.append(edge)
            continue
        meta.append(edge)
        parents[edge.source].add(edge.target)
    cached: dict[str, set[str]] = {}

    def ancestors(name: str) -> set[str]:
        if name not in cached:
            cached[name] = _metatag_ancestors(name, parents)
        return cached[name]

    kept: list[TagGraphEdge] = []
    for edge in meta:
        others = parents[edge.source] - {edge.target}
        if any(edge.target in ancestors(other) for other in others):
            continue
        kept.append(edge)
    return other + kept


def _pick_hub(
    members: Sequence[TagGraphNode],
    edges: Sequence[TagGraphEdge],
) -> TagGraphNode:
    """Prefer the most general metatag that other members point at."""
    members = [node for node in members if node.kind != "work"] or list(members)
    ids = {node.id for node in members}
    incoming: dict[str, int] = defaultdict(int)
    for edge in edges:
        if (
            edge.kind == "metatag"
            and edge.target in ids
            and edge.source in ids
        ):
            incoming[edge.target] += 1

    def score(node: TagGraphNode) -> tuple[int, int, int, int]:
        return (
            incoming.get(node.id, 0),
            1 if node.status == "canonical" else 0,
            -node.rank,
            node.degree,
        )

    return max(members, key=score)


def _assign_components(
    nodes: Sequence[TagGraphNode],
    edges: Sequence[TagGraphEdge],
) -> list[TagGraphComponent]:
    roots = _union_find([node.id for node in nodes], edges)
    grouped: dict[str, list[TagGraphNode]] = defaultdict(list)
    for node in nodes:
        grouped[roots[node.id]].append(node)
    edge_counts: dict[str, int] = defaultdict(int)
    for edge in edges:
        root = roots.get(edge.source)
        if root is not None:
            edge_counts[root] += 1
    components: list[TagGraphComponent] = []
    ordered_roots = sorted(
        grouped, key=lambda root: (-len(grouped[root]), root.casefold())
    )
    for index, root in enumerate(ordered_roots):
        members = grouped[root]
        hub = _pick_hub(members, edges)
        sample = [hub.name]
        for node in members:
            if node.name in sample:
                continue
            if node.in_seed:
                sample.append(node.name)
            if len(sample) >= 4:
                break
        if len(sample) < 3:
            for node in members:
                if node.name not in sample:
                    sample.append(node.name)
                if len(sample) >= 4:
                    break
        library_count = sum(1 for node in members if node.in_seed)
        work_count = sum(1 for node in members if node.kind == "work")
        components.append(
            TagGraphComponent(
                id=index,
                size=len(members),
                edge_count=edge_counts[root],
                sample=sample,
                hub=hub.name,
                title=hub.name,
                library_count=library_count,
                category=hub.category,
                work_count=work_count,
            )
        )
        for node in members:
            node.component = index
    return components


_GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))


def _stable_angle(text: str) -> float:
    h = 2166136261
    for char in text:
        h = ((h ^ ord(char)) * 16777619) & 0xFFFFFFFF
    return (h / 0xFFFFFFFF) * math.pi * 2


def _sunflower(count: int, spacing: float) -> list[tuple[float, float]]:
    if count <= 0:
        return []
    if count == 1:
        return [(0.0, 0.0)]
    return [
        (
            spacing * math.sqrt(index + 0.5) * math.cos(index * _GOLDEN_ANGLE),
            spacing * math.sqrt(index + 0.5) * math.sin(index * _GOLDEN_ANGLE),
        )
        for index in range(count)
    ]


def _node_radius(node: TagGraphNode) -> float:
    depth = math.log(1 + max(node.degree, 0))
    if node.kind == "work":
        return 10.0 + depth * 0.7
    return 3.8 + depth * 0.5


def graph_hubs(
    nodes: Sequence[TagGraphNode],
    edges: Sequence[TagGraphEdge],
    *,
    limit: int = 16,
) -> list[dict[str, Any]]:
    """Tags that appear on two or more works — the useful overlap."""
    works_per_tag: dict[str, int] = defaultdict(int)
    for edge in edges:
        if edge.kind == "work":
            works_per_tag[edge.target] += 1
    hubs: list[dict[str, Any]] = []
    for node in nodes:
        if node.kind == "work":
            continue
        count = works_per_tag.get(node.id, 0)
        if count < 2:
            continue
        hubs.append(
            {
                "id": node.id,
                "name": node.name,
                "works": count,
                "category": node.category,
            }
        )
    hubs.sort(key=lambda item: (-int(item["works"]), str(item["name"]).casefold()))
    return hubs[:limit]


def _work_tag_index(
    edges: Sequence[TagGraphEdge],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    tag_works: dict[str, list[str]] = defaultdict(list)
    work_tags: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.kind != "work":
            continue
        work_tags[edge.source].append(edge.target)
        tag_works[edge.target].append(edge.source)
    return tag_works, work_tags


def fandom_stem(name: str) -> str:
    """Franchise key: 'Doctor Who (2005)' and 'Doctor Who & Related Fandoms' → doctor who."""
    text = name.strip().casefold()
    if text.endswith(_RELATED_FANDOMS_SUFFIX):
        text = text[: -len(_RELATED_FANDOMS_SUFFIX)].rstrip()
    cut = len(text)
    for sep in (" (", " - "):
        idx = text.find(sep)
        if 0 < idx < cut:
            cut = idx
    return text[:cut].strip()


def fandom_families(
    nodes: Sequence[TagGraphNode],
    edges: Sequence[TagGraphEdge],
) -> dict[str, str]:
    """Map each fandom node id to a franchise root (metatags + name stems)."""
    fandoms = [
        node
        for node in nodes
        if node.kind != "work" and node.category == "Fandom"
    ]
    parent: dict[str, str] = {node.id: node.id for node in fandoms}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    by_stem: dict[str, list[str]] = defaultdict(list)
    for node in fandoms:
        stem = fandom_stem(node.name)
        if stem:
            by_stem[stem].append(node.id)
    for group in by_stem.values():
        root = group[0]
        for other in group[1:]:
            union(root, other)
    by_id = {node.id: node for node in nodes}
    for edge in edges:
        if edge.kind != "metatag":
            continue
        src, dst = by_id.get(edge.source), by_id.get(edge.target)
        if src is None or dst is None:
            continue
        if src.category != "Fandom" or dst.category != "Fandom":
            continue
        union(src.id, dst.id)
    return {node.id: find(node.id) for node in fandoms}


def _fandom_family_count(
    fandom_ids: Sequence[str],
    by_id: dict[str, TagGraphNode],
    families: dict[str, str] | None,
) -> int:
    roots: set[str] = set()
    for tag_id in fandom_ids:
        if families:
            roots.add(families.get(tag_id, tag_id))
            continue
        node = by_id.get(tag_id)
        roots.add(fandom_stem(node.name if node is not None else tag_id) or tag_id)
    return len(roots)


def _family_cluster_name(
    fandom_ids: Sequence[str],
    families: dict[str, str],
    tag_works: dict[str, list[str]],
    by_id: dict[str, TagGraphNode],
) -> str:
    """Public name for one franchise: most works, then shortest tag."""
    root = families.get(fandom_ids[0], fandom_ids[0]) if families else fandom_ids[0]
    pool = [
        fid for fid in fandom_ids if (families.get(fid, fid) if families else fid) == root
    ] or list(fandom_ids)
    return max(
        pool,
        key=lambda name: (
            len(tag_works.get(name, ())),
            -len((by_id[name].name if by_id.get(name) is not None else name)),
            name.casefold(),
        ),
    )


def _cluster_key(
    work_id: str,
    work_tags: dict[str, list[str]],
    tag_works: dict[str, list[str]],
    by_id: dict[str, TagGraphNode],
    *,
    mode: str = "fandom",
    families: dict[str, str] | None = None,
) -> str:
    names = work_tags.get(work_id) or []
    fandoms = [
        name
        for name in names
        if by_id.get(name) is not None and by_id[name].category == "Fandom"
    ]
    ships = [
        name
        for name in names
        if by_id.get(name) is not None and by_id[name].category == "Relationship"
    ]
    if mode == "one":
        return "_all"
    if mode == "crossover":
        if _fandom_family_count(fandoms, by_id, families) >= 2:
            return "_crossover"
        if fandoms:
            return _family_cluster_name(fandoms, families or {}, tag_works, by_id)
    if mode == "ship":
        pool = ships or fandoms or names
    elif mode in {"fandom", "crossover"}:
        pool = fandoms or names
    elif mode == "fandom-large":
        pool = fandoms or names
    else:
        pool = fandoms or names
    if not pool:
        return "_other"
    if mode == "fandom-large":
        return max(
            pool,
            key=lambda name: (len(tag_works.get(name, ())), name.casefold()),
        )
    return min(
        pool, key=lambda name: (len(tag_works.get(name, ())), name.casefold())
    )


def work_cluster_key(
    work_id: str,
    nodes: Sequence[TagGraphNode],
    edges: Sequence[TagGraphEdge],
    *,
    mode: str = "fandom",
) -> str:
    """Which layout group a work belongs to (fandom, crossovers, ship, …)."""
    by_id = {node.id: node for node in nodes}
    tag_works, work_tags = _work_tag_index(edges)
    families = fandom_families(nodes, edges) if mode == "crossover" else None
    return _cluster_key(
        work_id, work_tags, tag_works, by_id, mode=mode, families=families
    )


def _layout_cluster(
    works: Sequence[TagGraphNode],
    local_tags: Sequence[TagGraphNode],
    tag_works: dict[str, list[str]],
) -> float:
    """Pack one fandom around the origin. Shared tags sit between its works."""
    work_at = {}
    for work, (x, y) in zip(works, _sunflower(len(works), 38.0)):
        work.x, work.y = x, y
        work_at[work.id] = work
    exclusive: dict[str, list[TagGraphNode]] = defaultdict(list)
    for tag in local_tags:
        hosts = [wid for wid in tag_works.get(tag.id, ()) if wid in work_at]
        if len(hosts) >= 2:
            tag.x = sum(work_at[wid].x for wid in hosts) / len(hosts)
            tag.y = sum(work_at[wid].y for wid in hosts) / len(hosts)
        elif len(hosts) == 1:
            exclusive[hosts[0]].append(tag)
        else:
            tag.x, tag.y = 0.0, 0.0
    for work_id, group in exclusive.items():
        host = work_at[work_id]
        for tag, (x, y) in zip(group, _sunflower(len(group), 12.0)):
            dist = math.hypot(x, y)
            if dist < 1e-6:
                tag.x = host.x + 22.0
                tag.y = host.y
            else:
                scale = (dist + 20.0) / dist
                tag.x = host.x + x * scale
                tag.y = host.y + y * scale
    radius = 40.0
    for node in list(works) + list(local_tags):
        radius = max(radius, math.hypot(node.x, node.y) + _node_radius(node) + 12.0)
    return radius


def _place_relative(
    leftover: Sequence[TagGraphNode],
    edges: Sequence[TagGraphEdge],
    by_id: dict[str, TagGraphNode],
) -> None:
    if not leftover:
        return
    leftover_ids = {node.id for node in leftover}
    adj: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adj[edge.source].append(edge.target)
        adj[edge.target].append(edge.source)
    for _round in range(5):
        for node in leftover:
            nbrs = [
                by_id[other]
                for other in adj.get(node.id, ())
                if other in by_id and other not in leftover_ids
            ]
            if not nbrs and _round > 0:
                nbrs = [by_id[other] for other in adj.get(node.id, ()) if other in by_id]
            if not nbrs:
                continue
            node.x = sum(item.x for item in nbrs) / len(nbrs)
            node.y = sum(item.y for item in nbrs) / len(nbrs)
            node.x += 10.0 * math.cos(_stable_angle(node.id))
            node.y += 10.0 * math.sin(_stable_angle(node.id))


def _layout_by_component(
    nodes: Sequence[TagGraphNode],
    edges: Sequence[TagGraphEdge],
) -> None:
    groups: dict[int, list[TagGraphNode]] = defaultdict(list)
    for node in nodes:
        groups[node.component].append(node)
    placed: list[tuple[float, float, float]] = []
    for cid in sorted(groups, key=lambda key: (-len(groups[key]), key)):
        members = groups[cid]
        for node, (x, y) in zip(members, _sunflower(len(members), 22.0)):
            node.x, node.y = x, y
        radius = 16.0 + max(
            (math.hypot(node.x, node.y) + _node_radius(node) for node in members),
            default=20.0,
        )
        cx = cy = 0.0
        if placed:
            for step in range(1, 4000):
                dist = math.sqrt(step) * 48.0
                ang = step * _GOLDEN_ANGLE
                cand_x = dist * math.cos(ang)
                cand_y = dist * math.sin(ang)
                if all(
                    math.hypot(cand_x - px, cand_y - py) >= radius + pr + 36.0
                    for px, py, pr in placed
                ):
                    cx, cy = cand_x, cand_y
                    break
        for node in members:
            node.x += cx
            node.y += cy
        placed.append((cx, cy, radius))


def _nudge_apart(nodes: Sequence[TagGraphNode], *, rounds: int = 3, gap: float = 8.0) -> None:
    items = list(nodes)
    radii = {node.id: _node_radius(node) for node in items}
    cell = 28.0
    for _round in range(rounds):
        bins: dict[tuple[int, int], list[TagGraphNode]] = defaultdict(list)
        for node in items:
            bins[(int(node.x // cell), int(node.y // cell))].append(node)
        for node in items:
            cx, cy = int(node.x // cell), int(node.y // cell)
            for ix in (cx - 1, cx, cx + 1):
                for iy in (cy - 1, cy, cy + 1):
                    for other in bins.get((ix, iy), ()):
                        if other is node:
                            continue
                        dx = node.x - other.x
                        dy = node.y - other.y
                        dist = math.hypot(dx, dy) or 0.01
                        need = radii[node.id] + radii[other.id] + gap
                        if dist >= need:
                            continue
                        push = min((need - dist) * 0.5, 10.0)
                        node.x += dx / dist * push
                        node.y += dy / dist * push


def layout_tag_graph(
    nodes: Sequence[TagGraphNode],
    edges: Sequence[TagGraphEdge],
) -> None:
    """Stable geometric layout. No physics.

    Works pack by fandom. Tags shared across fandoms sit in the middle so
    you can see what actually connects the library.
    """
    if not nodes:
        return
    by_id = {node.id: node for node in nodes}
    for node in nodes:
        node.x = 0.0
        node.y = 0.0
        node.cluster = ""
    works = [node for node in nodes if node.kind == "work"]
    tags = [node for node in nodes if node.kind != "work"]
    if not works:
        _layout_by_component(nodes, edges)
        _nudge_apart(nodes)
        for node in nodes:
            node.x = round(node.x, 1)
            node.y = round(node.y, 1)
        return

    tag_works, work_tags = _work_tag_index(edges)
    clusters: dict[str, list[TagGraphNode]] = defaultdict(list)
    for work in works:
        key = _cluster_key(work.id, work_tags, tag_works, by_id)
        work.cluster = key
        clusters[key].append(work)

    tag_cluster_names: dict[str, set[str]] = defaultdict(set)
    for tag in tags:
        for work_id in tag_works.get(tag.id, ()):
            work = by_id.get(work_id)
            if work is not None and work.cluster:
                tag_cluster_names[tag.id].add(work.cluster)

    local_by_cluster: dict[str, list[TagGraphNode]] = defaultdict(list)
    global_tags: list[TagGraphNode] = []
    leftover: list[TagGraphNode] = []
    for tag in tags:
        names = tag_cluster_names.get(tag.id) or set()
        if len(names) >= 2:
            global_tags.append(tag)
            tag.cluster = ""
        elif len(names) == 1:
            key = next(iter(names))
            tag.cluster = key
            local_by_cluster[key].append(tag)
        else:
            leftover.append(tag)

    ordered_keys = sorted(
        clusters, key=lambda key: (-len(clusters[key]), key.casefold())
    )
    packed: list[tuple[str, float]] = []
    for key in ordered_keys:
        packed.append(
            (key, _layout_cluster(clusters[key], local_by_cluster[key], tag_works))
        )

    global_r = 0.0
    for tag, (x, y) in zip(global_tags, _sunflower(len(global_tags), 18.0)):
        tag.x, tag.y = x, y
        global_r = max(global_r, math.hypot(x, y) + _node_radius(tag))

    n_c = len(packed)
    if n_c > 1:
        max_cr = max(radius for _key, radius in packed)
        ring = global_r + max_cr + 70.0
        for index, (key, _radius) in enumerate(packed):
            ang = (2 * math.pi * index / n_c) - math.pi / 2
            cx, cy = ring * math.cos(ang), ring * math.sin(ang)
            for node in clusters[key] + local_by_cluster[key]:
                node.x += cx
                node.y += cy

    _place_relative(leftover, edges, by_id)
    _nudge_apart(nodes)
    for node in nodes:
        node.x = round(node.x, 1)
        node.y = round(node.y, 1)


def build_tag_graph(
    cache: TagCache,
    seed_names: Sequence[str] | None = None,
    *,
    include_metatags: bool = True,
    synonyms: SynonymMode = "seed",
    works: Sequence[GraphWork] | None = None,
) -> TagGraph:
    """Build a relationship graph from cached wrangling data.

    ``seed_names`` limits the graph to those tags plus related canonicals and
    metatag ancestors. ``None`` / empty uses every cached canonical/unmarked
    tag (synonyms still follow ``synonyms``), unless ``works`` is given — then
    seed from the works' tags.
    """
    work_list = list(works or [])
    work_tag_names: list[str] = []
    seen_work_tags: set[str] = set()
    for work in work_list:
        for name in work.tags:
            if name in seen_work_tags:
                continue
            seen_work_tags.add(name)
            work_tag_names.append(name)

    explicit_seed = [str(name).strip() for name in (seed_names or []) if str(name).strip()]
    if not explicit_seed:
        explicit_seed = list(work_tag_names)
    else:
        have = set(explicit_seed)
        for name in work_tag_names:
            if name not in have:
                explicit_seed.append(name)
                have.add(name)

    if explicit_seed:
        seed = explicit_seed
    else:
        seed = [row.name for row in cache.iter_root_rows()]
    seed_set = set(seed)
    rows = _collect_rows(
        cache, seed, synonyms=synonyms, include_metatags=include_metatags
    )

    cached_seed = 0
    missing_seed = 0
    for name in seed_set:
        row = rows.get(name)
        if row is None or row.status == "missing":
            missing_seed += 1
        else:
            cached_seed += 1

    nodes: dict[str, TagGraphNode] = {}
    for row in rows.values():
        nodes[row.name] = TagGraphNode(
            id=row.name,
            name=row.name,
            status=row.status,
            category=row.category,
            in_seed=row.name in seed_set,
            canonical=row.canonical or row.name,
        )

    edges: list[TagGraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def add_edge(source: str, target: str, kind: str) -> None:
        if source == target:
            return
        if source not in nodes or target not in nodes:
            return
        key = (source, target, kind)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append(TagGraphEdge(source=source, target=target, kind=kind))

    for row in rows.values():
        if synonyms != "none" and row.status == "synonym" and row.canonical:
            add_edge(row.name, row.canonical, "synonym")
        if include_metatags:
            for meta in _metatags_of(row, rows):
                if meta not in nodes:
                    nodes[meta] = TagGraphNode(
                        id=meta,
                        name=meta,
                        status="missing",
                        category=None,
                        in_seed=meta in seed_set,
                        canonical=meta,
                    )
                origin = row.name if row.status != "synonym" else row.canonical
                add_edge(origin, meta, "metatag")

    for work in work_list:
        node_id = f"work:{work.work_id}"
        nodes[node_id] = TagGraphNode(
            id=node_id,
            name=work.title,
            status="work",
            category="Work",
            in_seed=True,
            canonical=work.title,
            kind="work",
            url=work.url,
        )
        for tag_name in work.tags:
            if not tag_name:
                continue
            if tag_name not in nodes:
                nodes[tag_name] = TagGraphNode(
                    id=tag_name,
                    name=tag_name,
                    status="missing",
                    category=None,
                    in_seed=True,
                    canonical=tag_name,
                )
            add_edge(node_id, tag_name, "work")

    edges = reduce_metatag_edges(edges)

    degree: dict[str, int] = defaultdict(int)
    for edge in edges:
        degree[edge.source] += 1
        degree[edge.target] += 1
    for node in nodes.values():
        node.degree = degree[node.id]

    weighted: list[TagGraphEdge] = []
    for edge in edges:
        work = nodes.get(edge.source)
        deg = work.degree if work is not None else 1
        weighted.append(
            TagGraphEdge(
                edge.source,
                edge.target,
                edge.kind,
                edge_weight(edge.kind, hub_degree=deg),
            )
        )
    edges = weighted

    _assign_ranks(nodes, edges)
    ordered_nodes = sorted(
        nodes.values(),
        key=lambda node: (not node.in_seed, node.name.casefold()),
    )
    components = _assign_components(ordered_nodes, edges)
    layout_tag_graph(ordered_nodes, edges)
    synonym_count = sum(1 for edge in edges if edge.kind == "synonym")
    metatag_count = sum(1 for edge in edges if edge.kind == "metatag")
    work_edge_count = sum(1 for edge in edges if edge.kind == "work")
    return TagGraph(
        nodes=ordered_nodes,
        edges=edges,
        components=components,
        seed_count=len(seed_set),
        cached_seed=cached_seed,
        missing_seed=missing_seed,
        synonym_edges=synonym_count,
        metatag_edges=metatag_count,
        work_count=len(work_list),
        work_edges=work_edge_count,
        hubs=graph_hubs(ordered_nodes, edges),
    )


def graph_payload(graph: TagGraph) -> dict[str, Any]:
    return graph.to_dict()


def empty_graph_payload() -> dict[str, Any]:
    return {
        "nodes": [],
        "edges": [],
        "components": [],
        "hubs": [],
        "seed_count": 0,
        "cached_seed": 0,
        "missing_seed": 0,
        "synonym_edges": 0,
        "metatag_edges": 0,
        "work_count": 0,
        "work_edges": 0,
    }


def load_tag_graph(
    *,
    jsonl_paths: Sequence[Path | str] = (),
    names_files: Sequence[Path | str] = (),
    tags: Sequence[str] = (),
    cache_path: Path,
    ttl_days: float,
    synonyms: SynonymMode = "seed",
    include_metatags: bool = True,
) -> TagGraph:
    """Build a graph from JSONL / names / the tag cache. Does not fetch AO3."""
    jsonl = [Path(path) for path in jsonl_paths]
    names = [Path(path) for path in names_files]
    seed = collect_warm_names(
        jsonl_paths=jsonl,
        names_files=names,
        names=list(tags),
    )
    records: list[dict[str, Any]] = []
    for path in jsonl:
        if path.is_file():
            records.extend(load_jsonl_records(path))
    works = works_from_records(records)
    if not works and not seed:
        return TagGraph(
            nodes=[],
            edges=[],
            components=[],
            seed_count=0,
            cached_seed=0,
            missing_seed=0,
            synonym_edges=0,
            metatag_edges=0,
        )
    cache = TagCache.load(cache_path, ttl_days=ttl_days)
    try:
        return build_tag_graph(
            cache,
            seed or None,
            include_metatags=include_metatags,
            synonyms=synonyms,
            works=works,
        )
    finally:
        cache.close()


def render_dot(graph: TagGraph) -> str:
    lines = [
        "digraph tags {",
        "  rankdir=TB;",
        '  node [shape=box, fontname="Helvetica"];',
    ]
    for node in graph.nodes:
        fill = {
            "canonical": "#dbeafe",
            "synonym": "#fef9c3",
            "unmarked": "#e5e7eb",
            "missing": "#f3f4f6",
            "work": "#ddd6fe",
        }.get(node.status, "#ffffff")
        gid = node.id.replace("\\", "\\\\").replace('"', '\\"')
        label = node.name.replace("\\", "\\\\").replace('"', '\\"')
        shape = "ellipse" if node.kind == "work" else "box"
        lines.append(
            f'  "{gid}" [label="{label}", fillcolor="{fill}", style=filled, '
            f"shape={shape}, tooltip=\"{node.status}\"];"
        )
    for edge in graph.edges:
        src = edge.source.replace("\\", "\\\\").replace('"', '\\"')
        dst = edge.target.replace("\\", "\\\\").replace('"', '\\"')
        style = "dashed" if edge.kind == "synonym" else "solid"
        lines.append(f'  "{src}" -> "{dst}" [style={style}, label="{edge.kind}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _json_for_script(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_viewer_html(
    *,
    payload: dict[str, Any] | None = None,
    data_url: str | None = None,
) -> str:
    """Viewer HTML. Inline ``payload`` for a standalone file, or ``data_url`` to fetch JSON."""
    data = _json_for_script(payload) if payload is not None else "null"
    url = json.dumps(data_url) if data_url else "null"
    return _HTML_TEMPLATE.replace("%%DATA%%", data).replace("%%DATA_URL%%", url)


def render_html(graph: TagGraph) -> str:
    return render_viewer_html(payload=graph_payload(graph))


def write_graph(
    graph: TagGraph,
    path: Path | None,
    fmt: GraphFormat,
) -> str:
    """Serialize ``graph``. Returns the text body (also written when path set)."""
    if fmt == "json":
        body = json.dumps(graph_payload(graph), ensure_ascii=False, indent=2) + "\n"
    elif fmt == "dot":
        body = render_dot(graph)
    else:
        body = render_html(graph)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return body


def infer_format(path: Path | None, explicit: str | None) -> GraphFormat:
    if explicit in {"html", "json", "dot"}:
        return explicit  # type: ignore[return-value]
    if path is not None:
        suffix = path.suffix.lower()
        if suffix in {".json"}:
            return "json"
        if suffix in {".dot", ".gv"}:
            return "dot"
        if suffix in {".html", ".htm"}:
            return "html"
    return "html"


def format_graph_summary(graph: TagGraph, path: Path | None) -> str:
    lines = []
    if path is not None:
        lines.append(f"Wrote {path}")
    lines.append(
        f"{len(graph.nodes)} nodes, {len(graph.edges)} edges "
        f"({graph.work_count} works, {graph.synonym_edges} synonym, "
        f"{graph.metatag_edges} metatag)"
    )
    if graph.seed_count:
        lines.append(
            f"{graph.cached_seed} of {graph.seed_count} seed tags are in the cache; "
            f"{graph.missing_seed} uncached"
        )
    linked = sum(1 for node in graph.nodes if node.degree > 0)
    lines.append(
        f"{linked} nodes have a link; {graph.work_edges} work–tag edges"
    )
    if graph.hubs:
        names = ", ".join(str(hub["name"]) for hub in graph.hubs[:4])
        lines.append(f"{len(graph.hubs)} bridge tags (on 2+ works): {names}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        return serve_main(argv[1:])
    if argv and argv[0] == "reload":
        return reload_main(argv[1:])
    parser = argparse.ArgumentParser(
        prog="ao3kit tags graph",
        description=(
            "Build a graph of works and AO3 tag relationships "
            "(work → tag, synonym → canonical, tag → metatag) from the "
            "local tag cache. Does not fetch AO3."
        ),
    )
    parser.add_argument(
        "tags",
        nargs="*",
        help="Tag names to include (optional if --jsonl / --names-file used)",
    )
    parser.add_argument(
        "--jsonl",
        action="append",
        default=[],
        help="Scrape/library JSONL: each work becomes a node linked to its tags",
    )
    parser.add_argument(
        "--names-file",
        action="append",
        default=[],
        help="Text file of tag names, one per line (Calibre library dump)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output path (default: XDG cache dir / fanfic-organizer / tag-graph.html, or stdout for json/dot)",
    )
    parser.add_argument(
        "--format",
        choices=("html", "json", "dot"),
        help="Output format (default: from -o suffix, else html)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the HTML file in the default browser",
    )
    parser.add_argument(
        "--synonyms",
        choices=("seed", "all", "none"),
        default="seed",
        help=(
            "Which synonyms to draw: those in the seed set (default), every "
            "cached synonym of seed canonicals, or none"
        ),
    )
    parser.add_argument(
        "--no-metatags",
        action="store_true",
        help="Do not include metatag (parent) edges",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Tag cache SQLite path (default: XDG cache dir / fanfic-organizer)",
    )
    parser.add_argument(
        "--cache-ttl-days",
        type=float,
        default=None,
        help=(
            "Expire trees older than this many days before reading "
            f"(default: config or {DEFAULT_TAG_CACHE_TTL_DAYS}; 0 = never)"
        ),
    )
    args = parser.parse_args(argv)

    from ao3kit.config import load_user_config

    user_cfg = load_user_config(ensure=True)
    ttl = (
        float(args.cache_ttl_days)
        if args.cache_ttl_days is not None
        else float(user_cfg.settings.tag_cache_ttl_days)
    )
    cache_path = Path(args.cache) if args.cache else default_tag_cache_path()
    if not cache_path.is_file():
        print(f"Tag cache not found: {cache_path}", file=sys.stderr)
        print(
            "Warm it first: python -m ao3kit tags warm start --names-file tags.txt",
            file=sys.stderr,
        )
        return 1

    graph = load_tag_graph(
        jsonl_paths=[Path(p) for p in args.jsonl],
        names_files=[Path(p) for p in args.names_file],
        tags=list(args.tags),
        cache_path=cache_path,
        ttl_days=ttl,
        synonyms=args.synonyms,
        include_metatags=not args.no_metatags,
    )

    out_path = Path(args.output) if args.output else None
    fmt = infer_format(out_path, args.format)
    if out_path is None and fmt == "html":
        out_path = default_graph_html()
    body = write_graph(graph, out_path, fmt)
    summary = format_graph_summary(graph, out_path)
    if out_path is None:
        sys.stdout.write(body)
        print(summary, file=sys.stderr)
    else:
        print(summary)
    if args.open:
        target = out_path
        if target is None:
            print("Cannot --open without writing an HTML file", file=sys.stderr)
            return 2
        if fmt != "html":
            print("warning: --open is meant for HTML output", file=sys.stderr)
        webbrowser.open(target.resolve().as_uri())
    return 0


def graph_sources_fingerprint(
    jsonl_paths: Sequence[Path],
    cache_path: Path,
) -> tuple[tuple[str, int, int], ...]:
    parts: list[tuple[str, int, int]] = []
    for path in jsonl_paths:
        if path.is_file():
            stat = path.stat()
            parts.append((str(path), int(stat.st_mtime_ns), int(stat.st_size)))
        else:
            parts.append((str(path), 0, 0))
    if cache_path.is_file():
        stat = cache_path.stat()
        parts.append((str(cache_path), int(stat.st_mtime_ns), int(stat.st_size)))
    else:
        parts.append((str(cache_path), 0, 0))
    return tuple(parts)


def read_serve_stamp(path: Path | None = None) -> dict[str, Any] | None:
    stamp_path = path or default_graph_serve_stamp()
    if not stamp_path.is_file():
        return None
    try:
        data = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
        except OSError:
            return None
    return data


def write_serve_stamp(
    *,
    port: int,
    jsonl_paths: Sequence[Path],
    path: Path | None = None,
) -> Path:
    stamp_path = path or default_graph_serve_stamp()
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "port": port,
        "url": f"http://127.0.0.1:{port}/",
        "jsonl": [str(item) for item in jsonl_paths],
    }
    stamp_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return stamp_path


def clear_serve_stamp(path: Path | None = None) -> None:
    stamp_path = path or default_graph_serve_stamp()
    try:
        data = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if data.get("pid") not in (None, os.getpid()):
        return
    try:
        stamp_path.unlink()
    except OSError:
        pass


def parse_similar_work_ids(
    *,
    query: str = "",
    body: dict[str, Any] | None = None,
) -> list[str]:
    """Work ids from ``/similar?work_id=`` or a POST body."""
    from urllib.parse import parse_qs

    ids: list[str] = []
    qs = parse_qs(query, keep_blank_values=False)
    for key in ("work_id", "work_ids"):
        for raw in qs.get(key, []):
            ids.extend(
                part.strip() for part in str(raw).split(",") if part.strip()
            )
    if body:
        raw_ids = body.get("work_ids")
        if raw_ids is None:
            raw_ids = body.get("work_id")
        if isinstance(raw_ids, (str, int)):
            raw_ids = [raw_ids]
        for item in raw_ids or []:
            text = str(item).strip()
            if text:
                ids.append(text)
    seen: set[str] = set()
    out: list[str] = []
    for item in ids:
        if item.startswith("work:"):
            item = item[5:]
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_similar_tag(
    *,
    query: str = "",
    body: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Tag name and AO3 category from ``/similar?tag=`` or a POST body."""
    from urllib.parse import parse_qs

    name = ""
    category = ""
    qs = parse_qs(query, keep_blank_values=False)
    if qs.get("tag"):
        name = str(qs["tag"][0]).strip()
    if qs.get("category"):
        category = str(qs["category"][0]).strip()
    if body:
        if not name:
            name = str(body.get("tag") or "").strip()
        if not category:
            category = str(body.get("category") or "").strip()
    return name, category


def handle_similar_http(
    *,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    jsonl_paths: Sequence[Path | str],
    inbox: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """GET preview or POST queue for Find similar. Reloaded on each request."""
    from ao3kit.tags.graph_bridge import (
        facet_bucket_for,
        queue_similar_command,
        record_for_tag,
        records_for_tag_name,
        records_for_work_ids,
        select_for_tag,
        similar_preview,
        similar_scrape_options,
        similar_search_is_usable,
    )

    query = path.split("?", 1)[1] if "?" in path else ""
    work_ids = parse_similar_work_ids(query=query, body=body)
    tag, category = parse_similar_tag(query=query, body=body)
    seed: dict[str, Any]
    titles: list[str] | None = None
    selected = None
    if work_ids:
        records = records_for_work_ids(jsonl_paths, work_ids)
        if not records:
            return 404, {"ok": False, "error": "work not in graph dump"}
        seed = {"kind": "work", "work_ids": work_ids}
    elif tag:
        neighbors = records_for_tag_name(jsonl_paths, tag)
        seed_record = record_for_tag(tag, category)
        records = ([seed_record] if seed_record else []) + neighbors
        selected = select_for_tag(tag, category)
        seed = {
            "kind": "tag",
            "name": tag,
            "category": category,
            "bucket": facet_bucket_for(tag, category),
        }
        titles = [tag]
    else:
        return 400, {"ok": False, "error": "work_id or tag is required"}
    if method.upper() == "GET":
        payload = similar_preview(records, selected=selected)
        payload["ok"] = True
        payload["seed"] = seed
        if seed.get("kind") == "tag":
            payload["hint"] = (
                "Search AO3 for this tag. Check a fandom or ship only "
                "if you want AO3 to AND them."
            )
        return 200, payload

    data = body or {}
    select = data.get("select")
    if select is not None and not isinstance(select, dict):
        return 400, {"ok": False, "error": "select must be an object"}
    if select is None and selected is not None:
        select = selected.to_dict()
    scrape_kwargs = {
        "include": data.get("include"),
        "select": select,
        "max_results": str(data.get("max_results") or "25"),
        "sort_column": str(data.get("sort_column") or "kudos_count"),
        "complete": data.get("complete"),
        "language_id": str(data.get("language_id") or "en"),
        "min_score": str(data.get("min_score") or ""),
        "min_kudos": str(data.get("min_kudos") or ""),
        "min_words": str(data.get("min_words") or ""),
        "complete_only": bool(data.get("complete_only")),
    }
    if not similar_search_is_usable(similar_scrape_options(records, **scrape_kwargs)):
        return 400, {
            "ok": False,
            "error": (
                "Add a fandom, author, tag, or query so AO3 has something to search."
            ),
        }
    queued = queue_similar_command(
        work_ids=work_ids,
        records=records,
        inbox=inbox,
        tag=tag or None,
        titles=titles,
        **scrape_kwargs,
    )
    return 200, {
        "ok": True,
        "queued": True,
        "id": queued.get("id"),
        "titles": queued.get("titles") or [],
        "fandoms": queued.get("fandoms") or [],
        "select": queued.get("select"),
        "tag": queued.get("tag"),
        "message": (
            "Queued for Calibre. Keep the plugin open — "
            "the graph updates as works are imported."
        ),
    }


def notify_running_server(
    *,
    stamp_path: Path | None = None,
    timeout: float = 60.0,
) -> str | None:
    """Ask a running ``tags graph serve`` to rebuild. Returns the viewer URL."""
    stamp = read_serve_stamp(stamp_path)
    if not stamp:
        return None
    url = str(stamp.get("url") or "").rstrip("/")
    if not url:
        return None
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url + "/rebuild", data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    return url + "/"


def reload_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ao3kit tags graph reload")
    parser.add_argument("--stamp", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(list(argv or []))
    url = notify_running_server(stamp_path=args.stamp, timeout=args.timeout)
    if not url:
        print("No tag graph server is running.", file=sys.stderr)
        print("Start one: python -m ao3kit tags graph serve", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "url": url}))
    return 0


def serve_main(argv: list[str] | None = None) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    parser = argparse.ArgumentParser(
        prog="ao3kit tags graph serve",
        description=(
            "Serve the tag graph viewer over HTTP. Refresh the page after "
            "editing the viewer; Reload data after Calibre dumps JSONL."
        ),
    )
    parser.add_argument(
        "--jsonl",
        action="append",
        default=[],
        help="Work JSONL (default: XDG cache dir / fanfic-organizer / tag_graph_works.jsonl)",
    )
    parser.add_argument("--names-file", action="append", default=[])
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AO3KIT_GRAPH_PORT") or DEFAULT_GRAPH_PORT),
    )
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--cache-ttl-days", type=float, default=None)
    parser.add_argument("--synonyms", choices=("seed", "all", "none"), default="seed")
    parser.add_argument("--no-metatags", action="store_true")
    parser.add_argument(
        "--stamp",
        type=Path,
        default=None,
        help="PID/URL stamp so Calibre Tag graph can refresh this viewer",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        default=True,
        help="Open the viewer in a browser (default)",
    )
    parser.add_argument(
        "--no-open",
        action="store_false",
        dest="open",
        help="Do not open a browser",
    )
    args = parser.parse_args(list(argv or []))

    from ao3kit.config import load_user_config

    user_cfg = load_user_config(ensure=True)
    ttl = (
        float(args.cache_ttl_days)
        if args.cache_ttl_days is not None
        else float(user_cfg.settings.tag_cache_ttl_days)
    )
    jsonl_paths = [Path(item) for item in args.jsonl] or [default_graph_jsonl()]
    names_files = [Path(item) for item in args.names_file]
    cache_path = Path(args.cache) if args.cache else default_tag_cache_path()
    existing = read_serve_stamp(args.stamp)
    if existing:
        url = str(existing.get("url") or "")
        print(
            f"Already serving at {url} (pid {existing.get('pid')})",
            file=sys.stderr,
        )
        if args.open and url:
            webbrowser.open(url)
        return 0

    state: dict[str, Any] = {"payload": None, "fingerprint": None}
    lock = threading.Lock()

    def fingerprint() -> tuple[tuple[str, int, int], ...]:
        return graph_sources_fingerprint(jsonl_paths, cache_path)

    def build_payload(*, force: bool = False) -> dict[str, Any]:
        with lock:
            mark = fingerprint()
            if not force and state["payload"] is not None and state["fingerprint"] == mark:
                return state["payload"]
            if not cache_path.is_file():
                payload = empty_graph_payload()
            else:
                import importlib

                import ao3kit.tags.graph as live

                live = importlib.reload(live)
                graph = live.load_tag_graph(
                    jsonl_paths=jsonl_paths,
                    names_files=names_files,
                    cache_path=cache_path,
                    ttl_days=ttl,
                    synonyms=args.synonyms,
                    include_metatags=not args.no_metatags,
                )
                payload = live.graph_payload(graph)
            state["payload"] = payload
            state["fingerprint"] = mark
            out_json = default_graph_json()
            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            return payload

    def live_graph_mod():
        import importlib
        import ao3kit.tags.graph_bridge as bridge
        import ao3kit.tags.graph as live

        importlib.reload(bridge)
        return importlib.reload(live)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *log_args: object) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % log_args))

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/", "/index.html"}:
                import importlib

                with lock:
                    import ao3kit.tags.graph as live

                    live = importlib.reload(live)
                    html = live.render_viewer_html(data_url="/graph.json")
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/graph.json":
                payload = build_payload()
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
                return
            if path == "/status":
                payload = build_payload()
                from ao3kit.tags.graph_bridge import pending_inbox_count

                status = {
                    "url": f"http://127.0.0.1:{args.port}/",
                    "jsonl": [str(item) for item in jsonl_paths],
                    "works": payload.get("work_count", 0),
                    "nodes": len(payload.get("nodes") or []),
                    "pending_commands": pending_inbox_count(),
                    "live": True,
                }
                body = json.dumps(status).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
                return
            if path == "/similar":
                live = live_graph_mod()
                code, payload = live.handle_similar_http(
                    method="GET",
                    path=self.path,
                    body=None,
                    jsonl_paths=jsonl_paths,
                )
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send(code, body, "application/json; charset=utf-8")
                return
            self._send(404, b'{"error":"not found"}\n', "application/json")

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/similar":
                body = self._read_json_body()
                live = live_graph_mod()
                code, payload = live.handle_similar_http(
                    method="POST",
                    path=self.path,
                    body=body,
                    jsonl_paths=jsonl_paths,
                )
                self._send(
                    code,
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            if path != "/rebuild":
                self._send(404, b'{"error":"not found"}\n', "application/json")
                return
            payload = build_payload(force=True)
            body = json.dumps(
                {
                    "ok": True,
                    "works": payload.get("work_count", 0),
                    "nodes": len(payload.get("nodes") or []),
                }
            ).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = httpd.server_address[1]
    stamp = write_serve_stamp(port=port, jsonl_paths=jsonl_paths, path=args.stamp)
    atexit.register(clear_serve_stamp, args.stamp)
    url = f"http://127.0.0.1:{port}/"
    missing = [str(path) for path in jsonl_paths if not path.is_file()]
    print(f"Tag graph viewer {url}", file=sys.stderr)
    print(f"JSONL: {', '.join(str(path) for path in jsonl_paths)}", file=sys.stderr)
    if missing:
        print(
            "No dump yet — in Calibre use Tag graph… to write "
            f"{default_graph_jsonl().name}, then Reload data.",
            file=sys.stderr,
        )
    print("Refresh the page after viewer edits. POST /rebuild after a Calibre dump.", file=sys.stderr)
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        httpd.server_close()
        clear_serve_stamp(stamp)
    return 0


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AO3 tag graph</title>
<style>
:root {
  --bg: #1a1a1a; --panel: #222; --text: #e8e8e8; --muted: #8a8a8a;
  --line: #333; --accent: #a78bfa; --card: #2a2a2a;
  --work: #a78bfa; --fandom: #60a5fa; --character: #34d399;
  --relationship: #f472b6; --freeform: #fbbf24; --missing: #6b7280;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; height: 100%; background: var(--bg); color: var(--text);
  font: 13px/1.4 ui-sans-serif, system-ui, -apple-system, sans-serif;
  display: flex; flex-direction: column;
}
header {
  flex: 0 0 auto; padding: 10px 16px; border-bottom: 1px solid var(--line);
  display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: baseline;
}
h1 { font-size: 15px; font-weight: 600; margin: 0; }
.stats { color: var(--muted); display: flex; flex-wrap: wrap; gap: 12px; }
.stats strong { color: var(--text); font-weight: 600; }
.toolbar {
  flex: 0 0 auto; display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center;
  padding: 8px 16px; border-bottom: 1px solid var(--line); background: var(--panel);
  position: relative; z-index: 8;
}
.toolbar input[type="search"], .toolbar select {
  background: var(--bg); color: var(--text); border: 1px solid var(--line);
  padding: 6px 8px; min-width: 180px;
}
.toolbar input[type="number"] {
  background: var(--bg); color: var(--text); border: 1px solid var(--line);
  padding: 6px 4px; min-width: 3.2em; width: 3.6em;
}
#cluster-mode { min-width: 190px; }
.toolbar button.tool {
  background: var(--bg); color: var(--text); border: 1px solid var(--line);
  padding: 6px 10px; cursor: pointer;
}
.toolbar button.tool:hover, #detail button.tool:hover { background: var(--card); }
#detail button.tool {
  background: var(--bg); color: var(--text); border: 1px solid var(--line);
  padding: 6px 10px; cursor: pointer; margin-top: 4px;
}
.legend { display: flex; flex-wrap: wrap; gap: 8px 12px; color: var(--muted); margin-left: auto; }
.swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block;
  margin-right: 4px; vertical-align: -1px; }
#suggest {
  position: absolute; left: 16px; top: 100%; z-index: 9; width: 360px; max-height: 280px;
  overflow: auto; background: var(--panel); border: 1px solid var(--line); display: none;
}
#suggest button {
  display: block; width: 100%; text-align: left; background: transparent; border: 0;
  color: var(--text); padding: 7px 10px; cursor: pointer; border-bottom: 1px solid var(--line);
}
#suggest button:hover { background: var(--card); }
#suggest .sub { color: var(--muted); font-size: 11px; }
main {
  flex: 1 1 auto; min-height: 0;
  display: grid; grid-template-columns: 1fr 300px;
}
#canvas-wrap { overflow: hidden; position: relative; cursor: grab; background: var(--bg); }
#canvas-wrap.drag { cursor: grabbing; }
canvas { width: 100%; height: 100%; display: block; }
#hint {
  position: absolute; left: 12px; bottom: 12px; color: var(--muted); font-size: 11px;
  pointer-events: none; max-width: 70%;
}
#zoom {
  position: absolute; right: 12px; top: 12px; display: flex; flex-direction: column; gap: 4px;
}
#zoom button {
  width: 28px; height: 28px; background: var(--panel); color: var(--text);
  border: 1px solid var(--line); cursor: pointer; border-radius: 6px;
}
#sidebar {
  border-left: 1px solid var(--line); background: var(--panel);
  overflow: auto; padding: 12px 14px; min-height: 0;
}
#sidebar h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); font-weight: 600; margin: 18px 0 8px; }
#sidebar h2:first-child { margin-top: 0; }
#detail .name { font-size: 15px; font-weight: 600; }
#detail .meta { color: var(--muted); margin: 4px 0 8px; }
#detail a { color: var(--accent); text-decoration: none; }
.comp {
  display: block; width: 100%; text-align: left; background: transparent;
  border: 0; color: var(--text); padding: 7px 6px; cursor: pointer;
  border-bottom: 1px solid var(--line);
}
.comp:hover { background: var(--card); }
.comp .k { color: var(--muted); font-size: 11px; }
#cf { min-width: 0; width: 100%; margin-bottom: 8px;
  background: var(--bg); color: var(--text); border: 1px solid var(--line); padding: 6px 8px; }
.similar-panel { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--line); }
.similar-panel h3 {
  font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); font-weight: 600; margin: 10px 0 4px;
}
.similar-panel h3:first-child { margin-top: 0; }
.similar-shortcuts { display: flex; gap: 8px; margin: 0 0 8px; }
.similar-shortcuts button {
  background: transparent; color: var(--accent); border: 0; padding: 0;
  cursor: pointer; font-size: 11px;
}
.facet-list {
  max-height: 7.2em; overflow: auto; margin: 0 0 4px;
  border: 1px solid var(--line); padding: 4px 6px; background: var(--bg);
}
.facet-list label {
  display: flex; gap: 6px; align-items: flex-start; padding: 2px 0; cursor: pointer;
}
.facet-list .n { flex: 1; min-width: 0; word-break: break-word; }
.facet-list .k { color: var(--muted); font-size: 11px; flex: 0 0 auto; }
.similar-and { color: var(--muted); font-size: 11px; margin: 8px 0; }
.similar-panel .row {
  display: block; margin: 8px 0 2px; color: var(--muted); font-size: 11px;
}
.similar-panel input[type=text],
.similar-panel input[type=number],
.similar-panel input[type=search],
.similar-panel select {
  width: 100%; background: var(--bg); color: var(--text);
  border: 1px solid var(--line); padding: 5px 6px;
}
.similar-panel details { margin: 8px 0; color: var(--muted); }
.similar-panel details summary { cursor: pointer; }
.similar-panel .tool { width: 100%; margin-top: 8px; }
.similar-panel .tool:disabled { opacity: .6; cursor: default; }
</style>
</head>
<body>
<header>
  <h1>AO3 tag graph</h1>
  <div class="stats" id="stats"></div>
</header>
<div class="toolbar">
  <input id="q" type="search" placeholder="Search a work or tag…" autocomplete="off"/>
  <select id="cat"><option value="">All categories</option></select>
  <label><input type="checkbox" id="show-works" checked/> Works</label>
  <label><input type="checkbox" id="show-syn" checked/> Synonyms</label>
  <label><input type="checkbox" id="show-meta" checked/> Metatags</label>
  <label><input type="checkbox" id="bridges-only"/> Bridges only</label>
  <label title="Hover only names the node under the cursor; click still sets focus"><input type="checkbox" id="pin-mode"/> Pin mode</label>
  <label>Cluster
    <select id="cluster-mode" title="How to group works">
      <option value="fandom" selected>Specific fandom</option>
      <option value="fandom-large">Largest fandom</option>
      <option value="crossover">Crossovers (mixed franchises)</option>
      <option value="ship">Relationship</option>
      <option value="one">One group</option>
    </select>
  </label>
  <label title="Gently pull the focused neighborhood together (short, capped)"><input type="checkbox" id="settle-on" checked/> Settle focus</label>
  <label title="How many links away from the focused node to name">Hops <input id="label-hops" type="number" min="0" max="6" value="1"/></label>
  <label title="0 = off. Name every node with at least this many links">Degree ≥ <input id="label-degree" type="number" min="0" max="999" value="0"/></label>
  <button type="button" class="tool" id="reload-data" hidden title="Rebuild from the Calibre JSONL dump">Reload data</button>
  <div class="legend">
    <span><i class="swatch" style="background:var(--work)"></i>Work</span>
    <span><i class="swatch" style="background:var(--fandom)"></i>Fandom</span>
    <span><i class="swatch" style="background:var(--character)"></i>Character</span>
    <span><i class="swatch" style="background:var(--relationship)"></i>Relationship</span>
    <span><i class="swatch" style="background:var(--freeform)"></i>Additional</span>
    <span><i class="swatch" style="background:var(--missing)"></i>Uncached</span>
  </div>
  <div id="suggest"></div>
</div>
<main>
  <div id="canvas-wrap">
    <canvas id="g"></canvas>
    <div id="hint">Click a tag for its works and shared ships. Click empty space or Esc to clear. Hover names a node without moving focus.</div>
    <div id="zoom">
      <button type="button" id="z-in" title="Zoom in">+</button>
      <button type="button" id="z-out" title="Zoom out">−</button>
      <button type="button" id="z-fit" title="Fit">⤢</button>
    </div>
  </div>
  <aside id="sidebar">
    <h2>Selected</h2>
    <div id="detail">Click a work, tag, bridge, or cluster.</div>
    <h2>Bridges</h2>
    <div id="hubs"></div>
    <h2>Clusters</h2>
    <div id="clusters"></div>
    <h2 id="list-title">Works</h2>
    <input id="cf" type="search" placeholder="Filter works…"/>
    <div id="comps"></div>
  </aside>
</main>
<script>
const EMBEDDED = %%DATA%%;
const DATA_URL = %%DATA_URL%%;
function startGraph(DATA) {
if (!DATA || !DATA.nodes) DATA = {nodes: [], edges: [], hubs: []};
const wrap = document.getElementById("canvas-wrap");
const canvas = document.getElementById("g");
const ctx = canvas.getContext("2d");
const catSel = document.getElementById("cat");
const nodesById = Object.fromEntries(DATA.nodes.map(n => [n.id, n]));
const adj = {};
const workCountByTag = {};
const workTags = {};
const tagWorks = {};
for (const n of DATA.nodes) adj[n.id] = [];
for (const e of DATA.edges) {
  (adj[e.source] || (adj[e.source] = [])).push(e.target);
  (adj[e.target] || (adj[e.target] = [])).push(e.source);
  if (e.kind === "work") {
    workCountByTag[e.target] = (workCountByTag[e.target] || 0) + 1;
    (workTags[e.source] = workTags[e.source] || []).push(e.target);
    (tagWorks[e.target] = tagWorks[e.target] || []).push(e.source);
  }
}
const cats = [...new Set(DATA.nodes.map(n => n.category).filter(Boolean))].sort();
for (const c of cats) {
  const o = document.createElement("option"); o.value = c; o.textContent = c; catSel.appendChild(o);
}
const allWorks = DATA.nodes.filter(n => n.kind === "work")
  .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
const tagCount = DATA.nodes.length - allWorks.length;
const hubIds = new Set((DATA.hubs || []).map(h => h.id));

function fandomStem(name) {
  let text = String(name || "").trim().toLowerCase();
  if (text.endsWith(" & related fandoms")) {
    text = text.slice(0, -" & related fandoms".length).trim();
  }
  let cut = text.length;
  const paren = text.indexOf(" (");
  const dash = text.indexOf(" - ");
  if (paren > 0) cut = Math.min(cut, paren);
  if (dash > 0) cut = Math.min(cut, dash);
  return text.slice(0, cut).trim();
}
function buildFandomFamilies() {
  const ids = DATA.nodes.filter(n => n.kind !== "work" && n.category === "Fandom").map(n => n.id);
  const parent = {};
  function add(id) { if (parent[id] == null) parent[id] = id; }
  function find(id) {
    add(id);
    if (parent[id] !== id) parent[id] = find(parent[id]);
    return parent[id];
  }
  function union(a, b) {
    a = find(a); b = find(b);
    if (a !== b) parent[b] = a;
  }
  const byStem = {};
  for (const id of ids) {
    add(id);
    const stem = fandomStem((nodesById[id] || {}).name || id);
    if (!stem) continue;
    (byStem[stem] = byStem[stem] || []).push(id);
  }
  for (const group of Object.values(byStem)) {
    for (let i = 1; i < group.length; i++) union(group[0], group[i]);
  }
  for (const e of DATA.edges) {
    if (e.kind !== "metatag") continue;
    const a = nodesById[e.source], b = nodesById[e.target];
    if (!a || !b || a.category !== "Fandom" || b.category !== "Fandom") continue;
    union(a.id, b.id);
  }
  const out = {};
  for (const id of ids) out[id] = find(id);
  return out;
}
const fandomFamily = buildFandomFamilies();
document.getElementById("stats").innerHTML =
  "<span><strong>" + allWorks.length + "</strong> works</span>" +
  "<span><strong>" + tagCount + "</strong> tags</span>" +
  "<span><strong>" + (DATA.hubs || []).length + "</strong> bridges</span>" +
  "<span><strong>" + (DATA.missing_seed || 0) + "</strong> uncached</span>";

function colorFor(n) {
  if (n.kind === "work" || n.status === "work") return "#a78bfa";
  if (n.status === "missing") return "#6b7280";
  return ({
    Fandom: "#60a5fa", Character: "#34d399", Relationship: "#f472b6",
    "Additional Tags": "#fbbf24", Work: "#a78bfa"
  }[n.category] || "#fbbf24");
}
function radiusOf(n) {
  const d = Math.log(1 + (n.degree || 0));
  const bridge = workCountByTag[n.id] || 0;
  if (n.kind === "work") return 10 + d * 0.7;
  return (bridge >= 2 ? 5.5 : 3.4) + d * 0.5;
}
function kinds() {
  const set = new Set();
  if (document.getElementById("show-syn").checked) set.add("synonym");
  if (document.getElementById("show-meta").checked) set.add("metatag");
  if (document.getElementById("show-works").checked) set.add("work");
  return set;
}

let vis = { ids: new Set(), edges: [] };
let sim = [];
let simIndex = {};
let selected = null;
let hover = null;
let similarState = { key: null, el: null };
let view = { x: 0, y: 0, k: 1 };
let panning = false;
let lastPtr = null;
let lastHoverId = null;
const MIN_ZOOM = 0.02;
const MAX_ZOOM = 6;

function rebuildVis() {
  const cat = catSel.value;
  const k = kinds();
  const bridgesOnly = document.getElementById("bridges-only").checked;
  const ids = new Set();
  for (const n of DATA.nodes) {
    if (n.kind === "work") { if (k.has("work")) ids.add(n.id); continue; }
    if (n.status === "synonym" && !k.has("synonym")) continue;
    if (cat && n.category !== cat) continue;
    if (bridgesOnly && (workCountByTag[n.id] || 0) < 2 && n.category !== "Fandom") continue;
    ids.add(n.id);
  }
  const edges = [];
  for (const e of DATA.edges) {
    if (k.has(e.kind) && ids.has(e.source) && ids.has(e.target)) edges.push(e);
  }
  vis = { ids, edges };
}

function layoutNodes() {
  const selId = selected && selected.id;
  sim = [];
  simIndex = {};
  for (const n of DATA.nodes) {
    if (!vis.ids.has(n.id)) continue;
    const node = {
      id: n.id, name: n.name, kind: n.kind, status: n.status, category: n.category,
      in_seed: n.in_seed, degree: n.degree, url: n.url, cluster: n.cluster || "",
      x: n.x || 0, y: n.y || 0,
      r: radiusOf(n), color: colorFor(n),
      works: workCountByTag[n.id] || 0
    };
    simIndex[n.id] = sim.length;
    sim.push(node);
  }
  selected = selId && simIndex[selId] != null ? sim[simIndex[selId]] : null;
}

function sunflower(count, spacing) {
  if (count <= 0) return [];
  if (count === 1) return [[0, 0]];
  const g = Math.PI * (3 - Math.sqrt(5));
  const pts = [];
  for (let i = 0; i < count; i++) {
    const r = spacing * Math.sqrt(i + 0.5);
    const a = i * g;
    pts.push([r * Math.cos(a), r * Math.sin(a)]);
  }
  return pts;
}

function clusterMode() {
  return (document.getElementById("cluster-mode") || {}).value || "fandom";
}
function settleOn() {
  return !!(document.getElementById("settle-on") || {}).checked;
}
function clusterTitle(key) {
  if (key === "_crossover") return "Crossovers";
  if (key === "_all") return "All works";
  if (key === "_other") return "Other";
  return key;
}

function familyClusterName(fandoms) {
  const root = fandomFamily[fandoms[0]] || fandoms[0];
  const pool = fandoms.filter(id => (fandomFamily[id] || id) === root);
  const use = pool.length ? pool : fandoms;
  const countOf = id => workCountByTag[id] || 0;
  const lenOf = id => ((nodesById[id] || {}).name || id).length;
  return use.slice().sort((a, b) =>
    countOf(b) - countOf(a) || lenOf(a) - lenOf(b) || a.localeCompare(b)
  )[0];
}

function jsClusterKey(work) {
  const mode = clusterMode();
  const tags = workTags[work.id] || [];
  const fandoms = tags.filter(id => (nodesById[id] || {}).category === "Fandom");
  const ships = tags.filter(id => (nodesById[id] || {}).category === "Relationship");
  if (mode === "one") return "_all";
  if (mode === "crossover") {
    const roots = new Set(fandoms.map(id => fandomFamily[id] || id));
    if (roots.size >= 2) return "_crossover";
    if (fandoms.length) return familyClusterName(fandoms);
  }
  let pool;
  if (mode === "ship") pool = ships.length ? ships : (fandoms.length ? fandoms : tags);
  else pool = fandoms.length ? fandoms : tags;
  if (!pool.length) return "_other";
  const countOf = id => workCountByTag[id] || 0;
  if (mode === "fandom-large") {
    return pool.slice().sort((a, b) => countOf(b) - countOf(a) || a.localeCompare(b))[0];
  }
  return pool.slice().sort((a, b) => countOf(a) - countOf(b) || a.localeCompare(b))[0];
}

function packCluster(works, localTags) {
  const workAt = {};
  const pts = sunflower(works.length, 38);
  works.forEach((work, i) => {
    work.x = pts[i][0]; work.y = pts[i][1];
    workAt[work.id] = work;
  });
  const exclusive = {};
  for (const tag of localTags) {
    const hosts = (tagWorks[tag.id] || []).filter(id => workAt[id]);
    if (hosts.length >= 2) {
      tag.x = hosts.reduce((s, id) => s + workAt[id].x, 0) / hosts.length;
      tag.y = hosts.reduce((s, id) => s + workAt[id].y, 0) / hosts.length;
    } else if (hosts.length === 1) {
      (exclusive[hosts[0]] = exclusive[hosts[0]] || []).push(tag);
    } else { tag.x = 0; tag.y = 0; }
  }
  for (const workId of Object.keys(exclusive)) {
    const host = workAt[workId];
    const group = exclusive[workId];
    const tpts = sunflower(group.length, 12);
    group.forEach((tag, i) => {
      const x = tpts[i][0], y = tpts[i][1];
      const dist = Math.hypot(x, y);
      if (dist < 1e-6) { tag.x = host.x + 22; tag.y = host.y; }
      else {
        const scale = (dist + 20) / dist;
        tag.x = host.x + x * scale; tag.y = host.y + y * scale;
      }
    });
  }
  let radius = 40;
  for (const n of works.concat(localTags)) {
    radius = Math.max(radius, Math.hypot(n.x, n.y) + n.r + 12);
  }
  return radius;
}

function applyClusterLayout() {
  const mode = clusterMode();
  if (mode === "fandom") {
    for (const n of sim) {
      const src = nodesById[n.id];
      if (!src) continue;
      n.x = src.x || 0; n.y = src.y || 0; n.cluster = src.cluster || "";
    }
    return;
  }
  const works = sim.filter(n => n.kind === "work");
  const tags = sim.filter(n => n.kind !== "work");
  const clusters = {};
  for (const work of works) {
    const key = jsClusterKey(work);
    work.cluster = key;
    (clusters[key] = clusters[key] || []).push(work);
  }
  const tagClusters = {};
  for (const tag of tags) {
    const names = new Set();
    for (const wid of (tagWorks[tag.id] || [])) {
      const w = sim[simIndex[wid]];
      if (w && w.cluster) names.add(w.cluster);
    }
    tagClusters[tag.id] = names;
  }
  const localBy = {};
  const globalTags = [];
  const leftover = [];
  for (const tag of tags) {
    const names = tagClusters[tag.id] || new Set();
    if (names.size >= 2) { tag.cluster = ""; globalTags.push(tag); }
    else if (names.size === 1) {
      const key = [...names][0];
      tag.cluster = key;
      (localBy[key] = localBy[key] || []).push(tag);
    } else leftover.push(tag);
  }
  const keys = Object.keys(clusters).sort((a, b) => clusters[b].length - clusters[a].length || a.localeCompare(b));
  const packed = keys.map(key => ({ key, r: packCluster(clusters[key], localBy[key] || []) }));
  const gpts = sunflower(globalTags.length, 18);
  let globalR = 0;
  globalTags.forEach((tag, i) => {
    tag.x = gpts[i][0]; tag.y = gpts[i][1];
    globalR = Math.max(globalR, Math.hypot(tag.x, tag.y) + tag.r);
  });
  if (packed.length > 1) {
    const maxR = Math.max.apply(null, packed.map(p => p.r));
    const ring = globalR + maxR + 90;
    const others = packed.filter(p => p.key !== "_crossover");
    const around = packed.some(p => p.key === "_crossover") ? others : packed;
    around.forEach((p, i) => {
      const nAround = around.length;
      const ang = nAround === 2
        ? i * Math.PI
        : (2 * Math.PI * i / nAround) - Math.PI / 2;
      const cx = ring * Math.cos(ang), cy = ring * Math.sin(ang);
      for (const n of (clusters[p.key] || []).concat(localBy[p.key] || [])) {
        n.x += cx; n.y += cy;
      }
    });
  }
  for (const tag of leftover) {
    const nbrs = (adj[tag.id] || []).map(id => sim[simIndex[id]]).filter(Boolean);
    if (!nbrs.length) { tag.x = 0; tag.y = 0; continue; }
    tag.x = nbrs.reduce((s, n) => s + n.x, 0) / nbrs.length;
    tag.y = nbrs.reduce((s, n) => s + n.y, 0) / nbrs.length;
  }
}

let settleToken = 0;
function startSettle() {
  if (!settleOn()) return;
  const focus = selected;
  if (!focus) return;
  const token = ++settleToken;
  let keep = neighborhood(focus.id);
  let nodes = sim.filter(n => keep.has(n.id));
  if (nodes.length > 360) {
    nodes = nodes.filter(n =>
      n.id === focus.id || n.kind === "work" || n.works >= 2 || n.category === "Fandom"
    ).slice(0, 360);
    keep = new Set(nodes.map(n => n.id));
    keep.add(focus.id);
  }
  if (nodes.length < 2) return;
  let step = 0;
  function tick() {
    if (token !== settleToken) return;
    if (panning || step++ >= 24) {
      if (token === settleToken) fit(nodes);
      draw();
      return;
    }
    settleStep(nodes, keep);
    draw();
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function settleStep(nodes, keep) {
  const force = {};
  for (const n of nodes) force[n.id] = [0, 0];
  for (const e of vis.edges) {
    if (!keep.has(e.source) || !keep.has(e.target)) continue;
    const a = sim[simIndex[e.source]], b = sim[simIndex[e.target]];
    if (!a || !b) continue;
    const dx = b.x - a.x, dy = b.y - a.y;
    const dist = Math.hypot(dx, dy) || 0.01;
    const rest = a.r + b.r + 48;
    const f = (dist - rest) * 0.06;
    const ux = dx / dist, uy = dy / dist;
    force[a.id][0] += ux * f; force[a.id][1] += uy * f;
    force[b.id][0] -= ux * f; force[b.id][1] -= uy * f;
  }
  const nCount = nodes.length;
  const limit = nCount > 120 ? Math.min(nCount, 80) : nCount;
  for (let i = 0; i < limit; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < limit; j++) {
      const b = nodes[j];
      const dx = a.x - b.x, dy = a.y - b.y;
      const dist = Math.hypot(dx, dy) || 0.01;
      const min = a.r + b.r + 22;
      if (dist > min * 2.2) continue;
      const amt = dist < min ? (min - dist) * 0.22 : 0;
      if (!amt) continue;
      const ux = dx / dist, uy = dy / dist;
      force[a.id][0] += ux * amt; force[a.id][1] += uy * amt;
      force[b.id][0] -= ux * amt; force[b.id][1] -= uy * amt;
    }
  }
  for (const n of nodes) {
    const mass = n.kind === "work" ? 2.4 : 1;
    let dx = force[n.id][0] / mass, dy = force[n.id][1] / mass;
    const len = Math.hypot(dx, dy);
    if (len > 4) { dx *= 4 / len; dy *= 4 / len; }
    n.x += dx; n.y += dy;
  }
}

function toScreen(x, y) { return [x * view.k + view.x, y * view.k + view.y]; }
function toWorld(sx, sy) { return [(sx - view.x) / view.k, (sy - view.y) / view.k]; }

function hit(sx, sy) {
  let best = null, bestD = 1e9;
  for (const n of sim) {
    const p = toScreen(n.x, n.y);
    const d = Math.hypot(p[0] - sx, p[1] - sy);
    const pad = Math.max(n.r * view.k, 10);
    if (d <= pad && d < bestD) { best = n; bestD = d; }
  }
  return best;
}

function pinMode() {
  return !!(document.getElementById("pin-mode") || {}).checked;
}
function hopsValue() {
  const v = parseInt((document.getElementById("label-hops") || {}).value, 10);
  return Number.isFinite(v) ? Math.max(0, Math.min(6, v)) : 1;
}
function degreeValue() {
  const v = parseInt((document.getElementById("label-degree") || {}).value, 10);
  return Number.isFinite(v) ? Math.max(0, v) : 0;
}
function saveLabelSettings() {
  try {
    localStorage.setItem("ao3kit-graph-labels", JSON.stringify({
      hops: hopsValue(), degree: degreeValue(), pin: pinMode(),
      cluster: clusterMode(), settle: settleOn()
    }));
  } catch (err) {}
}
function loadLabelSettings() {
  try {
    const s = JSON.parse(localStorage.getItem("ao3kit-graph-labels") || "{}");
    if (s.hops != null) document.getElementById("label-hops").value = s.hops;
    if (s.degree != null) document.getElementById("label-degree").value = s.degree;
    if (s.pin != null) document.getElementById("pin-mode").checked = !!s.pin;
    if (s.cluster) document.getElementById("cluster-mode").value = s.cluster;
    if (s.settle != null) document.getElementById("settle-on").checked = !!s.settle;
  } catch (err) {}
}

function hopIds(id, hops) {
  const keep = new Set();
  if (id == null || simIndex[id] == null) return keep;
  keep.add(id);
  const q = [[id, 0]];
  for (let i = 0; i < q.length; i++) {
    const cur = q[i][0], dist = q[i][1];
    if (dist >= hops) continue;
    const links = adj[cur] || [];
    for (let j = 0; j < links.length; j++) {
      const nxt = links[j];
      if (keep.has(nxt) || simIndex[nxt] == null) continue;
      keep.add(nxt);
      q.push([nxt, dist + 1]);
    }
  }
  return keep;
}

function clusterBridges(keep, focusId) {
  const counts = {};
  for (const id of keep) {
    const n = sim[simIndex[id]];
    if (!n || n.kind !== "work") continue;
    const tags = workTags[id] || [];
    for (let i = 0; i < tags.length; i++) {
      const tid = tags[i];
      counts[tid] = (counts[tid] || 0) + 1;
    }
  }
  const focus = nodesById[focusId] || {};
  const focusFam = focus.category === "Fandom" ? (fandomFamily[focusId] || focusId) : null;
  const extra = Object.keys(counts).filter(tid => {
    if (counts[tid] < 2 || simIndex[tid] == null) return false;
    if (!focusFam) return true;
    const node = nodesById[tid];
    if (node && node.category === "Fandom") {
      return (fandomFamily[tid] || tid) === focusFam;
    }
    return true;
  });
  extra.sort((a, b) => counts[b] - counts[a] || a.localeCompare(b));
  return extra.slice(0, 24);
}

function neighborhood(id) {
  const hops = hopsValue();
  const keep = hopIds(id, hops);
  if (hops >= 1) {
    const extra = clusterBridges(keep, id);
    for (let i = 0; i < extra.length; i++) keep.add(extra[i]);
  }
  return keep;
}

function labelFocus() {
  return selected;
}

function areaLandmarks() {
  const seen = new Set();
  const out = [];
  function addFromId(id) {
    if (id == null || simIndex[id] == null) return;
    const node = sim[simIndex[id]];
    if (!node || seen.has(node.id)) return;
    seen.add(node.id);
    out.push(node);
  }
  const clusters = new Set();
  for (const n of sim) {
    if (n.kind === "work" && n.cluster && n.cluster !== "_other" && n.cluster !== "_all") {
      clusters.add(n.cluster);
    }
  }
  for (const key of clusters) addFromId(key);
  return out;
}

function neighborScore(n, focus) {
  if (focus && n.id === focus.id) return 10000;
  if (n.category === "Fandom") return 800 + (n.works || 0) * 10;
  if (n.category === "Relationship") return 600 + (n.works || 0) * 10;
  if (n.category === "Character") return 550 + (n.works || 0) * 10;
  if (hubIds.has(n.id) || n.works >= 2) return 500 + (n.works || 0) * 10;
  if (n.kind === "work") return 180 + (n.degree || 0);
  return 10 + (n.degree || 0);
}

function labelBudget() {
  const area = Math.max(1, wrap.clientWidth * wrap.clientHeight);
  return Math.max(10, Math.min(22, Math.floor(area / 32000)));
}

function labelNodes() {
  const focus = labelFocus();
  const minDeg = degreeValue();
  const seen = new Set();
  const out = [];
  function add(node) {
    if (!node || seen.has(node.id)) return;
    seen.add(node.id);
    out.push(node);
  }
  if (!pinMode() && hover) add(hover);
  if (focus) {
    add(focus);
    const keep = neighborhood(focus.id);
    const pool = [];
    for (const n of sim) if (keep.has(n.id) && n.id !== focus.id) pool.push(n);
    pool.sort((a, b) => neighborScore(b, focus) - neighborScore(a, focus));
    const budget = labelBudget();
    let works = 0;
    const workCap = focus.kind === "work" ? 4 : 6;
    for (const n of pool) {
      if (out.length >= budget) break;
      if (n.kind === "work") {
        if (works >= workCap) continue;
        works++;
      }
      add(n);
    }
  } else {
    for (const n of areaLandmarks()) add(n);
  }
  if (minDeg > 0) {
    for (const n of sim) {
      if ((n.degree || 0) >= minDeg) add(n);
    }
  }
  out.sort((a, b) => neighborScore(b, focus) - neighborScore(a, focus));
  return out;
}

function onScreen(n, w, h) {
  const p = toScreen(n.x, n.y);
  return p[0] > -80 && p[1] > -80 && p[0] < w + 80 && p[1] < h + 80;
}

function draw() {
  const w = wrap.clientWidth, h = wrap.clientHeight;
  if (w < 2 || h < 2) return;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== (w * dpr | 0) || canvas.height !== (h * dpr | 0)) {
    canvas.width = w * dpr; canvas.height = h * dpr;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const focus = labelFocus();
  const keep = focus ? neighborhood(focus.id) : null;
  ctx.save();
  ctx.translate(view.x, view.y);
  ctx.scale(view.k, view.k);
  ctx.lineCap = "round";
  const edgeCap = vis.edges.length > 4000;
  for (const e of vis.edges) {
    const a = sim[simIndex[e.source]], b = sim[simIndex[e.target]];
    if (!a || !b) continue;
    const inKeep = !keep || (keep.has(a.id) && keep.has(b.id));
    if (edgeCap && !inKeep && !onScreen(a, w, h) && !onScreen(b, w, h)) continue;
    if (keep && !inKeep) ctx.globalAlpha = e.kind === "work" ? 0.04 : 0.05;
    else if (e.kind === "work") ctx.globalAlpha = keep ? 0.7 : 0.16;
    else ctx.globalAlpha = e.kind === "synonym" ? 0.4 : 0.22;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
    ctx.strokeStyle = e.kind === "work" ? "#a78bfa"
      : e.kind === "synonym" ? "#fbbf24" : "#888";
    const wt = e.weight || 1;
    ctx.lineWidth = ((e.kind === "work" ? 0.6 : 0.9) + wt * 0.9) / view.k;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  for (const n of sim) {
    const on = !keep || keep.has(n.id);
    ctx.globalAlpha = on ? 1 : 0.14;
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
    ctx.fillStyle = n.color;
    ctx.fill();
    if (selected && n.id === selected.id) {
      ctx.strokeStyle = (!pinMode() && hover && hover.id !== selected.id) ? "#9ca3af" : "#fff";
      ctx.lineWidth = 2 / view.k;
      ctx.stroke();
    } else if (!pinMode() && hover && n.id === hover.id) {
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2 / view.k;
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
  drawLabels();
  ctx.restore();
}

function boxesOverlap(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

function drawLabels() {
  const fontPx = 12;
  ctx.font = (fontPx / view.k) + "px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  const focus = labelFocus();
  const nodes = labelNodes();
  const items = nodes.map(n => ({
    n,
    hovered: !!(hover && hover.id === n.id),
    focused: !!(selected && selected.id === n.id),
    pri: (hover && hover.id === n.id) || (selected && selected.id === n.id) ? 4
      : hubIds.has(n.id) ? 3
      : n.kind === "work" ? 2 : 1,
    deg: n.works || n.degree || 0
  }));
  items.sort((a, b) => b.pri - a.pri || b.deg - a.deg);
  const taken = [];
  const padX = 5 / view.k, padY = 3 / view.k;
  const gap = 6 / view.k;
  for (const item of items) {
    const n = item.n;
    const label = n.name.length > 42 ? n.name.slice(0, 40) + "…" : n.name;
    const tw = ctx.measureText(label).width;
    const th = fontPx / view.k;
    const slots = [
      [n.x + n.r + gap, n.y],
      [n.x - n.r - gap - tw, n.y],
      [n.x + n.r + gap, n.y - th - gap],
      [n.x + n.r + gap, n.y + th + gap],
      [n.x - n.r - gap - tw, n.y - th - gap],
      [n.x - n.r - gap - tw, n.y + th + gap]
    ];
    let box = null, lx = 0, ly = 0;
    for (let s = 0; s < slots.length; s++) {
      const x = slots[s][0], y = slots[s][1];
      const cand = { x: x - padX, y: y - th / 2 - padY, w: tw + padX * 2, h: th + padY * 2 };
      let overlaps = false;
      for (let i = 0; i < taken.length; i++) {
        if (boxesOverlap(cand, taken[i])) { overlaps = true; break; }
      }
      if (!overlaps) { box = cand; lx = x; ly = y; break; }
    }
    if (!box) {
      if (!item.hovered && !item.focused) continue;
      lx = slots[0][0]; ly = slots[0][1];
      box = { x: lx - padX, y: ly - th / 2 - padY, w: tw + padX * 2, h: th + padY * 2 };
    }
    taken.push(box);
    ctx.globalAlpha = 0.82;
    ctx.fillStyle = "#141414";
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(box.x, box.y, box.w, box.h, 3 / view.k);
    else ctx.rect(box.x, box.y, box.w, box.h);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#f2f2f2";
    ctx.fillText(label, lx, ly);
  }
}

function fit(nodes) {
  const set = nodes || sim;
  if (!set.length) return;
  let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
  for (const n of set) {
    minX = Math.min(minX, n.x - n.r); maxX = Math.max(maxX, n.x + n.r);
    minY = Math.min(minY, n.y - n.r); maxY = Math.max(maxY, n.y + n.r);
  }
  const w = wrap.clientWidth, h = wrap.clientHeight;
  if (w < 2 || h < 2) return;
  const gw = Math.max(maxX - minX, 40), gh = Math.max(maxY - minY, 40);
  view.k = Math.min(w / gw, h / gh) * 0.88;
  view.k = Math.min(Math.max(view.k, MIN_ZOOM), MAX_ZOOM);
  view.x = w / 2 - (minX + gw / 2) * view.k;
  view.y = h / 2 - (minY + gh / 2) * view.k;
}

function localXY(ev) {
  const r = wrap.getBoundingClientRect();
  return [ev.clientX - r.left, ev.clientY - r.top];
}

function similarChecked(root, key) {
  return Array.from(root.querySelectorAll('input[data-facet="' + key + '"]:checked'))
    .map(el => el.value);
}

function updateSimilarHint(root) {
  const hint = root.querySelector(".similar-and");
  if (!hint) return;
  const parts = [];
  ["fandoms", "authors", "relationships", "characters", "tags"].forEach(key => {
    similarChecked(root, key).forEach(name => parts.push(name));
  });
  const extra = (root.querySelector("[name=extra_query]") || {}).value || "";
  if (extra.trim()) parts.push(extra.trim());
  if (!parts.length) {
    hint.textContent = "Add a fandom, author, tag, or query.";
    return;
  }
  if (parts.length === 1) {
    hint.textContent = "Searching: " + parts[0];
    return;
  }
  hint.textContent = "AO3 ANDs all " + parts.length + " terms: "
    + parts.slice(0, 4).join(" · ")
    + (parts.length > 4 ? "…" : "");
}

function facetBlock(title, key, items) {
  const wrap = document.createElement("div");
  if (!items || !items.length) return wrap;
  const h = document.createElement("h3");
  h.textContent = title;
  wrap.appendChild(h);
  if (key === "tags" || key === "exclude") {
    const filter = document.createElement("input");
    filter.type = "search";
    filter.placeholder = "Filter…";
    filter.addEventListener("input", () => {
      const q = filter.value.trim().toLowerCase();
      wrap.querySelectorAll("label").forEach(lab => {
        const name = (lab.querySelector(".n") || lab).textContent.toLowerCase();
        lab.style.display = !q || name.indexOf(q) >= 0 ? "" : "none";
      });
    });
    wrap.appendChild(filter);
  }
  const list = document.createElement("div");
  list.className = "facet-list";
  items.forEach(item => {
    const lab = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.dataset.facet = key;
    cb.value = item.name;
    cb.checked = !!item.on;
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = item.name;
    lab.appendChild(cb);
    lab.appendChild(n);
    if (item.count > 1) {
      const k = document.createElement("span");
      k.className = "k";
      k.textContent = String(item.count);
      lab.appendChild(k);
    }
    list.appendChild(lab);
  });
  wrap.appendChild(list);
  return wrap;
}

function renderSimilarForm(seed, data, mount) {
  mount.innerHTML = "";
  const title = document.createElement("h3");
  title.textContent = "Find similar";
  mount.appendChild(title);
  const hint = document.createElement("div");
  hint.className = "meta";
  hint.textContent = data.hint || "AO3 requires every tag you check.";
  mount.appendChild(hint);
  const shortcuts = document.createElement("div");
  shortcuts.className = "similar-shortcuts";
  const isTag = !!(data.seed && data.seed.kind === "tag");
  const primary = document.createElement("button");
  primary.type = "button";
  primary.textContent = isTag ? "This tag only" : "Fandoms only";
  const clear = document.createElement("button");
  clear.type = "button";
  clear.textContent = "Clear";
  shortcuts.appendChild(primary);
  shortcuts.appendChild(clear);
  mount.appendChild(shortcuts);
  [
    ["Fandoms", "fandoms", data.fandoms],
    ["Authors", "authors", data.authors],
    ["Relationships", "relationships", data.relationships],
    ["Characters", "characters", data.characters],
    ["Additional tags", "tags", data.tags],
    ["Exclude tags", "exclude", data.exclude],
  ].forEach(row => mount.appendChild(facetBlock(row[0], row[1], row[2])));
  const andHint = document.createElement("div");
  andHint.className = "similar-and";
  mount.appendChild(andHint);
  const extraLab = document.createElement("label");
  extraLab.className = "row";
  extraLab.textContent = "Extra query";
  const extra = document.createElement("input");
  extra.type = "text";
  extra.name = "extra_query";
  extra.placeholder = "optional AO3 query";
  extraLab.appendChild(extra);
  mount.appendChild(extraLab);
  const sortLab = document.createElement("label");
  sortLab.className = "row";
  sortLab.textContent = "Sort by";
  const sort = document.createElement("select");
  sort.name = "sort_column";
  (data.sort_options || []).forEach(opt => {
    const o = document.createElement("option");
    o.value = opt.value;
    o.textContent = opt.label;
    if (opt.value === (data.sort_column || "kudos_count")) o.selected = true;
    sort.appendChild(o);
  });
  sortLab.appendChild(sort);
  mount.appendChild(sortLab);
  const completeLab = document.createElement("label");
  completeLab.className = "row";
  completeLab.textContent = "Complete works";
  const complete = document.createElement("select");
  complete.name = "complete";
  (data.complete_options || []).forEach(opt => {
    const o = document.createElement("option");
    o.value = opt.value;
    o.textContent = opt.label;
    complete.appendChild(o);
  });
  completeLab.appendChild(complete);
  mount.appendChild(completeLab);
  const langLab = document.createElement("label");
  langLab.className = "row";
  langLab.textContent = "Language";
  const lang = document.createElement("input");
  lang.type = "text";
  lang.name = "language_id";
  lang.value = data.language_id || "en";
  langLab.appendChild(lang);
  mount.appendChild(langLab);
  const maxLab = document.createElement("label");
  maxLab.className = "row";
  maxLab.textContent = "Max results";
  const max = document.createElement("input");
  max.type = "text";
  max.name = "max_results";
  max.value = data.max_results || "25";
  maxLab.appendChild(max);
  mount.appendChild(maxLab);
  const more = document.createElement("details");
  const sum = document.createElement("summary");
  sum.textContent = "More filters";
  more.appendChild(sum);
  function filterField(name, label, placeholder) {
    const lab = document.createElement("label");
    lab.className = "row";
    lab.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.name = name;
    input.placeholder = placeholder || "";
    lab.appendChild(input);
    more.appendChild(lab);
    return input;
  }
  const minScore = filterField("min_score", "Min quality score", "none");
  const minKudos = filterField("min_kudos", "Min kudos", "");
  const minWords = filterField("min_words", "Min words", "");
  const completeOnlyLab = document.createElement("label");
  completeOnlyLab.style.display = "flex";
  completeOnlyLab.style.gap = "6px";
  completeOnlyLab.style.marginTop = "8px";
  const completeOnly = document.createElement("input");
  completeOnly.type = "checkbox";
  completeOnly.name = "complete_only";
  completeOnlyLab.appendChild(completeOnly);
  completeOnlyLab.appendChild(document.createTextNode(
    "Only works with all planned chapters posted"
  ));
  more.appendChild(completeOnlyLab);
  mount.appendChild(more);
  const note = document.createElement("div");
  note.className = "meta";
  const btn = document.createElement("button");
  btn.className = "tool";
  btn.type = "button";
  btn.textContent = "Search and import";
  function setFacets(kind) {
    const seedName = (data.seed && data.seed.name) || "";
    const seedBucket = (data.seed && data.seed.bucket) || "";
    mount.querySelectorAll("input[data-facet]").forEach(cb => {
      if (kind === "clear") cb.checked = false;
      else if (kind === "tag") {
        cb.checked = cb.dataset.facet === seedBucket && cb.value === seedName;
      } else {
        cb.checked = cb.dataset.facet === "fandoms";
      }
    });
    updateSimilarHint(mount);
  }
  primary.onclick = () => setFacets(isTag ? "tag" : "fandoms");
  clear.onclick = () => setFacets("clear");
  mount.addEventListener("change", () => updateSimilarHint(mount));
  extra.addEventListener("input", () => updateSimilarHint(mount));
  btn.onclick = () => {
    if (!seed || (seed.kind === "work" && !seed.id) || (seed.kind === "tag" && !seed.name)) return;
    btn.disabled = true;
    btn.textContent = "Queuing…";
    note.textContent = "";
    const payload = {
      select: {
        fandoms: similarChecked(mount, "fandoms"),
        authors: similarChecked(mount, "authors"),
        relationships: similarChecked(mount, "relationships"),
        characters: similarChecked(mount, "characters"),
        tags: similarChecked(mount, "tags"),
        excluded_tags: similarChecked(mount, "exclude"),
        extra_query: extra.value.trim(),
      },
      sort_column: sort.value,
      complete: complete.value,
      language_id: lang.value.trim(),
      max_results: max.value.trim() || "25",
      min_score: minScore.value.trim(),
      min_kudos: minKudos.value.trim(),
      min_words: minWords.value.trim(),
      complete_only: completeOnly.checked,
    };
    if (seed.kind === "tag") {
      payload.tag = seed.name;
      if (seed.category) payload.category = seed.category;
    } else {
      payload.work_ids = [seed.id];
    }
    fetch("/similar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(r => r.json().then(d => ({ ok: r.ok, d }))).then(({ ok, d }) => {
      if (!ok) {
        btn.disabled = false;
        btn.textContent = "Search and import";
        note.textContent = d.error || "Could not queue search";
        return;
      }
      btn.textContent = "Queued";
      note.textContent = d.message || "Searching AO3";
    }).catch(() => {
      btn.disabled = false;
      btn.textContent = "Search and import";
      note.textContent = "Graph server did not accept the request.";
    });
  };
  mount.appendChild(btn);
  mount.appendChild(note);
  updateSimilarHint(mount);
}

function similarQuery(seed) {
  if (seed.kind === "tag") {
    let q = "/similar?tag=" + encodeURIComponent(seed.name);
    if (seed.category) q += "&category=" + encodeURIComponent(seed.category);
    return q;
  }
  return "/similar?work_id=" + encodeURIComponent(seed.id);
}

function loadSimilarForm(seed, mount) {
  mount.textContent = "Loading search options…";
  fetch(similarQuery(seed))
    .then(r => r.json().then(d => ({ ok: r.ok, d })))
    .then(({ ok, d }) => {
      if (!ok) {
        mount.textContent = d.error || "Could not load search options.";
        return;
      }
      renderSimilarForm(seed, d, mount);
    })
    .catch(() => {
      mount.textContent = "Graph server did not return search options.";
    });
}

function clearSelection() {
  selected = null;
  hover = null;
  lastHoverId = null;
  settleToken++;
  showDetail(null);
  fit();
  draw();
}

function showDetail(id) {
  const n = nodesById[id];
  const box = document.getElementById("detail");
  if (!n) { box.textContent = "Click a work, tag, bridge, or cluster."; return; }
  const neigh = DATA.edges.filter(e => e.source === id || e.target === id);
  const tags = neigh.filter(e => e.kind === "work").map(e => e.source === id ? e.target : e.source);
  const works = neigh.filter(e => e.kind === "work" && n.kind !== "work")
    .map(e => e.source === id ? e.target : e.source);
  const syn = neigh.filter(e => e.kind === "synonym").map(e => e.source === id ? e.target : e.source);
  const meta = neigh.filter(e => e.kind === "metatag" && e.source === id).map(e => e.target);
  const kids = neigh.filter(e => e.kind === "metatag" && e.target === id).map(e => e.source);
  box.innerHTML = "";
  const nameEl = document.createElement("div"); nameEl.className = "name"; nameEl.textContent = n.name;
  const metaEl = document.createElement("div"); metaEl.className = "meta";
  const bits = [n.kind === "work" ? "work" : n.status];
  if (n.category && n.kind !== "work") bits.push(n.category);
  if (n.cluster) bits.push(n.cluster);
  if (n.kind === "work") bits.push(tags.length + " tags");
  else if (works.length) bits.push(works.length + " works");
  metaEl.textContent = bits.join(" · ");
  box.appendChild(nameEl); box.appendChild(metaEl);
  const href = n.kind === "work"
    ? (n.url || (String(id).indexOf("work:") === 0
        ? "https://archiveofourown.org/works/" + id.slice(5) : ""))
    : "https://archiveofourown.org/tags/" + encodeURIComponent(n.name);
  if (href) {
    const a = document.createElement("a");
    a.href = href; a.target = "_blank"; a.rel = "noreferrer";
    a.textContent = n.kind === "work" ? "Open work on AO3" : "Open tag on AO3";
    box.appendChild(a);
  }
  if (DATA_URL) {
    let seed = null;
    if (n.kind === "work") {
      const workId = String(id).indexOf("work:") === 0 ? id.slice(5) : "";
      if (workId) seed = { kind: "work", id: workId };
    } else {
      const tagName = String(n.canonical || n.name || "").trim();
      if (tagName) seed = { kind: "tag", name: tagName, category: n.category || "" };
    }
    const key = seed
      ? (seed.kind === "work" ? "work:" + seed.id : "tag:" + seed.name)
      : null;
    if (key && similarState.key === key && similarState.el) {
      box.appendChild(similarState.el);
    } else if (seed) {
      similarState = { key: key, el: document.createElement("div") };
      similarState.el.className = "similar-panel";
      box.appendChild(similarState.el);
      loadSimilarForm(seed, similarState.el);
    } else {
      similarState = { key: null, el: null };
    }
  } else {
    similarState = { key: null, el: null };
  }
  function list(title, items) {
    if (!items.length) return;
    const h = document.createElement("div");
    h.style.marginTop = "8px"; h.style.color = "var(--muted)"; h.textContent = title;
    box.appendChild(h);
    items.slice(0, 40).forEach(itemId => {
      const node = nodesById[itemId] || { name: itemId, id: itemId };
      const b = document.createElement("button");
      b.className = "comp"; b.textContent = node.name;
      b.onclick = () => focusNode(itemId);
      box.appendChild(b);
    });
  }
  if (n.kind === "work") list("Tags on this work", tags);
  else list("Works with this tag", works);
  list("Canonical / synonyms", syn);
  list("Metatags", meta);
  list("Under this metatag", kids);
}

function focusNode(id) {
  hover = null;
  lastHoverId = null;
  selected = sim[simIndex[id]] || null;
  showDetail(id);
  const n = sim[simIndex[id]];
  if (n) {
    view.k = Math.max(view.k, 1.05);
    view.x = wrap.clientWidth / 2 - n.x * view.k;
    view.y = wrap.clientHeight / 2 - n.y * view.k;
  }
  draw();
  startSettle();
}

function focusCluster(key) {
  const members = sim.filter(n => n.cluster === key || n.id === key);
  if (!members.length) return;
  hover = null;
  lastHoverId = null;
  selected = sim[simIndex[key]] || members.find(n => n.kind === "work") || members[0];
  showDetail(selected.id);
  fit(members);
  draw();
  startSettle();
}

function fillHubs() {
  const box = document.getElementById("hubs");
  box.textContent = "";
  const hubs = DATA.hubs || [];
  if (!hubs.length) {
    const empty = document.createElement("div");
    empty.style.color = "var(--muted)";
    empty.textContent = "No shared tags yet.";
    box.appendChild(empty);
    return;
  }
  for (const h of hubs) {
    const b = document.createElement("button");
    b.className = "comp";
    const name = document.createElement("div"); name.textContent = h.name;
    const k = document.createElement("div"); k.className = "k";
    k.textContent = h.works + " works" + (h.category ? " · " + h.category : "");
    b.appendChild(name); b.appendChild(k);
    b.onclick = () => focusNode(h.id);
    box.appendChild(b);
  }
}

function fillClusters() {
  const box = document.getElementById("clusters");
  box.textContent = "";
  const groups = {};
  for (const n of sim.filter(n => n.kind === "work")) {
    const key = n.cluster || "_other";
    (groups[key] = groups[key] || []).push(n);
  }
  const keys = Object.keys(groups).sort((a, b) => groups[b].length - groups[a].length || a.localeCompare(b));
  if (!keys.length) {
    const empty = document.createElement("div");
    empty.style.color = "var(--muted)";
    empty.textContent = "No fandom clusters.";
    box.appendChild(empty);
    return;
  }
  for (const key of keys) {
    const b = document.createElement("button");
    b.className = "comp";
    const name = document.createElement("div");
    name.textContent = clusterTitle(key);
    const k = document.createElement("div"); k.className = "k";
    k.textContent = groups[key].length + " works";
    b.appendChild(name); b.appendChild(k);
    b.onclick = () => focusCluster(key);
    box.appendChild(b);
  }
}

function fillList() {
  const q = (document.getElementById("cf").value || "").trim().toLowerCase();
  const box = document.getElementById("comps");
  box.textContent = "";
  document.getElementById("list-title").textContent = "Works (" + allWorks.length + ")";
  const frag = document.createDocumentFragment();
  for (const n of allWorks) {
    if (q && n.name.toLowerCase().indexOf(q) < 0) continue;
    const b = document.createElement("button");
    b.className = "comp";
    const name = document.createElement("div"); name.textContent = n.name;
    const k = document.createElement("div"); k.className = "k";
    const live = simIndex[n.id] != null ? sim[simIndex[n.id]] : n;
    const cluster = live.cluster || n.cluster || "";
    k.textContent = (n.degree || 0) + " tags" + (cluster && cluster !== "_other" ? " · " + clusterTitle(cluster) : "");
    b.appendChild(name); b.appendChild(k);
    b.onclick = () => focusNode(n.id);
    frag.appendChild(b);
  }
  if (!frag.childNodes.length) {
    const empty = document.createElement("div");
    empty.style.color = "var(--muted)";
    empty.textContent = allWorks.length ? "No matching works." : "No AO3 works in this graph.";
    box.appendChild(empty);
    return;
  }
  box.appendChild(frag);
}

function suggest() {
  const q = (document.getElementById("q").value || "").trim().toLowerCase();
  const box = document.getElementById("suggest");
  if (!q) { box.style.display = "none"; return; }
  box.textContent = "";
  let shown = 0;
  for (const n of DATA.nodes) {
    if (n.name.toLowerCase().indexOf(q) < 0) continue;
    const b = document.createElement("button");
    const name = document.createElement("div"); name.textContent = n.name;
    const sub = document.createElement("div"); sub.className = "sub";
    sub.textContent = n.kind === "work" ? "work" : (n.category || n.status);
    b.appendChild(name); b.appendChild(sub);
    b.onclick = () => {
      box.style.display = "none";
      document.getElementById("q").value = n.name;
      focusNode(n.id);
    };
    box.appendChild(b);
    if (++shown >= 20) break;
  }
  box.style.display = shown ? "block" : "none";
}

function relayout() {
  rebuildVis();
  layoutNodes();
  applyClusterLayout();
  fillClusters();
  fillList();
  if (selected && settleOn()) startSettle();
  else fit();
  draw();
}

["show-works", "show-syn", "show-meta", "cat", "bridges-only"].forEach(id => {
  document.getElementById(id).onchange = relayout;
});
["label-hops", "label-degree"].forEach(id => {
  document.getElementById(id).addEventListener("input", () => {
    saveLabelSettings();
    draw();
    if (id === "label-hops") startSettle();
  });
});
document.getElementById("cluster-mode").onchange = () => {
  saveLabelSettings();
  relayout();
};
document.getElementById("settle-on").onchange = () => {
  saveLabelSettings();
  if (settleOn()) startSettle();
};
document.getElementById("pin-mode").onchange = () => {
  hover = null;
  lastHoverId = null;
  saveLabelSettings();
  draw();
};
document.getElementById("q").addEventListener("input", suggest);
document.getElementById("cf").addEventListener("input", fillList);
document.getElementById("z-in").onclick = () => { view.k = Math.min(MAX_ZOOM, view.k * 1.2); draw(); };
document.getElementById("z-out").onclick = () => { view.k = Math.max(MIN_ZOOM, view.k / 1.2); draw(); };
document.getElementById("z-fit").onclick = () => { fit(); draw(); };

let down = null;
wrap.addEventListener("pointerdown", ev => {
  const xy = localXY(ev);
  const n = hit(xy[0], xy[1]);
  down = { x: xy[0], y: xy[1], n, moved: false };
  lastPtr = { x: xy[0], y: xy[1] };
  if (n) {
    selected = n;
    showDetail(n.id);
    draw();
    startSettle();
  } else panning = true;
  wrap.classList.add("drag");
  wrap.setPointerCapture(ev.pointerId);
});
wrap.addEventListener("pointermove", ev => {
  const xy = localXY(ev);
  if (down && Math.hypot(xy[0] - down.x, xy[1] - down.y) > 5) down.moved = true;
  if (panning && lastPtr) {
    view.x += xy[0] - lastPtr.x; view.y += xy[1] - lastPtr.y;
    draw();
  } else if (!pinMode()) {
    hover = hit(xy[0], xy[1]);
    wrap.style.cursor = hover ? "pointer" : "grab";
    const hid = hover ? hover.id : null;
    if (hid !== lastHoverId) { lastHoverId = hid; draw(); }
  } else {
    wrap.style.cursor = hit(xy[0], xy[1]) ? "pointer" : "grab";
  }
  lastPtr = { x: xy[0], y: xy[1] };
});
wrap.addEventListener("pointerup", () => {
  if (down && !down.moved && !down.n) clearSelection();
  panning = false; lastPtr = null; down = null;
  wrap.classList.remove("drag");
});
wrap.addEventListener("pointerleave", () => {
  if (panning) return;
  hover = null;
  lastHoverId = null;
  draw();
});
wrap.addEventListener("wheel", ev => {
  ev.preventDefault();
  const xy = localXY(ev);
  const factor = ev.deltaY < 0 ? 1.12 : 0.89;
  const nk = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.k * factor));
  view.x = xy[0] - (xy[0] - view.x) * (nk / view.k);
  view.y = xy[1] - (xy[1] - view.y) * (nk / view.k);
  view.k = nk;
  draw();
}, { passive: false });
window.addEventListener("resize", () => { fit(); draw(); });
document.addEventListener("keydown", ev => {
  if (ev.key === "Escape") {
    if (document.activeElement && document.activeElement.tagName === "INPUT") {
      document.activeElement.blur();
      return;
    }
    ev.preventDefault();
    clearSelection();
    return;
  }
  if (ev.key === "/" && document.activeElement.tagName !== "INPUT") {
    ev.preventDefault(); document.getElementById("q").focus();
  }
});
fillHubs();
fillClusters();
fillList();
loadLabelSettings();
relayout();
const reloadBtn = document.getElementById("reload-data");
if (DATA_URL && reloadBtn) {
  reloadBtn.hidden = false;
  reloadBtn.onclick = () => {
    reloadBtn.disabled = true;
    reloadBtn.textContent = "Reloading…";
    fetch("/rebuild", { method: "POST" })
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(() => location.reload())
      .catch(() => location.reload());
  };
  if (!window.__graphPoll) {
    window.__graphPoll = true;
    let seenWorks = DATA.work_count;
    let reloadTimer = 0;
    setInterval(() => {
      fetch("/status").then(r => r.json()).then(s => {
        if (typeof s.works !== "number") return;
        if (seenWorks == null) { seenWorks = s.works; return; }
        if (s.works === seenWorks) return;
        seenWorks = s.works;
        const stats = document.getElementById("stats");
        if (stats) stats.textContent = s.works + " works — updating…";
        clearTimeout(reloadTimer);
        reloadTimer = setTimeout(() => location.reload(), 1600);
      }).catch(() => {});
    }, 2000);
  }
}
}

if (EMBEDDED && EMBEDDED.nodes) startGraph(EMBEDDED);
else {
  const url = DATA_URL || "graph.json";
  const stats = document.getElementById("stats");
  if (stats) stats.textContent = "Loading graph…";
  fetch(url).then(r => {
    if (!r.ok) throw new Error(r.status + " " + r.statusText);
    return r.json();
  }).then(startGraph).catch(err => {
    if (stats) {
      stats.textContent = "No graph data yet. In Calibre run Tag graph… then Reload data.";
    }
    console.error(err);
  });
}
</script>
</body>
</html>

"""
