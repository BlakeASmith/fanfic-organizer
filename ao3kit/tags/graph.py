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
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import webbrowser
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

from ao3kit.tags.cache import (
    DEFAULT_TAG_CACHE_PATH,
    DEFAULT_TAG_CACHE_TTL_DAYS,
    CacheRow,
    TagCache,
)
from ao3kit.tags.clean import collect_unique_tag_names
from ao3kit.tags.warm import EXTRA_NAME_KEYS, collect_warm_names, load_jsonl_records

SynonymMode = Literal["seed", "all", "none"]
GraphFormat = Literal["html", "json", "dot"]

DEFAULT_GRAPH_HTML = (
    Path(__file__).resolve().parents[2] / ".cache" / "tag-graph.html"
)

# Calibre / FanFicFare status labels, not AO3 tags on the work.
_SKIP_WORK_TAGS = frozenset({"completed", "complete", "fanfiction"})


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
    """Link magnitude for the viewer: higher is a shorter, stiffer spring.

    AO3 works routinely carry dozens of tags. Extra tags must not weaken
    the spokes (that inflates a halo); busy works get a slight hold bonus.
    """
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "components": [item.to_dict() for item in self.components],
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
            )
        )
        for node in members:
            node.component = index
    return components


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
    )


def graph_payload(graph: TagGraph) -> dict[str, Any]:
    return graph.to_dict()


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


def render_html(graph: TagGraph) -> str:
    payload = graph_payload(graph)
    data = _json_for_script(payload)
    return _HTML_TEMPLATE.replace("%%DATA%%", data)


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
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
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
        help="Output path (default: .cache/tag-graph.html, or stdout for json/dot)",
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
        default=DEFAULT_TAG_CACHE_PATH,
        help=f"Tag cache SQLite path (default: {DEFAULT_TAG_CACHE_PATH})",
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
    cache_path = Path(args.cache) if args.cache else DEFAULT_TAG_CACHE_PATH
    if not cache_path.is_file():
        print(f"Tag cache not found: {cache_path}", file=sys.stderr)
        print(
            "Warm it first: python -m ao3kit tags warm start --names-file tags.txt",
            file=sys.stderr,
        )
        return 1

    seed = collect_warm_names(
        jsonl_paths=[Path(p) for p in args.jsonl],
        names_files=[Path(p) for p in args.names_file],
        names=list(args.tags),
    )
    records: list[dict[str, Any]] = []
    for path in args.jsonl:
        records.extend(load_jsonl_records(Path(path)))
    works = works_from_records(records)
    cache = TagCache.load(cache_path, ttl_days=ttl)
    try:
        graph = build_tag_graph(
            cache,
            seed or None,
            include_metatags=not args.no_metatags,
            synonyms=args.synonyms,
            works=works,
        )
    finally:
        cache.close()

    out_path = Path(args.output) if args.output else None
    fmt = infer_format(out_path, args.format)
    if out_path is None and fmt == "html":
        out_path = DEFAULT_GRAPH_HTML
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
  position: relative;
}
.toolbar input[type="search"], .toolbar select {
  background: var(--bg); color: var(--text); border: 1px solid var(--line);
  padding: 6px 8px; min-width: 200px;
}
.toolbar label { color: var(--muted); display: flex; gap: 6px; align-items: center; }
.legend { display: flex; flex-wrap: wrap; gap: 8px 12px; color: var(--muted); margin-left: auto; }
.swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block;
  margin-right: 4px; vertical-align: -1px; }
