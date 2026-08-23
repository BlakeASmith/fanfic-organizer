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
    fandom_stem,
    graph_hubs,
    infer_format,
    main,
    render_dot,
    render_html,
    work_cluster_key,
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
    assert "Pin mode" in html
    assert "show-works" in html
    assert "bridges-only" in html
    assert "names-mode" not in html
    assert "labelFocus" in html
    assert "areaLandmarks" in html
    assert "label-hops" in html
    assert "label-degree" in html
    assert "hopIds" in html
    assert "keep.has(a.id) && keep.has(b.id)" in html
    assert "pin-mode" in html
    assert "cluster-mode" in html
    assert "settle-on" in html
    assert "startSettle" in html
    assert "Crossovers" in html
    assert "show-physics" not in html
    assert "p-bounce" not in html
    assert "Bridges" in html
    assert "fillHubs" in html
    assert "MIN_ZOOM" in html


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


def test_layout_puts_bridge_tag_between_fandoms(tmp_path: Path):
    cache = _cache(tmp_path)
    cache.remember_profile(
        _profile("Doctor Who (2005)", canonical=True, category="Fandom")
    )
    graph = build_tag_graph(
        cache,
        None,
        works=[
            GraphWork("1", "Who fic", ("Doctor Who (2005)", "Fluff")),
            GraphWork("2", "Spidey fic", ("Spider-Man - All Media Types", "Fluff")),
        ],
    )
    who = next(node for node in graph.nodes if node.id == "work:1")
    spidey = next(node for node in graph.nodes if node.id == "work:2")
    fluff = next(node for node in graph.nodes if node.id == "Fluff")
    assert who.cluster != spidey.cluster
    spread = ((who.x - spidey.x) ** 2 + (who.y - spidey.y) ** 2) ** 0.5
    mid_x = (who.x + spidey.x) / 2
    mid_y = (who.y + spidey.y) / 2
    to_mid = ((fluff.x - mid_x) ** 2 + (fluff.y - mid_y) ** 2) ** 0.5
    assert spread > 40
    assert to_mid < spread * 0.55
    assert any(hub["id"] == "Fluff" for hub in graph.hubs)
    assert any(hub["works"] >= 2 for hub in graph_hubs(graph.nodes, graph.edges))


def test_crossover_cluster_key_groups_mixed_fandoms(tmp_path: Path):
    cache = _cache(tmp_path)
    cache.remember_profile(
        _profile("Doctor Who (2005)", canonical=True, category="Fandom")
    )
    graph = build_tag_graph(
        cache,
        None,
        works=[
            GraphWork(
                "1",
                "Xover",
                ("Doctor Who (2005)", "Spider-Man - All Media Types", "Fluff"),
            ),
            GraphWork("2", "Who only", ("Doctor Who (2005)", "Fluff")),
        ],
    )
    assert (
        work_cluster_key("work:1", graph.nodes, graph.edges, mode="crossover")
        == "_crossover"
    )
    assert (
        work_cluster_key("work:2", graph.nodes, graph.edges, mode="crossover")
        == "Doctor Who (2005)"
    )
    assert work_cluster_key("work:1", graph.nodes, graph.edges, mode="one") == "_all"


def test_fandom_stem_collapses_related_names():
    assert fandom_stem("Doctor Who (2005)") == "doctor who"
    assert fandom_stem("Doctor Who & Related Fandoms") == "doctor who"
    assert fandom_stem("Harry Potter - J. K. Rowling") == "harry potter"


def test_crossover_treats_related_fandoms_as_one_franchise(tmp_path: Path):
    cache = _cache(tmp_path)
    cache.remember_profile(
        _profile(
            "Doctor Who (2005)",
            canonical=True,
            category="Fandom",
            metatags=["Doctor Who"],
        )
    )
    cache.remember_profile(
        _profile(
            "Doctor Who",
            canonical=True,
            category="Fandom",
            metatags=["Doctor Who & Related Fandoms"],
        )
    )
    cache.remember_profile(
        _profile("Doctor Who & Related Fandoms", canonical=True, category="Fandom")
    )
    cache.remember_profile(
        _profile(
            "Torchwood",
            canonical=True,
            category="Fandom",
            metatags=["Doctor Who & Related Fandoms"],
        )
    )
    cache.remember_profile(
        _profile("Spider-Man - All Media Types", canonical=True, category="Fandom")
    )
    cache.remember_profile(
        _profile(
            "Doctor Who (1963)",
            canonical=True,
            category="Fandom",
            metatags=["Doctor Who"],
        )
    )
    graph = build_tag_graph(
        cache,
        None,
        works=[
            GraphWork(
                "1",
                "Who only",
                (
                    "Doctor Who (2005)",
                    "Doctor Who",
                    "Doctor Who & Related Fandoms",
                    "Fluff",
                ),
            ),
            GraphWork("2", "Torchwood", ("Torchwood", "Doctor Who", "Fluff")),
            GraphWork(
                "3",
                "Xover",
                ("Doctor Who (2005)", "Spider-Man - All Media Types", "Fluff"),
            ),
            GraphWork(
                "4",
                "Classic",
                ("Doctor Who (1963)", "Doctor Who", "Fluff"),
            ),
        ],
    )
    assert (
        work_cluster_key("work:1", graph.nodes, graph.edges, mode="crossover")
        == "Doctor Who"
    )
    assert (
        work_cluster_key("work:2", graph.nodes, graph.edges, mode="crossover")
        == "Doctor Who"
    )
    assert (
        work_cluster_key("work:3", graph.nodes, graph.edges, mode="crossover")
        == "_crossover"
    )
    assert (
        work_cluster_key("work:4", graph.nodes, graph.edges, mode="crossover")
        == "Doctor Who"
    )


