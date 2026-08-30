"""Collection membership computed from rules and per-work pins."""

from __future__ import annotations

from pathlib import Path

from ao3kit.config import init_user_config, load_user_config
from ao3kit.tags.collections import (
    CollectionRule,
    capture_manual_adds,
    collection_rule_from_form,
    compute_collections,
    load_collection_rules,
    overlay_record_collections,
    recompute_records,
    save_collection_rules,
    upsert_pin,
)


def test_tag_contains_rule_matches_substring_and_canonical_names():
    rule = CollectionRule(
        id="river",
        match="mentions",
        values=["River Song"],
        collections=["River Song"],
    )
    hit = {"tags": ["River Song - Freeform"], "fandoms": []}
    miss = {"tags": ["Fluff"], "fandoms": []}
    synonym = {
        "tags": ["Melody Pond"],
        "cleaned": {
            "simplified": ["River Song"],
            "tags": [{"original": "Melody Pond", "mapped": "River Song"}],
        },
    }
    assert rule.matches(hit)
    assert not rule.matches(miss)
    assert rule.matches(synonym)
    assert compute_collections(synonym, [rule]).names() == ["River Song"]


def test_explain_record_collections_lists_rule_sources():
    rule = CollectionRule(
        id="river",
        match="mentions",
        values=["River Song"],
        collections=["River Song"],
    )
    never = CollectionRule(
        id="never-fluff",
        match="work_id",
        values=["9"],
        collections=["Fluff"],
        mode="exclude",
        pin=True,
    )
    fluff = CollectionRule(
        id="fluff",
        match="mentions",
        values=["Fluff"],
        collections=["Fluff"],
    )
    record = {
        "work_id": "9",
        "title": "A Book",
        "tags": ["River Song - Freeform", "Fluff"],
        "current_collections": ["River Song", "Fluff", "Manual"],
        "cleaned": {"collections": {"Engine": ["raw"]}},
    }
    from ao3kit.tags.collections import explain_record_collections

    payload = explain_record_collections(record, [rule, fluff, never])
    by_name = {item["name"]: item for item in payload["memberships"]}
    assert by_name["River Song"]["status"] == "in"
    assert by_name["River Song"]["shared_includes"][0]["id"] == "river"
    assert by_name["Fluff"]["status"] == "excluded"
    assert by_name["Fluff"]["exclude_pins"][0]["id"] == "never-fluff"
    assert by_name["Manual"]["status"] == "unexplained"
    assert by_name["Engine"]["status"] == "pending"
    assert by_name["Engine"]["engine_sources"] == ["raw"]


def test_remove_pin_drops_matching_pin():
    from ao3kit.tags.collections import remove_pin, upsert_pin

    rules, pin = upsert_pin([], collection="Jegulus", work_id="9")
    assert pin is not None
    rules, extra = upsert_pin(rules, collection="Jegulus", work_id="9", mode="exclude")
    assert extra is not None
    kept, removed = remove_pin(rules, collection="Jegulus", work_id="9", mode="include")
    assert [item.id for item in removed] == [pin.id]
    assert any(item.mode == "exclude" for item in kept)
    kept, removed = remove_pin(kept, collection="Jegulus", work_id="9")
    assert len(removed) == 1
    assert kept == []
    include = CollectionRule(
        id="pin-jegulus",
        match="work_id",
        values=["9"],
        collections=["Jegulus"],
        pin=True,
    )
    exclude = CollectionRule(
        id="never-fluff",
        match="work_id",
        values=["9"],
        collections=["Fluff"],
        mode="exclude",
    )
    tag = CollectionRule(
        id="fluff-tag",
        match="mentions",
        values=["Fluff"],
        collections=["Fluff"],
    )
    record = {"work_id": "9", "tags": ["Fluff"], "url": "https://archiveofourown.org/works/9"}
    names = compute_collections(record, [tag, include, exclude]).names()
    assert names == ["Jegulus"]