#suggest {
  position: absolute; left: 16px; top: 100%; z-index: 5; width: 360px; max-height: 280px;
  overflow: auto; background: var(--panel); border: 1px solid var(--line); display: none;
}
#suggest button {
  display: block; width: 100%; text-align: left; background: transparent; border: 0;
  color: var(--text); padding: 7px 10px; cursor: pointer; border-bottom: 1px solid var(--line);
}
#suggest button:hover, #suggest button.active { background: var(--card); }
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
  pointer-events: none;
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
#detail ul { margin: 4px 0 0; padding-left: 0; list-style: none; }
.comp {
  display: block; width: 100%; text-align: left; background: transparent;
  border: 0; color: var(--text); padding: 7px 6px; cursor: pointer;
  border-bottom: 1px solid var(--line);
}
.comp:hover { background: var(--card); }
.comp .k { color: var(--muted); font-size: 11px; }
#cf { min-width: 0; width: 100%; margin-bottom: 8px;
  background: var(--bg); color: var(--text); border: 1px solid var(--line); padding: 6px 8px; }
.settings {
  display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center;
  padding: 6px 16px; border-bottom: 1px solid var(--line);
  background: #1e1e1e; color: var(--muted); font-size: 12px;
}
.settings label { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.settings input[type="range"] { width: 92px; accent-color: var(--accent); }
.settings select {
  background: var(--bg); color: var(--text); border: 1px solid var(--line);
  padding: 4px 6px;
}
.settings button {
  background: var(--card); color: var(--text); border: 1px solid var(--line);
  padding: 4px 8px; cursor: pointer; border-radius: 4px;
}
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
  <label><input type="checkbox" id="show-physics" checked/> Physics</label>
  <select id="names-mode" title="When to show node names">
    <option value="hover">Names: hover</option>
    <option value="works" selected>Names: works</option>
    <option value="all">Names: all</option>
  </select>
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
<div class="settings" id="phys-settings">
  <label>Bounce <input id="p-bounce" type="range" min="0" max="100" value="62"/></label>
  <label>Stiffness <input id="p-stiff" type="range" min="20" max="200" value="90"/></label>
  <label>Repel <input id="p-charge" type="range" min="20" max="200" value="80"/></label>
  <label>Cluster <input id="p-spread" type="range" min="40" max="180" value="70"/></label>
  <label>Settle <input id="p-settle" type="range" min="10" max="200" value="80"/></label>
  <button type="button" id="p-jiggle">Jiggle</button>
</div>
<main>
  <div id="canvas-wrap">
    <canvas id="g"></canvas>
    <div id="hint">Scroll to zoom · ⤢ fits everything · drag a node · Cluster keeps the blob round</div>
    <div id="zoom">
      <button type="button" id="z-in" title="Zoom in">+</button>
      <button type="button" id="z-out" title="Zoom out">−</button>
      <button type="button" id="z-fit" title="Fit">⤢</button>
    </div>
  </div>
  <aside id="sidebar">
    <h2>Selected</h2>
    <div id="detail">Click a work or tag.</div>
    <h2 id="list-title">Works</h2>
    <input id="cf" type="search" placeholder="Filter works…"/>
    <div id="comps"></div>
  </aside>
</main>
<script>
const DATA = %%DATA%%;
const wrap = document.getElementById("canvas-wrap");
const canvas = document.getElementById("g");
const ctx = canvas.getContext("2d");
const catSel = document.getElementById("cat");
const nodesById = Object.fromEntries(DATA.nodes.map(n => [n.id, n]));
const adj = {};
for (const n of DATA.nodes) adj[n.id] = [];
for (const e of DATA.edges) {
  (adj[e.source] || (adj[e.source] = [])).push(e.target);
  (adj[e.target] || (adj[e.target] = [])).push(e.source);
}
const cats = [...new Set(DATA.nodes.map(n => n.category).filter(Boolean))].sort();
for (const c of cats) {
  const o = document.createElement("option"); o.value = c; o.textContent = c; catSel.appendChild(o);
}
const allWorks = DATA.nodes.filter(n => n.kind === "work")
  .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
const tagCount = DATA.nodes.length - allWorks.length;
document.getElementById("stats").innerHTML =
  "<span><strong>" + allWorks.length + "</strong> works</span>" +
  "<span><strong>" + tagCount + "</strong> tags</span>" +
  "<span><strong>" + (DATA.work_edges || 0) + "</strong> work–tag links</span>" +
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
  return n.kind === "work" ? 8 + d * 0.6 : 2.6 + d * 0.45;
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
let view = { x: 0, y: 0, k: 1 };
let dragging = null;
let panning = false;
let lastPtr = null;
let lastHoverId = null;
let viewLocked = false;
const MIN_ZOOM = 0.02;
const MAX_ZOOM = 6;

function rebuildVis() {
  const cat = catSel.value;
  const k = kinds();
  const ids = new Set();
  for (const n of DATA.nodes) {
    if (n.kind === "work") { if (k.has("work")) ids.add(n.id); continue; }
    if (n.status === "synonym" && !k.has("synonym")) continue;
    if (cat && n.category !== cat) continue;
    ids.add(n.id);
  }
  const edges = [];
  for (const e of DATA.edges) {
    if (k.has(e.kind) && ids.has(e.source) && ids.has(e.target)) edges.push(e);
  }
  vis = { ids, edges };
}

function hashAngle(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return ((h >>> 0) % 1000) / 1000 * Math.PI * 2;
}

let springs = [];
let alpha = 0;
let looping = false;

function physicsOn() {
  return document.getElementById("show-physics").checked;
}

const SETTING_IDS = ["p-bounce", "p-stiff", "p-charge", "p-spread", "p-settle", "names-mode", "show-physics"];
function saveSettings() {
  const s = {};
  for (const id of SETTING_IDS) {
    const el = document.getElementById(id);
    if (!el) continue;
    s[id] = el.type === "checkbox" ? el.checked : el.value;
  }
  try { localStorage.setItem("ao3kit-graph-v2", JSON.stringify(s)); } catch (err) {}
}
function loadSettings() {
  try {
    const s = JSON.parse(localStorage.getItem("ao3kit-graph-v2") || "{}");
    for (const id of SETTING_IDS) {
      if (s[id] == null) continue;
      const el = document.getElementById(id);
      if (!el) continue;
      if (el.type === "checkbox") el.checked = !!s[id];
      else el.value = s[id];
    }
  } catch (err) {}
}

function layoutNodes() {
  sim = [];
  simIndex = {};
  springs = [];
  const works = [];
  const tags = [];
  for (const n of DATA.nodes) {
    if (!vis.ids.has(n.id)) continue;
    const mass = n.kind === "work"
      ? 10 + Math.log(1 + (n.degree || 0)) * 1.8
      : 1.1 + Math.log(1 + (n.degree || 0));
    const node = {
      id: n.id, name: n.name, kind: n.kind, status: n.status, category: n.category,
      in_seed: n.in_seed, degree: n.degree, url: n.url,
      x: 0, y: 0, vx: 0, vy: 0, mass: mass,
      r: radiusOf(n), color: colorFor(n)
    };
    simIndex[n.id] = sim.length;
    sim.push(node);
    if (n.kind === "work") works.push(node); else tags.push(node);
  }
  const nW = Math.max(works.length, 1);
  const nAll = Math.max(sim.length, 1);
  const R = 28 + Math.sqrt(nW) * 22;
  works.forEach((n, i) => {
    const a = (i / nW) * Math.PI * 2 - Math.PI / 2;
    const jitter = 8 + Math.sqrt(nW);
    n.x = Math.cos(a) * R + (Math.random() - 0.5) * jitter;
    n.y = Math.sin(a) * R + (Math.random() - 0.5) * jitter;
  });
  const slot = {};
  for (const n of tags) {
    const hosts = [];
    const links = adj[n.id] || [];
    for (let i = 0; i < links.length; i++) {
      const h = sim[simIndex[links[i]]];
      if (h && h.kind === "work") hosts.push(h);
    }
    if (!hosts.length) {
      const a = hashAngle(n.id);
      const r = Math.sqrt(nAll) * 10 * Math.random();
      n.x = Math.cos(a) * r;
      n.y = Math.sin(a) * r;
      continue;
    }
    if (hosts.length === 1) {
      const h = hosts[0];
      const k = (slot[h.id] = (slot[h.id] || 0) + 1) - 1;
      const perRing = 12;
      const ring = Math.floor(k / perRing);
      const iOn = k % perRing;
      const a = (iOn / perRing) * Math.PI * 2 + ring * 0.28;
      const rad = h.r + 6 + ring * 7;
      n.x = h.x + Math.cos(a) * rad;
      n.y = h.y + Math.sin(a) * rad;
    } else {
      let x = 0, y = 0;
      for (const h of hosts) { x += h.x; y += h.y; }
      n.x = x / hosts.length;
      n.y = y / hosts.length;
    }
  }
  for (const e of vis.edges) {
    const a = sim[simIndex[e.source]], b = sim[simIndex[e.target]];
    if (!a || !b) continue;
    const w = e.weight || 1;
    if (e.kind === "work") {
      springs.push({
        a, b, kind: e.kind, weight: w,
        len: a.r + b.r + 5, str: 1.4 * w, pullOnly: true
      });
    } else if (e.kind === "synonym") {
      springs.push({ a, b, kind: e.kind, weight: w, len: a.r + b.r + 4, str: 1.2 * w, pullOnly: false });
    } else {
      springs.push({ a, b, kind: e.kind, weight: w, len: a.r + b.r + 9, str: 0.45 * w, pullOnly: false });
    }
  }
}

function slider(id, fallback) {
  const el = document.getElementById(id);
  const v = el ? parseFloat(el.value) : fallback;
  return Number.isFinite(v) ? v : fallback;
}

function kick(amount) {
  if (!physicsOn()) { alpha = 0; return; }
  alpha = Math.max(alpha, amount);
  if (!looping) { looping = true; requestAnimationFrame(loop); }
}

function tick() {
  const bounce = slider("p-bounce", 62) / 100;
  const stiff = slider("p-stiff", 90) / 100;
  const chargeMul = slider("p-charge", 80) / 100;
  const spread = slider("p-spread", 70) / 100;
  const friction = 0.78 + bounce * 0.18;
  const k = dragging ? Math.max(Math.min(alpha, 1), 0.55) : Math.min(alpha, 1);
  const nCount = Math.max(sim.length, 1);
  const targetR = (48 + Math.sqrt(nCount) * 13) * (0.36 + spread * 0.7);
  for (const s of springs) {
    const a = s.a, b = s.b;
    let dx = b.x - a.x, dy = b.y - a.y;
    const dist = Math.hypot(dx, dy) || 0.01;
    if (s.pullOnly && dist <= s.len) continue;
    const f = ((dist - s.len) / dist) * s.str * stiff * k;
    dx *= f; dy *= f;
    if (a !== dragging) { a.vx += dx / a.mass; a.vy += dy / a.mass; }
    if (b !== dragging) { b.vx -= dx / b.mass; b.vy -= dy / b.mass; }
  }
  const cell = 42;
  const bins = new Map();
  for (const n of sim) {
    const key = (n.x / cell | 0) + ":" + (n.y / cell | 0);
    let b = bins.get(key);
    if (!b) { b = []; bins.set(key, b); }
    b.push(n);
  }
  for (const n of sim) {
    if (n === dragging) continue;
    const cx = n.x / cell | 0, cy = n.y / cell | 0;
    for (let ix = cx - 1; ix <= cx + 1; ix++) {
      for (let iy = cy - 1; iy <= cy + 1; iy++) {
        const bucket = bins.get(ix + ":" + iy);
        if (!bucket) continue;
        for (const o of bucket) {
          if (o === n) continue;
          let dx = n.x - o.x, dy = n.y - o.y;
          let dist2 = dx * dx + dy * dy;
          if (dist2 === 0) { dx = 0.6; dy = 0.6; dist2 = 0.7; }
          const towardWork = n.kind === "work" || o.kind === "work";
          const min = n.r + o.r + (towardWork ? 2 : 5);
          if (dist2 > min * min * 16) continue;
          const dist = Math.sqrt(dist2);
          const charge = (towardWork ? 6 : 16) * chargeMul * k / dist2;
          const overlap = dist < min ? (min - dist) / dist : 0;
          const collide = overlap * (0.16 + bounce * 0.22) * k;
          const push = charge + collide;
          n.vx += dx * push / n.mass;
          n.vy += dy * push / n.mass;
          if (overlap && bounce > 0.35) {
            const along = (n.vx * dx + n.vy * dy) / dist2;
            if (along < 0) {
              n.vx -= dx * along * bounce;
              n.vy -= dy * along * bounce;
            }
          }
        }
      }
    }
  }
  const big = 88;
  const coarse = new Map();
  for (const n of sim) {
    const key = (n.x / big | 0) + ":" + (n.y / big | 0);
    let c = coarse.get(key);
    if (!c) { c = { x: 0, y: 0, m: 0 }; coarse.set(key, c); }
    c.x += n.x * n.mass; c.y += n.y * n.mass; c.m += n.mass;
  }
  for (const c of coarse.values()) { c.x /= c.m; c.y /= c.m; }
  const far = 40 * chargeMul * k;
  for (const n of sim) {
    if (n === dragging) continue;
    const cx = n.x / big | 0, cy = n.y / big | 0;
    for (let ix = cx - 3; ix <= cx + 3; ix++) {
      for (let iy = cy - 3; iy <= cy + 3; iy++) {
        if (Math.abs(ix - cx) <= 1 && Math.abs(iy - cy) <= 1) continue;
        const c = coarse.get(ix + ":" + iy);
        if (!c) continue;
        const dx = n.x - c.x, dy = n.y - c.y;
        const dist2 = dx * dx + dy * dy + 400;
        const push = far * c.m / dist2 / n.mass;
        n.vx += dx * push;
        n.vy += dy * push;
      }
    }
    const dist = Math.hypot(n.x, n.y) || 0.01;
    n.vx += (-n.x) * 0.028 * k / n.mass;
    n.vy += (-n.y) * 0.028 * k / n.mass;
    if (dist > targetR * 0.82) {
      const t = (dist - targetR * 0.82) / Math.max(targetR * 0.18, 1);
      const pull = Math.min(t * t, 5) * 0.16 * k;
      n.vx -= (n.x / dist) * pull;
      n.vy -= (n.y / dist) * pull;
    }
  }
  packComponents(targetR, k);
  for (const n of sim) {
    if (n === dragging) continue;
    n.vx *= friction; n.vy *= friction;
    n.x += n.vx; n.y += n.vy;
  }
  roundBlob();
}

function packComponents(targetR, k) {
  const nCount = sim.length;
  if (nCount < 3) return;
  const parent = new Array(nCount);
  for (let i = 0; i < nCount; i++) parent[i] = i;
  function find(i) {
    while (parent[i] !== i) i = parent[i] = parent[parent[i]];
    return i;
  }
  for (const s of springs) {
    const i = simIndex[s.a.id], j = simIndex[s.b.id];
    if (i == null || j == null) continue;
    const a = find(i), b = find(j);
    if (a !== b) parent[a] = b;
  }
  const groups = new Map();
  for (let i = 0; i < nCount; i++) {
    const r = find(i);
    let g = groups.get(r);
    if (!g) { g = { x: 0, y: 0, n: 0, nodes: [] }; groups.set(r, g); }
    g.x += sim[i].x; g.y += sim[i].y; g.n++; g.nodes.push(sim[i]);
  }
  for (const g of groups.values()) {
    g.x /= g.n; g.y /= g.n;
    const d = Math.hypot(g.x, g.y);
    const cap = targetR * (0.12 + 0.55 * Math.sqrt(g.n / nCount));
    if (d <= cap || !d) continue;
    const s = (d - cap) / d * 0.18 * k;
    for (const n of g.nodes) {
      if (n === dragging) continue;
      n.vx -= g.x * s;
      n.vy -= g.y * s;
    }
  }
}

function roundBlob() {
  const N = sim.length;
  if (dragging || N < 8) {
    if (!dragging && N) {
      let ax = 0, ay = 0;
      for (const n of sim) { ax += n.x; ay += n.y; }
      ax /= N; ay /= N;
      for (const n of sim) { n.x -= ax; n.y -= ay; }
    }
    return;
  }
  let ax = 0, ay = 0;
  for (const n of sim) { ax += n.x; ay += n.y; }
  ax /= N; ay /= N;
  let xx = 0, yy = 0, xy = 0;
  for (const n of sim) {
    const dx = n.x - ax, dy = n.y - ay;
    xx += dx * dx; yy += dy * dy; xy += dx * dy;
  }
  xx /= N; yy /= N; xy /= N;
  const trace = xx + yy;
  const gap = Math.sqrt(Math.max(0, trace * trace / 4 - (xx * yy - xy * xy)));
  const l1 = trace / 2 + gap;
  const l2 = trace / 2 - gap;
  let ux = 1, uy = 0, shrink = 1;
  if (l1 > 1 && l1 > l2 * 1.28) {
    ux = l1 - yy; uy = xy;
    if (Math.abs(ux) + Math.abs(uy) < 1e-9) { ux = xy; uy = l1 - xx; }
    const vlen = Math.hypot(ux, uy) || 1;
    ux /= vlen; uy /= vlen;
    const elong = Math.sqrt(l1 / Math.max(l2, 1));
    shrink = 1 - Math.min(0.06, (elong - 1.12) * 0.05);
  }
  for (const n of sim) {
    let dx = n.x - ax, dy = n.y - ay;
    if (shrink < 1) {
      const along = dx * ux + dy * uy;
      dx -= ux * along * (1 - shrink);
      dy -= uy * along * (1 - shrink);
    }
    n.x = dx;
    n.y = dy;
  }
}

function loop() {
  const holding = !!dragging;
  if ((alpha > 0.012 || holding) && physicsOn()) {
    tick();
    if (holding) alpha = Math.max(alpha, 0.75);
    else {
      const settle = slider("p-settle", 80) / 100;
      alpha *= 0.988 - settle * 0.05;
    }
    if (!viewLocked && !holding) fit();
    draw();
    requestAnimationFrame(loop);
  } else {
    looping = false;
    alpha = 0;
    if (!viewLocked) fit();
    draw();
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

function neighborhood(id) {
  const keep = new Set([id]);
  const links = adj[id] || [];
  for (let i = 0; i < links.length; i++) keep.add(links[i]);
  return keep;
}

function onScreen(n, w, h) {
  const p = toScreen(n.x, n.y);
  return p[0] > -40 && p[1] > -40 && p[0] < w + 40 && p[1] < h + 40;
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
  const focus = hover || selected;
  const keep = focus ? neighborhood(focus.id) : null;
  const namesMode = (document.getElementById("names-mode") || {}).value || "works";
  ctx.save();
  ctx.translate(view.x, view.y);
  ctx.scale(view.k, view.k);
  ctx.lineCap = "round";
  for (const e of vis.edges) {
    const a = sim[simIndex[e.source]], b = sim[simIndex[e.target]];
    if (!a || !b) continue;
    const inKeep = !keep || keep.has(a.id) || keep.has(b.id);
    if (keep && !inKeep) ctx.globalAlpha = e.kind === "work" ? 0.06 : 0.05;
    else if (e.kind === "work") ctx.globalAlpha = keep ? 0.75 : 0.22;
    else ctx.globalAlpha = e.kind === "synonym" ? 0.4 : 0.2;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
    ctx.strokeStyle = e.kind === "work" ? "#a78bfa"
      : e.kind === "synonym" ? "#fbbf24" : "#888";
    const w = e.weight || 1;
    ctx.lineWidth = ((e.kind === "work" ? 0.7 : 0.9) + w * 1.1) / view.k;
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
    if (focus && n.id === focus.id) {
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2 / view.k;
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#eee";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.font = (11 / view.k) + "px ui-sans-serif, system-ui, sans-serif";
  for (const n of sim) {
    const hovered = keep && keep.has(n.id);
    const named = namesMode === "all" ||
      (namesMode === "works" && n.kind === "work") ||
      hovered;
    if (!named) continue;
    if (namesMode === "all" && n.kind !== "work" && n.r * view.k < 2.2 && !hovered) continue;
    const label = n.name.length > 42 ? n.name.slice(0, 40) + "…" : n.name;
    ctx.globalAlpha = hovered || n.kind === "work" ? 0.95 : 0.75;
    ctx.fillText(label, n.x + n.r + 4 / view.k, n.y);
  }
  ctx.restore();
}

function fit() {
  if (!sim.length) return;
  let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
  for (const n of sim) {
    minX = Math.min(minX, n.x - n.r); maxX = Math.max(maxX, n.x + n.r);
    minY = Math.min(minY, n.y - n.r); maxY = Math.max(maxY, n.y + n.r);
  }
  const w = wrap.clientWidth, h = wrap.clientHeight;
  if (w < 2 || h < 2) return;
  const gw = Math.max(maxX - minX, 40), gh = Math.max(maxY - minY, 40);
  view.k = Math.min(w / gw, h / gh) * 0.9;
  view.k = Math.min(Math.max(view.k, MIN_ZOOM), MAX_ZOOM);
  view.x = w / 2 - (minX + gw / 2) * view.k;
  view.y = h / 2 - (minY + gh / 2) * view.k;
}

function localXY(ev) {
  const r = wrap.getBoundingClientRect();
  return [ev.clientX - r.left, ev.clientY - r.top];
}

function showDetail(id) {
  const n = nodesById[id];
  const box = document.getElementById("detail");
  if (!n) { box.textContent = "Click a work or tag."; return; }
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
  if (n.kind === "work") bits.push(tags.length + " tags");
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
  function list(title, items) {
    if (!items.length) return;
    const h = document.createElement("div");
    h.style.marginTop = "8px"; h.style.color = "var(--muted)"; h.textContent = title;
    box.appendChild(h);
    items.forEach(itemId => {
      const node = nodesById[itemId] || { name: itemId, id: itemId };
      const b = document.createElement("button");
      b.className = "comp"; b.textContent = node.name;
      b.onclick = () => focusNode(itemId);
      box.appendChild(b);
    });
  }
  list("Tags on this work", tags);
  list("Works with this tag", works);
  list("Canonical / synonyms", syn);
  list("Metatags", meta);
  list("Under this metatag", kids);
}

function focusNode(id) {
  selected = sim[simIndex[id]] || null;
  showDetail(id);
  const n = sim[simIndex[id]];
  if (n) {
    viewLocked = true;
    view.k = Math.max(view.k, 1.1);
    view.x = wrap.clientWidth / 2 - n.x * view.k;
    view.y = wrap.clientHeight / 2 - n.y * view.k;
  }
  draw();
}

function fillList() {
  const q = (document.getElementById("cf").value || "").trim().toLowerCase();
  const box = document.getElementById("comps");
  box.textContent = "";
  const title = document.getElementById("list-title");
  title.textContent = "Works (" + allWorks.length + ")";
  const frag = document.createDocumentFragment();
  for (const n of allWorks) {
    if (q && n.name.toLowerCase().indexOf(q) < 0) continue;
    const b = document.createElement("button");
    b.className = "comp";
    const name = document.createElement("div");
    name.textContent = n.name;
    const k = document.createElement("div");
    k.className = "k";
    k.textContent = (n.degree || 0) + " tags";
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
  viewLocked = false;
  fit();
  draw();
  kick(1);
}

["show-works", "show-syn", "show-meta", "cat"].forEach(id => {
  document.getElementById(id).onchange = relayout;
});
document.getElementById("show-physics").onchange = () => {
  saveSettings();
  if (physicsOn()) kick(1);
  else { alpha = 0; looping = false; draw(); }
};
document.getElementById("names-mode").onchange = () => { saveSettings(); draw(); };
["p-bounce", "p-stiff", "p-charge", "p-spread", "p-settle"].forEach(id => {
  document.getElementById(id).addEventListener("input", () => {
    saveSettings();
    if (physicsOn()) kick(0.4);
  });
});
document.getElementById("p-jiggle").onclick = () => {
  for (const n of sim) {
    n.vx += (Math.random() - 0.5) * 8;
    n.vy += (Math.random() - 0.5) * 8;
  }
  kick(1);
};
document.getElementById("q").addEventListener("input", suggest);
document.getElementById("cf").addEventListener("input", fillList);
document.getElementById("z-in").onclick = () => {
  viewLocked = true;
  view.k = Math.min(MAX_ZOOM, view.k * 1.2);
  draw();
};
document.getElementById("z-out").onclick = () => {
  viewLocked = true;
  view.k = Math.max(MIN_ZOOM, view.k / 1.2);
  draw();
};
document.getElementById("z-fit").onclick = () => { viewLocked = false; fit(); draw(); };

wrap.addEventListener("pointerdown", ev => {
  const xy = localXY(ev);
  const n = hit(xy[0], xy[1]);
  lastPtr = { x: xy[0], y: xy[1] };
  if (n) {
    dragging = n;
    n.vx = 0; n.vy = 0;
    selected = n;
    showDetail(n.id);
    kick(1);
  } else panning = true;
  wrap.classList.add("drag");
  wrap.setPointerCapture(ev.pointerId);
});
wrap.addEventListener("pointermove", ev => {
  const xy = localXY(ev);
  if (dragging && lastPtr) {
    const wxy = toWorld(xy[0], xy[1]);
    dragging.vx = wxy[0] - dragging.x;
    dragging.vy = wxy[1] - dragging.y;
    dragging.x = wxy[0];
    dragging.y = wxy[1];
    kick(1);
  } else if (panning && lastPtr) {
    viewLocked = true;
    view.x += xy[0] - lastPtr.x; view.y += xy[1] - lastPtr.y;
    draw();
  } else {
    hover = hit(xy[0], xy[1]);
    wrap.style.cursor = hover ? "pointer" : "grab";
    const hid = hover ? hover.id : null;
    if (hid !== lastHoverId) { lastHoverId = hid; draw(); }
  }
  lastPtr = { x: xy[0], y: xy[1] };
});
wrap.addEventListener("pointerup", () => {
  if (dragging) kick(1);
  dragging = null; panning = false; lastPtr = null;
  wrap.classList.remove("drag");
});
wrap.addEventListener("wheel", ev => {
  ev.preventDefault();
  const xy = localXY(ev);
  const factor = ev.deltaY < 0 ? 1.12 : 0.89;
  const nk = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.k * factor));
  viewLocked = true;
  view.x = xy[0] - (xy[0] - view.x) * (nk / view.k);
  view.y = xy[1] - (xy[1] - view.y) * (nk / view.k);
  view.k = nk;
  draw();
}, { passive: false });
window.addEventListener("resize", () => { if (!viewLocked) fit(); draw(); });
document.addEventListener("keydown", ev => {
  if (ev.key === "/" && document.activeElement.tagName !== "INPUT") {
    ev.preventDefault(); document.getElementById("q").focus();
  }
});
fillList();
loadSettings();
relayout();
</script>
</body>
</html>

"""