def test_layout_is_deterministic(tmp_path: Path):
    cache = _cache(tmp_path)
    works = [GraphWork("1", "Alpha", ("wolfstar", "Fluff"))]
    first = build_tag_graph(cache, None, works=works)
    second = build_tag_graph(cache, None, works=works)
    pos = {(node.id, node.x, node.y) for node in first.nodes}
    pos2 = {(node.id, node.x, node.y) for node in second.nodes}
    assert pos == pos2


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
                "tags": ["Fluff", "Completed", "No Archive Warnings Apply"],
                "fandoms": ["Harry Potter - J. K. Rowling"],
                "relationships": ["wolfstar"],
                "characters": ["Remus Lupin"],
            }
        ]
    )
    assert len(works) == 1
    assert works[0].work_id == "50448730"
    assert "Completed" not in works[0].tags
    assert "No Archive Warnings Apply" not in works[0].tags
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
    assert "x" in payload["nodes"][0]
    assert "hubs" in payload
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
    assert mod.live_graph_reload_argv() == ["tags", "graph", "reload"]
    assert mod.build_graph_serve_argv() == ["tags", "graph", "serve", "--no-open"]
    assert "--port" in mod.build_graph_serve_argv(port=8767)


def test_viewer_html_can_load_json_url():
    from ao3kit.tags.graph import render_viewer_html

    html = render_viewer_html(data_url="/graph.json")
    assert "%%" not in html
    assert "/graph.json" in html
    assert "function startGraph" in html
    assert "Reload data" in html
    assert "Find similar" in html
    assert "/similar" in html
    assert "similar-panel" in html
    assert "Search and import" in html
    assert "Fandoms only" in html
    assert "This tag only" in html
    assert "?tag=" in html


def test_load_tag_graph_empty_without_dump(tmp_path: Path):
    from ao3kit.tags.graph import empty_graph_payload, load_tag_graph

    cache = _cache(tmp_path)
    cache_path = tmp_path / "ao3_tag_cache.sqlite"
    cache.close()
    graph = load_tag_graph(
        jsonl_paths=[tmp_path / "missing.jsonl"],
        cache_path=cache_path,
        ttl_days=0,
    )
    assert graph.nodes == []
    assert empty_graph_payload()["nodes"] == []


def test_reload_main_without_server(tmp_path: Path, capsys):
    code = main(["reload", "--stamp", str(tmp_path / "no-server.json")])
    assert code == 2
    assert "no tag graph server" in capsys.readouterr().err.lower()


def test_notify_running_server_posts_rebuild(tmp_path: Path):
    import os
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from ao3kit.tags.graph import notify_running_server

    posted = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            posted.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    stamp = tmp_path / "stamp.json"
    stamp.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "port": port,
                "url": f"http://127.0.0.1:{port}/",
            }
        ),
        encoding="utf-8",
    )
    try:
        url = notify_running_server(stamp_path=stamp, timeout=2)
        assert url == f"http://127.0.0.1:{port}/"
        assert posted == ["/rebuild"]
    finally:
        httpd.shutdown()


