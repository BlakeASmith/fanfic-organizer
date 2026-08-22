"""Declarative extra tag mappings on top of AO3 canonical resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from ao3kit.config import init_user_config, load_user_config
from ao3kit.tags.mappings import (
    TagMapping,
    mapping_from_form,
    merge_mapping_rules,
    move_mapping,
    preview_tag,
    remove_mapping,
    save_mappings,
    toggle_mapping,
)
from ao3kit.tags.metadata import ResolvedTag, TagResolver
from ao3kit.tags.rules import MapToRule, TagRulesConfig, TagRulesEngine


def _resolver_with(*resolved: ResolvedTag) -> TagResolver:
    resolver = TagResolver(
        session=object(), delay=0, owns_session=False, cache_path=None, persist=False
    )

    def fake_resolve_one(name: str) -> ResolvedTag:
        for item in resolved:
            if item.original == name:
                return item
        return ResolvedTag(
            original=name, resolved=name, status="unmarked", changed=False
        )

    resolver.resolve_one = fake_resolve_one  # type: ignore[method-assign]
    return resolver


def test_keep_separate_mapping_beats_canonical():
    mapping = TagMapping(
        id="keep-jegulus",
        match="tag_ci",
        values=["Jegulus"],
        action="keep_separate",
        collections=["Jegulus"],
        stop=True,
    )
    config = merge_mapping_rules(TagRulesConfig(resolve_canonical=True), [mapping])
    resolver = _resolver_with(
        ResolvedTag(
            original="Jegulus",
            resolved="Regulus Black/James Potter",
            status="synonym",
            changed=True,
        )
    )
    item = TagRulesEngine(config, resolver).apply_one("Jegulus")
    assert item.mapped == "Jegulus"
    assert item.mapping_action == "keep_separate"
    assert item.collections == ["Jegulus"]


def test_map_to_and_drop_mappings():
    mappings = [
        TagMapping(
            id="melody-to-river",
            match="tag_ci",
            values=["Melody Pond"],
            action="map_to",
            map_to="River Song",
            collections=["River Song"],
        ),
        TagMapping(
            id="drop-tbd",
            match="tag_ci",
            values=["tbd"],
            action="drop",
        ),
    ]
    config = merge_mapping_rules(TagRulesConfig(resolve_canonical=True), mappings)
    resolver = _resolver_with(
        ResolvedTag(
            original="Melody Pond",
            resolved="Melody Pond",
            status="canonical",
            changed=False,
        ),
        ResolvedTag(original="tbd", resolved="tbd", status="unmarked", changed=False),
        ResolvedTag(original="Fluff", resolved="Fluff", status="canonical", changed=False),
    )
    result = TagRulesEngine(config, resolver).apply(["Melody Pond", "tbd", "Fluff"])
    assert result.simplified == ["River Song", "Fluff"]
    assert "tbd" in result.dropped


def test_ui_mappings_run_before_python_rules():
    python = TagRulesConfig(
        rules=[
            MapToRule(id="python-map", priority=100, map_to="FromPython", tags_ci=["X"])
        ]
    )
    ui = TagMapping(
        id="ui-map",
        match="tag_ci",
        values=["X"],
        action="map_to",
        map_to="FromUI",
    )
    config = merge_mapping_rules(python, [ui])
    resolver = _resolver_with(
        ResolvedTag(original="X", resolved="Canonical X", status="synonym", changed=True)
    )
    item = TagRulesEngine(config, resolver).apply_one("X")
    assert item.mapped == "FromUI"
    assert item.mapping_rule == "ui-map"


def test_mentions_collects_from_canonical_or_substring():
    mapping = TagMapping(
        id="river-collect",
        match="mentions",
        values=["River Song"],
        action="collect",
        collections=["River Song"],
    )
    config = merge_mapping_rules(TagRulesConfig(resolve_canonical=True), [mapping])
    resolver = _resolver_with(
        ResolvedTag(
            original="Melody Pond",
            resolved="River Song",
            status="synonym",
            changed=True,
        ),
        ResolvedTag(
            original="River Song - Freeform",
            resolved="River Song",
            status="synonym",
            changed=True,
        ),
        ResolvedTag(original="Fluff", resolved="Fluff", status="canonical", changed=False),
    )
    engine = TagRulesEngine(config, resolver)
    melody = engine.apply_one("Melody Pond")
    freeform = engine.apply_one("River Song - Freeform")
    fluff = engine.apply_one("Fluff")
    assert melody.collections == ["River Song"]
    assert melody.mapped == "River Song"
    assert freeform.collections == ["River Song"]
    assert fluff.collections == []


def test_infer_collections_from_tag_text():
    from ao3kit.tags.mappings import infer_collections, mapping_from_form

    assert infer_collections(action="collect", values=["River Song"]) == ["River Song"]
    assert infer_collections(action="drop", values=["tbd"]) == []
    assert infer_collections(action="keep_separate", values=["Jegulus"]) == []
    assert infer_collections(
        action="map_to", values=["Melody Pond"], map_to="River Song"
    ) == []
    assert infer_collections(
        action="keep_separate",
        values=["Jegulus"],
        collections="Jegulus",
    ) == ["Jegulus"]
    mapping = mapping_from_form(
        match="mentions",
        values="River Song",
        action="collect",
        existing_ids=[],
    )
    assert mapping.collections == ["River Song"]
    assert mapping.action == "collect"
    tag_only = mapping_from_form(
        match="is_ci",
        values="Jegulus",
        action="keep_separate",
        existing_ids=[],
    )
    assert tag_only.collections == []
    assert tag_only.action == "keep_separate"


def test_is_ci_matches_raw_or_canonical():
    mapping = TagMapping(
        id="jegulus-is",
        match="is_ci",
        values=["Jegulus"],
        action="keep_separate",
        collections=["Jegulus"],
    )
    config = merge_mapping_rules(TagRulesConfig(resolve_canonical=True), [mapping])
    resolver = _resolver_with(
        ResolvedTag(
            original="Jegulus",
            resolved="Regulus Black/James Potter",
            status="synonym",
            changed=True,
        )
    )
    item = TagRulesEngine(config, resolver).apply_one("Jegulus")
    assert item.mapped == "Jegulus"
    assert item.collections == ["Jegulus"]
    with pytest.raises(ValueError, match="target tag"):
        mapping_from_form(
            match="tag_ci",
            values="Melody Pond",
            action="map_to",
            map_to="",
            existing_ids=[],
        )


def test_mapping_roundtrip_yaml(tmp_path: Path):
    path = tmp_path / "mappings.yaml"
    rows = [
        mapping_from_form(
            match="tag_ci",
            values="Jegulus",
            action="keep_separate",
            collections="Jegulus",
            stop=True,
            existing_ids=[],
        )
    ]
    save_mappings(path, rows)
    from ao3kit.tags.mappings import load_mappings

    loaded = load_mappings(path)
    assert len(loaded) == 1
    assert loaded[0].id == "keep_separate-jegulus"
    assert loaded[0].action == "keep_separate"
    assert loaded[0].stop is True


def test_move_toggle_remove():
    rows = [
        TagMapping(id="a", values=["A"], action="drop"),
        TagMapping(id="b", values=["B"], action="drop"),
        TagMapping(id="c", values=["C"], action="drop"),
    ]
    moved = move_mapping(rows, "c", direction="up")
    assert [item.id for item in moved] == ["a", "c", "b"]
    toggled = toggle_mapping(moved, "c")
    assert toggled[1].enabled is False
    remaining = remove_mapping(toggled, "a")
    assert [item.id for item in remaining] == ["c", "b"]


def test_load_active_rules_merges_mappings(tmp_path: Path):
    cfg = init_user_config(home=tmp_path / "home")
    cfg.save_mappings(
        [
            TagMapping(
                id="keep-jegulus",
                values=["Jegulus"],
                action="keep_separate",
                collections=["Jegulus"],
            )
        ]
    )
    rules = cfg.load_active_rules()
    assert any(rule.id == "keep-jegulus" for rule in rules.rules)
    assert any(rule.id == "example-collection" for rule in rules.rules)
    keep = next(rule for rule in rules.sorted_rules() if rule.id == "keep-jegulus")
    example = next(
        rule for rule in rules.sorted_rules() if rule.id == "example-collection"
    )
    assert rules.sorted_rules().index(keep) < rules.sorted_rules().index(example)


def test_preview_tag_shows_canonical_and_mapping():
    mapping = TagMapping(
        id="keep-jegulus",
        values=["Jegulus"],
        action="keep_separate",
    )
    config = merge_mapping_rules(TagRulesConfig(resolve_canonical=True), [mapping])
    resolver = _resolver_with(
        ResolvedTag(
            original="Jegulus",
            resolved="Regulus Black/James Potter",
            status="synonym",
            changed=True,
        )
    )
    preview = preview_tag("Jegulus", TagRulesEngine(config, resolver))
    assert preview["canonical"] == "Regulus Black/James Potter"
    assert preview["mapped"] == "Jegulus"
    assert preview["mapping_action"] == "keep_separate"


def test_config_cli_mappings(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    from ao3kit.config_cli import main

    home = str(tmp_path / "home")
    assert (
        main(
            [
                "--home",
                home,
                "mappings",
                "add",
                "--values",
                "Jegulus",
                "--action",
                "keep_separate",
                "--collection",
                "Jegulus",
                "--stop",
            ]
        )
        == 0
    )
    assert main(["--home", home, "mappings", "list"]) == 0
    listed = capsys.readouterr().out
    assert "keep_separate-jegulus" in listed
    cfg = load_user_config(home=tmp_path / "home")
    assert cfg.load_mappings()[0].values == ["Jegulus"]
    assert cfg.load_mappings()[0].collections == ["Jegulus"]
    assert main(["--home", home, "mappings", "remove", "keep_separate-jegulus"]) == 0
    assert cfg.load_mappings() == []


def test_config_cli_mappings_set(tmp_path: Path):
    from ao3kit.config_cli import main

    home = str(tmp_path / "home")
    assert (
        main(
            [
                "--home",
                home,
                "mappings",
                "add",
                "--values",
                "tbd",
                "--action",
                "drop",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--home",
                home,
                "mappings",
                "set",
                "drop-tbd",
                "--values",
                "n/a, tbd",
                "--action",
                "drop",
            ]
        )
        == 0
    )
    cfg = load_user_config(home=tmp_path / "home")
    assert cfg.load_mappings()[0].values == ["n/a", "tbd"]
