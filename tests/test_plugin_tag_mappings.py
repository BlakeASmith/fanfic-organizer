from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN_MAPPINGS = (
    Path(__file__).resolve().parents[1] / "calibre-plugin" / "tag_mappings.py"
)


def load_plugin_mappings():
    spec = importlib.util.spec_from_file_location(
        "ao3_plugin_tag_mappings", PLUGIN_MAPPINGS
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_mappings_add_argv():
    mod = load_plugin_mappings()
    argv = mod.build_mappings_add_argv(
        match="tag_ci",
        values="Jegulus",
        action="keep_separate",
        collections="Jegulus",
        stop=True,
    )
    assert argv[:3] == ["config", "mappings", "add"]
    assert "--values" in argv and "Jegulus" in argv
    assert "--stop" in argv
    assert argv[argv.index("--collection") + 1] == "Jegulus"


def test_build_mappings_set_and_move_argv():
    mod = load_plugin_mappings()
    argv = mod.build_mappings_set_argv(
        "keep_separate-jegulus",
        match="tag_ci",
        values="Jegulus",
        action="keep_separate",
        stop=True,
    )
    assert argv[:4] == ["config", "mappings", "set", "keep_separate-jegulus"]
    assert mod.build_mappings_move_argv("x", up=True)[-1] == "--up"
    assert mod.build_mappings_remove_argv("x") == ["config", "mappings", "remove", "x"]


def test_plugin_match_choices_match_library():
    from ao3kit.tags.mappings import ACTION_CHOICES, MATCH_CHOICES

    mod = load_plugin_mappings()
    assert list(mod.MATCH_CHOICES) == list(MATCH_CHOICES)
    assert list(mod.ACTION_CHOICES) == list(ACTION_CHOICES)


def test_format_when_then_and_preview():
    mod = load_plugin_mappings()
    row = {
        "match": "tag_ci",
        "values": ["Jegulus"],
        "action": "keep_separate",
        "collections": ["Jegulus"],
    }
    assert "is exactly" in mod.format_when(row)
    assert "Jegulus" in mod.format_when(row)
    assert mod.format_then(row) == "Keep this spelling"
    assert (
        mod.format_rule_summary(row)
        == 'is exactly “Jegulus” · Keep this spelling → Jegulus'
    )
    tag_only = {
        "match": "mentions",
        "values": ["WIP"],
        "action": "drop",
        "collections": [],
    }
    assert mod.format_rule_summary(tag_only) == 'contains “WIP” · Remove it'
    assert mod.row_has_collection(row) is True
    assert mod.row_has_collection(tag_only) is False
    assert mod.row_has_collection({**row, "enabled": False}) is False
    text = mod.format_preview(
        {
            "original": "Jegulus",
            "canonical": "Regulus Black/James Potter",
            "status": "synonym",
            "ao3_changed": True,
            "mapped": "Jegulus",
            "dropped": False,
            "mapping_action": "keep_separate",
            "mapping_rule": "keep-jegulus",
            "collections": ["Jegulus"],
            "applied_rules": ["keep-jegulus"],
        }
    )
    assert "Regulus Black/James Potter" in text
    assert "AO3's usual name" in text
    assert "keep_separate" not in text
    assert "keep-jegulus" not in text
    assert "Goes in collection: Jegulus" in text
    text_meta = mod.format_preview(
        {
            "original": "Spider-Man - All Media Types",
            "canonical": "Spider-Man - All Media Types",
            "status": "canonical",
            "mapped": "Spider-Man - All Media Types",
            "metatags": ["Marvel"],
        }
    )
    assert "Marvel" in text_meta
    assert "Added to Fandom" in text_meta or "AO3 also adds to Fandom" in text_meta
    dropped = mod.format_preview(
        {
            "original": "WIP",
            "canonical": "WIP",
            "dropped": True,
            "mapped": None,
            "collections": [],
        }
    )
    assert "removed" in dropped
    assert "(none)" in dropped