def test_queue_similar_command_uses_fandoms(tmp_path: Path):
    from ao3kit.tags.graph_bridge import (
        pending_inbox_count,
        queue_similar_command,
        similar_scrape_options,
    )

    records = [
        {
            "work_id": "592896",
            "title": "Dynasty",
            "fandoms": ["Doctor Who (2005)", "Iron Man (Movies)"],
            "relationships": ["Eleventh Doctor/Amy Pond"],
            "tags": ["Crossover"],
        }
    ]
    options = similar_scrape_options(records, include=["fandoms"])
    assert options["tag_id"] == "Doctor Who (2005)"
    assert "Iron Man (Movies)" in options["other_tag_names"]
    assert "Eleventh Doctor/Amy Pond" not in options["other_tag_names"]
    with_ships = similar_scrape_options(
        records, include=["fandoms", "relationships"]
    )
    assert "Eleventh Doctor/Amy Pond" in with_ships["other_tag_names"]
    only_ship = similar_scrape_options(
        records,
        select={
            "fandoms": ["Doctor Who (2005)"],
            "relationships": ["Eleventh Doctor/Amy Pond"],
            "extra_query": "slow burn",
        },
        sort_column="hits",
        complete="true",
        min_kudos="100",
        max_results="10",
    )
    assert only_ship["tag_id"] == "Doctor Who (2005)"
    assert "Iron Man (Movies)" not in only_ship["other_tag_names"]
    assert "Eleventh Doctor/Amy Pond" in only_ship["other_tag_names"]
    assert only_ship["query"] == "slow burn"
    assert only_ship["sort_column"] == "hits"
    assert only_ship["complete"] == "true"
    assert only_ship["min_kudos"] == "100"
    assert only_ship["max_results"] == "10"
    inbox = tmp_path / "inbox"
    queued = queue_similar_command(
        work_ids=["592896"], records=records, inbox=inbox
    )
    assert queued.get("id")
    assert pending_inbox_count(inbox) == 1
    assert (inbox / f"{queued['id']}.json").is_file()


def test_similar_preview_and_http(tmp_path: Path):
    from ao3kit.tags.graph import handle_similar_http, parse_similar_work_ids
    from ao3kit.tags.graph_bridge import similar_preview, similar_search_is_usable

    records = [
        {
            "work_id": "592896",
            "title": "Dynasty",
            "authors": ["torchwood_house"],
            "fandoms": ["Doctor Who (2005)", "Iron Man (Movies)"],
            "relationships": ["Eleventh Doctor/Amy Pond"],
            "tags": ["Crossover"],
        }
    ]
    preview = similar_preview(records)
    fandoms = {item["name"]: item["on"] for item in preview["fandoms"]}
    assert fandoms["Doctor Who (2005)"] is True
    assert fandoms["Iron Man (Movies)"] is True
    assert preview["authors"][0]["name"] == "torchwood_house"
    assert preview["authors"][0]["on"] is False
    assert preview["relationships"][0]["on"] is False
    assert parse_similar_work_ids(query="work_id=work:592896") == ["592896"]

    jsonl = tmp_path / "works.jsonl"
    jsonl.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")
    inbox = tmp_path / "inbox"
    code, payload = handle_similar_http(
        method="GET",
        path="/similar?work_id=592896",
        body=None,
        jsonl_paths=[jsonl],
        inbox=inbox,
    )
    assert code == 200
    assert payload["ok"] is True
    assert payload["fandoms"][0]["on"] is True

    code, payload = handle_similar_http(
        method="POST",
        path="/similar",
        body={"work_ids": ["592896"], "select": {"fandoms": [], "tags": []}},
        jsonl_paths=[jsonl],
        inbox=inbox,
    )
    assert code == 400
    assert "fandom" in payload["error"].lower()
    assert not inbox.exists() or not list(inbox.glob("*.json"))

    code, payload = handle_similar_http(
        method="POST",
        path="/similar",
        body={
            "work_ids": ["592896"],
            "select": {
                "fandoms": ["Doctor Who (2005)"],
                "relationships": ["Eleventh Doctor/Amy Pond"],
            },
            "sort_column": "comments_count",
            "complete": "false",
            "max_results": "15",
        },
        jsonl_paths=[jsonl],
        inbox=inbox,
    )
    assert code == 200
    queued = json.loads((inbox / f"{payload['id']}.json").read_text(encoding="utf-8"))
    assert queued["options"]["tag_id"] == "Doctor Who (2005)"
    assert "Eleventh Doctor/Amy Pond" in queued["options"]["other_tag_names"]
    assert queued["options"]["sort_column"] == "comments_count"
    assert queued["options"]["complete"] == "false"
    assert similar_search_is_usable(queued["options"])


