"""Tests for the tag-relationship graph built from the SQLite cache."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from ao3kit.tags.cache import TagCache
from ao3kit.tags.graph import (
    GraphWork,
    build_tag_graph,
    edge_weight,
    infer_format,
    main,
    render_dot,
    render_html,
    works_from_records,
    write_graph,
)
from ao3kit.tags.metadata import TagProfile, TagRef

PLUGIN_SCRAPE_RUN = Path(__file__).resolve().parents[1] / "calibre-plugin" / "scrape_run.py"


def _load_plugin(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _profile(
    name: str,
    *,
    canonical: bool = False,
    synonym_of: str | None = None,
    synonyms: list[str] | None = None,
    metatags: list[str] | None = None,
    category: str = "Additional Tags",
) -> TagProfile:
    return TagProfile(
        name=name,
        url=f"https://archiveofourown.org/tags/{name}",
        category=category,  # type: ignore[arg-type]
        canonical=canonical and synonym_of is None,
        filterable=canonical or synonym_of is not None,
        description="",
        synonym_of=(
            TagRef(name=synonym_of, url=f"https://archiveofourown.org/tags/{synonym_of}")
            if synonym_of
            else None
        ),
        synonyms=[
            TagRef(name=item, url=f"https://archiveofourown.org/tags/{item}")
            for item in (synonyms or [])
        ],
        metatags=[
            TagRef(name=item, url=f"https://archiveofourown.org/tags/{item}")
            for item in (metatags or [])
        ],
    )


def _cache(tmp_path: Path) -> TagCache:
    cache = TagCache.load(tmp_path / "ao3_tag_cache.sqlite", ttl_days=0)
    cache.remember_profile(
        _profile(
            "Sirius Black/Remus Lupin",
            canonical=True,
            category="Relationship",
            synonyms=["wolfstar", "Wolfstar"],
        )
    )
    cache.remember_profile(
        _profile("wolfstar", synonym_of="Sirius Black/Remus Lupin", category="Relationship")
    )
    cache.remember_profile(
        _profile(
            "Spider-Man - All Media Types",
            canonical=True,
            category="Fandom",
            synonyms=["spiderman"],
            metatags=["Marvel"],
        )
    )
    cache.remember_profile(
        _profile(
            "Marvel",
            canonical=True,
            category="Fandom",
            metatags=["Comics"],
        )
    )
    cache.remember_profile(
        _profile("Fluff", canonical=True, category="Additional Tags")
    )
    return cache


def _edge_set(graph) -> set[tuple[str, str, str]]:
    return {(edge.source, edge.target, edge.kind) for edge in graph.edges}


def test_seed_includes_synonym_and_canonical(tmp_path: Path):
    cache = _cache(tmp_path)
    graph = build_tag_graph(cache, ["wolfstar"], synonyms="seed")
    names = {node.name for node in graph.nodes}
    assert "wolfstar" in names
    assert "Sirius Black/Remus Lupin" in names
    assert "Wolfstar" not in names
    assert ("wolfstar", "Sirius Black/Remus Lupin", "synonym") in _edge_set(graph)
    wolf = next(node for node in graph.nodes if node.name == "wolfstar")
    canon = next(
        node for node in graph.nodes if node.name == "Sirius Black/Remus Lupin"
    )
    assert wolf.in_seed is True
    assert canon.in_seed is False
    assert wolf.status == "synonym"
    assert canon.status == "canonical"


def test_all_synonyms_adds_cached_aliases(tmp_path: Path):
    cache = _cache(tmp_path)
    graph = build_tag_graph(cache, ["wolfstar"], synonyms="all")
    names = {node.name for node in graph.nodes}
    assert "Wolfstar" in names
    assert ("Wolfstar", "Sirius Black/Remus Lupin", "synonym") in _edge_set(graph)


def test_no_synonyms_skips_alias_edges(tmp_path: Path):
    cache = _cache(tmp_path)
    graph = build_tag_graph(cache, ["wolfstar"], synonyms="none")
    assert all(edge.kind != "synonym" for edge in graph.edges)
    assert any(node.name == "wolfstar" for node in graph.nodes)


def test_metatag_chain_pulls_ancestors_not_in_seed(tmp_path: Path):
    cache = _cache(tmp_path)
    graph = build_tag_graph(
        cache, ["Spider-Man - All Media Types"], synonyms="seed"
    )
    names = {node.name for node in graph.nodes}
    assert "Marvel" in names
    assert "Comics" in names
    assert "Fluff" not in names
    edges = _edge_set(graph)
    assert ("Spider-Man - All Media Types", "Marvel", "metatag") in edges
    assert ("Marvel", "Comics", "metatag") in edges
    marvel = next(node for node in graph.nodes if node.name == "Marvel")
    assert marvel.in_seed is False
    spider = next(
        node for node in graph.nodes if node.name == "Spider-Man - All Media Types"
    )
    assert spider.rank > marvel.rank
    comps = [c for c in graph.components if c.size > 1]
    assert comps
    assert comps[0].hub == "Marvel"
    assert comps[0].title == "Marvel"


def test_uncached_seed_tag_is_missing_node(tmp_path: Path):
    cache = _cache(tmp_path)
    graph = build_tag_graph(cache, ["Not a Real Tag", "Fluff"])
    missing = next(node for node in graph.nodes if node.name == "Not a Real Tag")
    assert missing.status == "missing"
    assert missing.in_seed is True
    assert graph.missing_seed == 1
    assert graph.cached_seed == 1
    assert graph.seed_count == 2


def test_no_metatags_flag_omits_parent_edges(tmp_path: Path):
    cache = _cache(tmp_path)
    graph = build_tag_graph(
        cache,
        ["Spider-Man - All Media Types"],
        include_metatags=False,
    )
    assert all(edge.kind != "metatag" for edge in graph.edges)
    assert "Marvel" not in {node.name for node in graph.nodes}


def test_empty_seed_uses_cached_canonicals(tmp_path: Path):
    cache = _cache(tmp_path)
    graph = build_tag_graph(cache, None, synonyms="seed")
    names = {node.name for node in graph.nodes}
    assert "Fluff" in names
    assert "Marvel" in names
    assert "wolfstar" not in names


def test_html_embeds_graph_and_escapes_script(tmp_path: Path):
    cache = TagCache.load(tmp_path / "tags.sqlite", ttl_days=0)
    cache.remember_profile(
        _profile(
            "</script><script>alert(1)",
            canonical=True,
            metatags=["Marvel"],
        )
    )
    cache.remember_profile(_profile("Marvel", canonical=True, category="Fandom"))
    graph = build_tag_graph(cache, ["</script><script>alert(1)"])
    html = render_html(graph)
    assert "<script>alert(1)" not in html
    assert "\\u003c/script\\u003e" in html or "\\u003cscript\\u003e" in html
    assert "Marvel" in html
    assert "AO3 tag graph" in html
    assert "Scroll to zoom" in html
    assert "show-works" in html
    assert "show-physics" in html
    assert "names-mode" in html
    assert "p-bounce" in html
    assert "p-spread" in html
    assert "MIN_ZOOM" in html
    assert "roundBlob" in html
    assert "zoomedIn" not in html
    assert "a.r + b.r" in html
    assert "pullOnly" in html
    assert "perRing" in html


def test_edge_weight_keeps_busy_work_spokes_strong():
    assert edge_weight("synonym") == 2.0
    assert edge_weight("metatag") == 0.7
    assert edge_weight("work", hub_degree=1) == 1.0
    busy = edge_weight("work", hub_degree=80)
    assert busy > edge_weight("work", hub_degree=4)
    assert busy > 1.0


def test_busy_work_edges_do_not_collapse(tmp_path: Path):
    cache = _cache(tmp_path)
    tags = tuple(f"tag-{i}" for i in range(40))
    graph = build_tag_graph(cache, None, works=[GraphWork("1", "Busy", tags)])
    weights = [edge.weight for edge in graph.edges if edge.kind == "work"]
    assert len(weights) == 40
    assert min(weights) >= 1.0


def test_works_are_nodes_linked_to_every_tag(tmp_path: Path):
    cache = _cache(tmp_path)
    works = [
        GraphWork("1", "Alpha", ("wolfstar", "Fluff")),
        GraphWork("2", "Beta", ("Fluff", "Not Cached")),
    ]
    graph = build_tag_graph(cache, None, works=works)
    ids = {node.id for node in graph.nodes}
    assert "work:1" in ids
    assert "work:2" in ids
    edges = _edge_set(graph)
    assert ("work:1", "wolfstar", "work") in edges
    assert ("work:1", "Fluff", "work") in edges
    assert ("work:2", "Fluff", "work") in edges
    assert ("work:2", "Not Cached", "work") in edges
    fluff = next(node for node in graph.nodes if node.id == "Fluff")
    assert fluff.degree >= 2
    assert graph.work_count == 2
    assert graph.work_edges == 4
    work_edge = next(edge for edge in graph.edges if edge.kind == "work")
    assert work_edge.weight > 0.8
    syn = next(edge for edge in graph.edges if edge.kind == "synonym")
    assert syn.weight == 2.0
    alpha = next(node for node in graph.nodes if node.id == "work:1")
    assert alpha.kind == "work"
    assert alpha.name == "Alpha"


def test_works_from_records_collects_all_tag_fields():
    works = works_from_records(
        [
            {
                "work_id": "50448730",
                "title": "Clandestine",
                "url": "https://archiveofourown.org/works/50448730",
                "tags": ["Fluff", "Completed"],
                "fandoms": ["Harry Potter - J. K. Rowling"],
                "relationships": ["wolfstar"],
                "characters": ["Remus Lupin"],
            }
        ]
    )
    assert len(works) == 1
    assert works[0].work_id == "50448730"
    assert "Completed" not in works[0].tags
    assert set(works[0].tags) == {
        "Fluff",
        "Harry Potter - J. K. Rowling",
        "wolfstar",
        "Remus Lupin",
    }


def test_works_from_records_skips_non_ao3_books():
    works = works_from_records(
        [
            {
                "title": "CBC Canada [Fri, 21 Aug 2026]",
                "tags": ["News", "CBC Canada"],
                "calibre_uuid": "da06bfcd-74ad-4da6-9f0a-7d6e962e4ad7",
            },
            {
                "url": "https://archiveofourown.org/works/50448730",
                "title": "Clandestine",
                "tags": ["Fluff"],
            },
        ]
    )
    assert [work.work_id for work in works] == ["50448730"]


def test_cli_jsonl_includes_work_nodes(tmp_path: Path, capsys):
    cache = _cache(tmp_path)
    cache_path = tmp_path / "ao3_tag_cache.sqlite"
    cache.close()
    jsonl = tmp_path / "works.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "work_id": "1",
                "title": "Clandestine",
                "tags": ["wolfstar", "Fluff"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "graph.html"
    code = main(
        [
            "--cache",
            str(cache_path),
            "--cache-ttl-days",
            "0",
            "--jsonl",
            str(jsonl),
            "-o",
            str(out),
        ]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "Clandestine" in text
    assert "work:1" in text
    captured = capsys.readouterr()
    assert "1 works" in captured.out


def test_dot_and_json_round_trip(tmp_path: Path):
    cache = _cache(tmp_path)
    graph = build_tag_graph(cache, ["wolfstar", "Fluff"])
    payload = json.loads(write_graph(graph, None, "json"))
    assert payload["seed_count"] == 2
    assert any(node["name"] == "wolfstar" for node in payload["nodes"])
    dot = render_dot(graph)
    assert "wolfstar" in dot
    assert "synonym" in dot
    assert infer_format(Path("out.html"), None) == "html"
    assert infer_format(Path("out.json"), None) == "json"
    assert infer_format(Path("out.dot"), None) == "dot"


def test_cli_writes_html(tmp_path: Path, capsys):
    cache = _cache(tmp_path)
    cache_path = tmp_path / "ao3_tag_cache.sqlite"
    cache.close()
    names = tmp_path / "names.txt"
    names.write_text("wolfstar\nFluff\nUncached Tag\n", encoding="utf-8")
    out = tmp_path / "graph.html"
    code = main(
        [
            "--cache",
            str(cache_path),
            "--cache-ttl-days",
            "0",
            "--names-file",
            str(names),
            "-o",
            str(out),
        ]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "wolfstar" in text
    assert "Fluff" in text
    captured = capsys.readouterr()
    assert str(out) in captured.out
    assert "uncached" in captured.out.lower()


def test_cli_missing_cache_errors(tmp_path: Path, capsys):
    code = main(["--cache", str(tmp_path / "missing.sqlite"), "-o", str(tmp_path / "x.html")])
    assert code == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_plugin_graph_argv():
    mod = _load_plugin(PLUGIN_SCRAPE_RUN, "ao3_scrape_run_graph")
    argv = mod.build_tag_graph_argv("/tmp/names.txt", "/tmp/tag-graph.html")
    assert argv[:2] == ["tags", "graph"]
    assert argv[argv.index("--names-file") + 1] == "/tmp/names.txt"
    assert argv[argv.index("-o") + 1] == "/tmp/tag-graph.html"
    assert "--open" in argv
    argv2 = mod.build_tag_graph_argv(
        "/tmp/names.txt", "/tmp/tag-graph.html", open_browser=False
    )
    assert "--open" not in argv2
    argv3 = mod.build_tag_graph_argv(
        None, "/tmp/tag-graph.html", jsonl="/tmp/works.jsonl", open_browser=False
    )
    assert argv3[argv3.index("--jsonl") + 1] == "/tmp/works.jsonl"
    assert "--names-file" not in argv3