def test_author_and_fandom_matches():
    author = CollectionRule(
        id="by-avocadomoon",
        match="author_ci",
        values=["avocadomoon"],
        collections=["Avocado"],
    )
    fandom = CollectionRule(
        id="pitt",
        match="fandom_mentions",
        values=["The Pitt"],
        collections=["The Pitt"],
    )
    record = {
        "authors": ["avocadomoon"],
        "fandoms": ["The Pitt (TV)"],
        "tags": ["Fluff"],
    }
    names = compute_collections(record, [author, fandom]).names()
    assert names == ["Avocado", "The Pitt"]


def test_overlay_unions_engine_collections_and_applies_excludes():
    record = {
        "work_id": "1",
        "tags": ["Fluff"],
        "cleaned": {"collections": {"FromEngine": ["raw"]}},
    }
    pin = CollectionRule(
        id="pin-1",
        match="work_id",
        values=["1"],
        collections=["Pinned"],
        pin=True,
    )
    never = CollectionRule(
        id="never-engine",
        match="work_id",
        values=["1"],
        collections=["FromEngine"],
        mode="exclude",
    )
    overlayed = overlay_record_collections(record, [pin, never])
    assert list(overlayed["cleaned"]["collections"]) == ["Pinned"]


def test_capture_manual_add_becomes_pin():
    shared = CollectionRule(
        id="river",
        match="mentions",
        values=["River Song"],
        collections=["River Song"],
    )
    record = {
        "work_id": "42",
        "title": "A Book",
        "tags": ["River Song"],
        "cleaned": {"collections": {"River Song": []}},
    }
    rules, added = capture_manual_adds(
        [shared], record, ["River Song", "Jegulus"]
    )
    assert len(added) == 1
    pin = added[0]
    assert pin.pin is True
    assert pin.match == "work_id"
    assert pin.values == ["42"]
    assert pin.collections == ["Jegulus"]
    names = compute_collections(record, rules).names()
    assert "Jegulus" in names
    assert "River Song" in names


def test_upsert_pin_is_idempotent():
    rules, first = upsert_pin([], collection="Jegulus", work_id="9")
    assert first is not None
    rules, second = upsert_pin(rules, collection="Jegulus", work_id="9")
    assert second is None
    assert len(rules) == 1


def test_recompute_saves_pin_and_replaces_collections():
    shared = CollectionRule(
        id="river",
        match="mentions",
        values=["River Song"],
        collections=["River Song"],
    )
    record = {
        "work_id": "7",
        "title": "T",
        "tags": ["Melody Pond"],
        "cleaned": {
            "simplified": ["River Song"],
            "collections": {"River Song": ["Melody Pond"]},
        },
        "current_collections": ["River Song", "Manual"],
        "calibre_uuid": "uuid-7",
    }
    out, rules, pins = recompute_records([record], [shared], remember_adds=True)
    assert len(pins) == 1
    assert pins[0].collections == ["Manual"]
    assert list(out[0]["cleaned"]["collections"]) == ["River Song", "Manual"]


def test_form_infers_collection_from_match_text():
    rule = collection_rule_from_form(
        match="mentions",
        values="River Song",
        collections="",
        existing_ids=[],
    )
    assert rule.collections == ["River Song"]
    assert rule.mode == "include"


def test_and_fandom_and_word_count():
    from ao3kit.tags.collections import CollectionCondition

    rule = CollectionRule(
        id="big-hp",
        collections=["Big Harry Potter"],
        all=[
            CollectionCondition(field="fandom", op="contains", values=["Harry Potter"]),
            CollectionCondition(field="words", op="gte", value=200_000),
        ],
    )
    hit = {
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "metadata": {"words": 250_000},
    }
    miss_words = {
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "metadata": {"words": 1_000},
    }
    miss_fandom = {"fandoms": ["Marvel"], "metadata": {"words": 250_000}}
    assert rule.matches(hit)
    assert not rule.matches(miss_words)
    assert not rule.matches(miss_fandom)
    assert "AND" in rule.when_display()