def test_similar_tag_preview_and_http(tmp_path: Path):
    from ao3kit.tags.graph import handle_similar_http, parse_similar_tag
    from ao3kit.tags.graph_bridge import (
        facet_bucket_for,
        records_for_tag_name,
        select_for_tag,
        similar_preview,
        similar_search_is_usable,
    )

    assert parse_similar_tag(query="tag=Jegulus&category=Relationship") == (
        "Jegulus",
        "Relationship",
    )
    assert facet_bucket_for("Jegulus", "Relationship") == "relationships"
    assert facet_bucket_for("Doctor Who (2005)", "Fandom") == "fandoms"
    assert facet_bucket_for("Slow Burn", "Additional Tags") == "tags"
    chosen = select_for_tag("Jegulus", "Relationship")
    assert chosen.relationships == ["Jegulus"]
    assert chosen.fandoms == []

    records = [
        {
            "work_id": "592896",
            "title": "Dynasty",
            "fandoms": ["Doctor Who (2005)", "Iron Man (Movies)"],
            "relationships": ["Eleventh Doctor/Amy Pond"],
            "tags": ["Crossover"],
        }
    ]
    jsonl = tmp_path / "works.jsonl"
    jsonl.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")
    assert [row["work_id"] for row in records_for_tag_name([jsonl], "Crossover")] == [
        "592896"
    ]
    assert records_for_tag_name([jsonl], "Missing Tag") == []

    inbox = tmp_path / "inbox"
    code, payload = handle_similar_http(
        method="GET",
        path="/similar?tag=Crossover&category=Additional%20Tags",
        body=None,
        jsonl_paths=[jsonl],
        inbox=inbox,
    )
    assert code == 200
    assert payload["ok"] is True
    assert payload["seed"]["kind"] == "tag"
    assert payload["seed"]["name"] == "Crossover"
    assert payload["seed"]["bucket"] == "tags"
    tags = {item["name"]: item["on"] for item in payload["tags"]}
    assert tags["Crossover"] is True
    fandoms = {item["name"]: item["on"] for item in payload["fandoms"]}
    assert fandoms["Doctor Who (2005)"] is False
    assert fandoms["Iron Man (Movies)"] is False

    preview = similar_preview(
        [records[0]], selected=select_for_tag("Crossover", "Additional Tags")
    )
    assert {item["name"]: item["on"] for item in preview["tags"]}["Crossover"] is True

    code, payload = handle_similar_http(
        method="POST",
        path="/similar",
        body={"tag": "Crossover", "category": "Additional Tags"},
        jsonl_paths=[jsonl],
        inbox=inbox,
    )
    assert code == 200
    queued = json.loads((inbox / f"{payload['id']}.json").read_text(encoding="utf-8"))
    assert queued["tag"] == "Crossover"
    assert queued["titles"] == ["Crossover"]
    assert "Crossover" in queued["options"]["other_tag_names"]
    assert queued["options"]["tag_id"] == ""
    assert similar_search_is_usable(queued["options"])

    code, payload = handle_similar_http(
        method="GET",
        path="/similar?tag=Doctor%20Who%20(2005)&category=Fandom",
        body=None,
        jsonl_paths=[jsonl],
        inbox=inbox,
    )
    assert code == 200
    fandoms = {item["name"]: item["on"] for item in payload["fandoms"]}
    assert fandoms["Doctor Who (2005)"] is True
    assert fandoms["Iron Man (Movies)"] is False

    code, payload = handle_similar_http(
        method="POST",
        path="/similar",
        body={
            "tag": "Doctor Who (2005)",
            "category": "Fandom",
            "select": {"fandoms": ["Doctor Who (2005)"]},
        },
        jsonl_paths=[jsonl],
        inbox=inbox,
    )
    assert code == 200
    queued = json.loads((inbox / f"{payload['id']}.json").read_text(encoding="utf-8"))
    assert queued["options"]["tag_id"] == "Doctor Who (2005)"

    code, payload = handle_similar_http(
        method="GET",
        path="/similar?tag=Never%20Seen%20Ship&category=Relationship",
        body=None,
        jsonl_paths=[jsonl],
        inbox=inbox,
    )
    assert code == 200
    ships = {item["name"]: item["on"] for item in payload["relationships"]}
    assert ships["Never Seen Ship"] is True

    code, payload = handle_similar_http(
        method="POST",
        path="/similar",
        body=None,
        jsonl_paths=[jsonl],
        inbox=inbox,
    )
    assert code == 400
    assert "work_id or tag" in payload["error"]


def test_upsert_graph_jsonl_merges_by_work_id(tmp_path: Path):
    live = _load_plugin(
        Path(__file__).resolve().parents[1] / "calibre-plugin" / "graph_live.py",
        "graph_live_upsert",
    )
    path = tmp_path / "tag_graph_works.jsonl"
    added = live.upsert_graph_jsonl(
        path,
        [
            {"work_id": "1", "title": "One", "fandoms": ["DW"], "authors": ["Ann"]},
            {"work_id": "2", "title": "Two"},
        ],
    )
    assert added == 2
    added2 = live.upsert_graph_jsonl(
        path,
        [
            {"work_id": "2", "title": "Two renamed"},
            {"work_id": "3", "title": "Three"},
        ],
    )
    assert added2 == 1
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["work_id"] for row in rows] == ["1", "2", "3"]
    assert rows[0]["authors"] == ["Ann"]
    assert rows[1]["title"] == "Two renamed"
    assert live.read_serve_url(tmp_path) is None

