from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PLUGIN_COLLECTIONS = (
    Path(__file__).resolve().parents[1] / "calibre-plugin" / "collection_rules.py"
)


def load_plugin_collections():
    spec = importlib.util.spec_from_file_location(
        "ao3_plugin_collection_rules", PLUGIN_COLLECTIONS
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_match_and_mode_choices_match_library():
    from ao3kit.tags.collections import (
        FIELD_CHOICES,
        MATCH_CHOICES,
        MODE_CHOICES,
        OP_CHOICES,
    )

    mod = load_plugin_collections()
    assert list(mod.MATCH_CHOICES) == list(MATCH_CHOICES)
    assert list(mod.MODE_CHOICES) == list(MODE_CHOICES)
    assert list(mod.FIELD_CHOICES) == list(FIELD_CHOICES)
    assert list(mod.OP_CHOICES) == list(OP_CHOICES)


def test_build_collections_add_conditions_json():
    mod = load_plugin_collections()
    argv = mod.build_collections_add_argv(
        collections="Big Harry Potter",
        conditions=[
            {"field": "fandom", "op": "contains", "values": ["Harry Potter"]},
            {"field": "words", "op": "gte", "value": 200000},
        ],
    )
    assert argv[:3] == ["config", "collections", "add"]
    assert "--conditions-json" in argv
    payload = json.loads(argv[argv.index("--conditions-json") + 1])
    assert payload[0]["field"] == "fandom"
    assert payload[1]["field"] == "words"


def test_format_when_compound():
    mod = load_plugin_collections()
    row = {
        "all": [
            {"field": "fandom", "op": "contains", "values": ["Harry Potter"]},
            {"field": "words", "op": "gte", "value": 200000},
        ],
        "collections": ["Big Harry Potter"],
        "when": 'fandom contains “Harry Potter” AND word count ≥ 200000',
    }
    assert "AND" in mod.format_when(row)
    assert "Harry Potter" in mod.format_when(row)


def test_build_collections_add_and_pin_argv():
    mod = load_plugin_collections()
    argv = mod.build_collections_add_argv(
        match="mentions",
        values="River Song",
        collections="River Song",
        mode="include",
    )
    assert argv[:3] == ["config", "collections", "add"]
    assert argv[argv.index("--collection") + 1] == "River Song"
    assert "--pin" not in argv

    pin = mod.build_collections_pin_argv(
        collection="Jegulus",
        work_id="9",
        description="A Book",
    )
    assert pin[:3] == ["config", "collections", "pin"]
    assert pin[pin.index("--work-id") + 1] == "9"
    assert pin[pin.index("--collection") + 1] == "Jegulus"
    never = mod.build_collections_pin_argv(
        collection="Fluff",
        uuid="abc",
        exclude=True,
    )
    assert "--exclude" in never
    assert never[never.index("--uuid") + 1] == "abc"


def test_format_when_kind_and_summary():
    mod = load_plugin_collections()
    row = {
        "match": "mentions",
        "values": ["River Song"],
        "collections": ["River Song"],
        "mode": "include",
        "when": 'tag contains “River Song”',
    }
    assert "River Song" in mod.format_when(row)
    assert mod.format_collection(row) == "River Song"
    assert mod.format_kind(row) == "Rule"
    pin = {
        "match": "work_id",
        "values": ["9"],
        "collections": ["Jegulus"],
        "pin": True,
        "description": "A Book",
        "mode": "include",
    }
    assert "always this work" in mod.format_when(pin)
    assert mod.format_kind(pin) == "Always this work"
    never = {**row, "mode": "exclude", "when": ""}
    assert mod.format_kind(never) == "Never"
    assert "→" in mod.format_rule_summary(row)


def test_build_collections_unpin_argv():
    mod = load_plugin_collections()
    argv = mod.build_collections_unpin_argv(collection="Jegulus", work_id="9")
    assert argv[:3] == ["config", "collections", "unpin"]
    assert argv[argv.index("--work-id") + 1] == "9"
    assert "--exclude" not in argv
    assert "--all-modes" not in argv
    never = mod.build_collections_unpin_argv(
        collection="Fluff", uuid="abc", exclude=True
    )
    assert "--exclude" in never
    assert never[never.index("--uuid") + 1] == "abc"
    both = mod.build_collections_unpin_argv(
        collection="Jegulus", work_id="9", all_modes=True
    )
    assert "--all-modes" in both
    assert "--exclude" not in both


def test_flatten_explain_rows_and_why_text():
    mod = load_plugin_collections()
    books = [
        {
            "title": "Work A",
            "work_id": "1",
            "book_id": 9,
            "current": ["River Song", "Manual"],
            "computed": ["River Song"],
            "memberships": [
                {
                    "name": "River Song",
                    "status": "in",
                    "includes": [
                        {
                            "when": "tag contains “River Song”",
                            "pin": False,
                        }
                    ],
                    "excludes": [],
                    "include_pins": [],
                    "exclude_pins": [],
                    "engine_sources": [],
                },
                {
                    "name": "Manual",
                    "status": "unexplained",
                    "includes": [],
                    "excludes": [],
                    "include_pins": [],
                    "exclude_pins": [],
                    "engine_sources": [],
                },
            ],
        }
    ]
    assert mod.collection_names_from_explain(books) == ["Manual", "River Song"]
    rows = mod.flatten_explain_rows(books)
    assert [row["name"] for row in rows] == ["River Song", "Manual"]
    assert rows[0]["title"] == "Work A"
    assert rows[0]["book_id"] == 9
    assert mod.format_membership_status("pending") == "Will be added"
    assert "tag contains" in mod.format_membership_why(rows[0])
    assert "on the book with no matching rule" in mod.format_membership_why(rows[1])
    filtered = mod.flatten_explain_rows(books, "Fluff")
    assert len(filtered) == 1
    assert filtered[0]["name"] == "Fluff"
    assert filtered[0]["status"] == "out"
    assert "no matching rule" in mod.format_membership_why(filtered[0])
    empty = mod.empty_membership("")
    assert "not in any collection yet" in mod.format_membership_why(empty)


def test_merge_collection_names_from_rules_and_explain():
    mod = load_plugin_collections()
    rules = [
        {"collections": ["Jegulus", "Fluff"]},
        {"collections": "Fluff"},
        {"collections": ["  ", "River Song"]},
    ]
    assert mod.collection_names_from_rules(rules) == ["Fluff", "Jegulus", "River Song"]
    merged = mod.merge_collection_names(
        ["Jegulus"],
        ["jegulus", "Manual"],
        None,
        "  extra  ",
    )
    assert merged == ["extra", "Jegulus", "Manual"]