def test_title_wildcard_regex_and_casefold():
    from ao3kit.tags.collections import CollectionCondition

    wildcard = CollectionRule(
        id="spider",
        collections=["Spidey"],
        all=[CollectionCondition(field="title", op="wildcard", values=["Spider*Man"])],
    )
    assert wildcard.matches({"title": "Spider-Man Returns"})
    assert not wildcard.matches({"title": "Batman"})

    regex = CollectionRule(
        id="re",
        collections=["X"],
        all=[CollectionCondition(field="summary", op="regex", values=[r"time\s+travel"])],
    )
    assert regex.matches({"summary": "A story about time travel and tea."})
    assert not regex.matches({"summary": "No spoilers here."})

    sensitive = CollectionRule(
        id="case",
        collections=["X"],
        all=[
            CollectionCondition(
                field="title", op="contains", values=["Foo"], casefold=False
            )
        ],
    )
    assert sensitive.matches({"title": "Foo bar"})
    assert not sensitive.matches({"title": "foo bar"})

    bad = CollectionRule(
        id="bad",
        collections=["X"],
        all=[CollectionCondition(field="title", op="regex", values=["("])],
    )
    assert not bad.matches({"title": "anything"})


def test_relationship_character_series_complete():
    from ao3kit.tags.collections import CollectionCondition

    ship = CollectionRule(
        id="ship",
        collections=["Drarry"],
        all=[
            CollectionCondition(
                field="relationship", op="contains", values=["Harry Potter/Draco"]
            )
        ],
    )
    assert ship.matches({"relationships": ["Harry Potter/Draco Malfoy"]})

    character = CollectionRule(
        id="char",
        collections=["River"],
        all=[CollectionCondition(field="character", op="is", values=["River Song"])],
    )
    assert character.matches({"characters": ["River Song"]})

    series = CollectionRule(
        id="ser",
        collections=["Series A"],
        all=[CollectionCondition(field="series", op="contains", values=["Chronicles"])],
    )
    assert series.matches({"series": [{"name": "The Chronicles", "series_id": "1"}]})

    complete = CollectionRule(
        id="done",
        collections=["Finished"],
        all=[CollectionCondition(field="complete", op="is", value=True)],
    )
    assert complete.matches({"metadata": {"chapters": {"is_complete": True}}})
    assert not complete.matches({"metadata": {"chapters": {"is_complete": False}}})


def test_compound_yaml_roundtrip(tmp_path: Path):
    from ao3kit.tags.collections import CollectionCondition

    path = tmp_path / "collections.yaml"
    rule = CollectionRule(
        id="big-hp",
        collections=["Big Harry Potter"],
        all=[
            CollectionCondition(field="fandom", op="contains", values=["Harry Potter"]),
            CollectionCondition(field="words", op="gte", value=200_000),
        ],
    )
    save_collection_rules(path, [rule])
    loaded = load_collection_rules(path)
    assert len(loaded) == 1
    assert len(loaded[0].all) == 2
    assert loaded[0].matches(
        {"fandoms": ["Harry Potter"], "metadata": {"words": 200_000}}
    )


def test_config_cli_when_conditions(tmp_path: Path):
    from ao3kit.config_cli import main

    home = str(tmp_path / "home")
    assert (
        main(
            [
                "--home",
                home,
                "collections",
                "add",
                "--when",
                "fandom:contains:Harry Potter",
                "--when",
                "words:gte:200000",
                "--collection",
                "Big Harry Potter",
            ]
        )
        == 0
    )
    cfg = load_user_config(home=tmp_path / "home")
    rules = cfg.load_collection_rules()
    assert len(rules) == 1
    assert len(rules[0].all) == 2
    assert rules[0].collections == ["Big Harry Potter"]


def test_collection_yaml_roundtrip(tmp_path: Path):
    path = tmp_path / "collections.yaml"
    rows = [
        collection_rule_from_form(
            match="mentions",
            values="River Song",
            collections="River Song",
            existing_ids=[],
        )
    ]
    save_collection_rules(path, rows)
    loaded = load_collection_rules(path)
    assert len(loaded) == 1
    assert loaded[0].match == "mentions"
    assert loaded[0].collections == ["River Song"]


def test_config_cli_collections(tmp_path: Path):
    from ao3kit.config_cli import main

    home = str(tmp_path / "home")
    assert (
        main(
            [
                "--home",
                home,
                "collections",
                "add",
                "--match",
                "mentions",
                "--values",
                "River Song",
                "--collection",
                "River Song",
            ]
        )
        == 0
    )
    cfg = load_user_config(home=tmp_path / "home")
    rules = cfg.load_collection_rules()
    assert len(rules) == 1
    assert rules[0].collections == ["River Song"]
    assert main(["--home", home, "collections", "remove", rules[0].id]) == 0
    assert cfg.load_collection_rules() == []


def test_config_cli_pin(tmp_path: Path):
    from ao3kit.config_cli import main

    home = str(tmp_path / "home")
    assert (
        main(
            [
                "--home",
                home,
                "collections",
                "pin",
                "--work-id",
                "9",
                "--collection",
                "Jegulus",
            ]
        )
        == 0
    )
    cfg = load_user_config(home=tmp_path / "home")
    rules = cfg.load_collection_rules()
    assert len(rules) == 1
    assert rules[0].pin is True
    assert rules[0].match == "work_id"
    assert rules[0].values == ["9"]
    assert rules[0].collections == ["Jegulus"]
    assert (
        main(
            [
                "--home",
                home,
                "collections",
                "pin",
                "--work-id",
                "9",
                "--collection",
                "Jegulus",
            ]
        )
        == 0
    )
    assert len(cfg.load_collection_rules()) == 1
    assert (
        main(
            [
                "--home",
                home,
                "collections",
                "unpin",
                "--work-id",
                "9",
                "--collection",
                "Jegulus",
            ]
        )
        == 0
    )
    assert cfg.load_collection_rules() == []


def test_init_user_config_has_remember_adds_setting(tmp_path: Path):
    cfg = init_user_config(home=tmp_path / "home")
    assert cfg.settings.collections_remember_manual_adds is True


def test_tags_collections_cli_does_not_resolve_tags(tmp_path: Path, monkeypatch):
    import json

    from ao3kit.config_cli import main as config_main
    from ao3kit.tags.metadata import main as tags_main

    home = tmp_path / "home"
    monkeypatch.setenv("AO3KIT_HOME", str(home))
    assert (
        config_main(
            [
                "--home",
                str(home),
                "collections",
                "add",
                "--match",
                "mentions",
                "--values",
                "River Song",
                "--collection",
                "River Song",
            ]
        )
        == 0
    )
    inp = tmp_path / "in.jsonl"
    inp.write_text(
        json.dumps(
            {
                "work_id": "9",
                "title": "T",
                "tags": ["River Song - Freeform"],
                "fandoms": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.jsonl"

    def _no_enrich(*_args, **_kwargs):
        raise AssertionError("tags collections must not run tag enrich")

    class _NoResolver:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("tags collections must not construct TagResolver")

    monkeypatch.setattr("ao3kit.tags.clean.enrich_records", _no_enrich)
    monkeypatch.setattr("ao3kit.tags.metadata.TagResolver", _NoResolver)
    assert (
        tags_main(["collections", "--jsonl", str(inp), "-o", str(out), "--verbose"])
        == 0
    )
    record = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert list(record["cleaned"]["collections"]) == ["River Song"]


def test_tags_collections_explain_cli(tmp_path: Path, monkeypatch):
    import json

    from ao3kit.config_cli import main as config_main
    from ao3kit.tags.metadata import main as tags_main

    home = tmp_path / "home"
    monkeypatch.setenv("AO3KIT_HOME", str(home))
    assert (
        config_main(
            [
                "--home",
                str(home),
                "collections",
                "add",
                "--match",
                "mentions",
                "--values",
                "River Song",
                "--collection",
                "River Song",
            ]
        )
        == 0
    )
    inp = tmp_path / "in.jsonl"
    inp.write_text(
        json.dumps(
            {
                "work_id": "9",
                "title": "T",
                "tags": ["River Song - Freeform"],
                "current_collections": ["River Song", "Manual"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "explain.json"
    assert (
        tags_main(
            ["collections", "--jsonl", str(inp), "-o", str(out), "--explain"]
        )
        == 0
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload) == 1
    by_name = {item["name"]: item for item in payload[0]["memberships"]}
    assert by_name["River Song"]["status"] == "in"
    assert by_name["Manual"]["status"] == "unexplained"
