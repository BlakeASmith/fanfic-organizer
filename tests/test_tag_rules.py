"""Tests for code-first tag rules and loaders."""

from __future__ import annotations

from pathlib import Path

from ao3kit.tags.metadata import ResolvedTag, TagResolver
from ao3kit.tags.rules import (
    CollectRule,
    KeepSeparateRule,
    MapToRule,
    MatchSpec,
    RuleContext,
    RuleEffect,
    TagRule,
    TagRulesConfig,
    TagRulesEngine,
    load_tag_rules,
    rule,
)


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


def test_custom_code_rule_and_keep_separate():
    class ShipNickname(TagRule):
        id = "ship-nick"
        priority = 100

        def apply(self, ctx: RuleContext) -> RuleEffect | None:
            if ctx.original.lower() == "jegulus":
                return RuleEffect(
                    keep_separate=True, collections=["Jegulus"], stop=True
                )
            return None

    config = TagRulesConfig(
        resolve_canonical=True,
        rules=[ShipNickname()],
    )
    resolver = _resolver_with(
        ResolvedTag(
            original="Jegulus",
            resolved="Regulus Black/James Potter",
            status="synonym",
            changed=True,
        )
    )
    result = TagRulesEngine(config, resolver).apply(["Jegulus", "Slow Burn"])
    assert result.simplified == ["Jegulus", "Slow Burn"]
    jegulus = next(t for t in result.tags if t.original == "Jegulus")
    assert jegulus.mapping_action == "keep_separate"
    assert jegulus.collections == ["Jegulus"]


def test_function_rule_decorator():
    @rule(id="collect-fluff", priority=10)
    def collect_fluff(ctx: RuleContext) -> RuleEffect | None:
        if ctx.canonical == "Fluff":
            return RuleEffect(collections=["Fluffy Works"])
        return None

    config = TagRulesConfig(rules=[collect_fluff])
    resolver = _resolver_with(
        ResolvedTag(original="Fluff", resolved="Fluff", status="canonical")
    )
    item = TagRulesEngine(config, resolver).apply_one("Fluff")
    assert item.collections == ["Fluffy Works"]


def test_collections_accumulate_with_canonical_resolve():
    config = TagRulesConfig(
        resolve_canonical=True,
        rules=[
            CollectRule(
                id="river",
                priority=50,
                collections=["River Song"],
                canonical_ci=["river song"],
            ),
            CollectRule(
                id="river-name",
                priority=40,
                collections=["River Song", "Doctor Who"],
                contains=["River Song"],
            ),
        ],
    )
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
        ResolvedTag(
            original="Fluff",
            resolved="Fluff",
            status="canonical",
            changed=False,
        ),
    )
    result = TagRulesEngine(config, resolver).apply(
        ["Melody Pond", "River Song - Freeform", "Fluff"]
    )
    assert result.simplified == ["River Song", "Fluff"]
    assert set(result.collections["River Song"]) == {
        "Melody Pond",
        "River Song - Freeform",
    }
    assert result.collections["Doctor Who"] == ["River Song - Freeform"]


def test_higher_priority_map_to_wins():
    config = TagRulesConfig(
        rules=[
            KeepSeparateRule(id="low-keep", priority=10, tags=["X"]),
            MapToRule(id="high-map", priority=90, map_to="Y", tags=["X"]),
        ]
    )
    resolver = _resolver_with(
        ResolvedTag(original="X", resolved="Z", status="synonym", changed=True)
    )
    item = TagRulesEngine(config, resolver).apply_one("X")
    assert item.mapped == "Y"
    assert item.mapping_rule == "high-map"


def test_stop_skips_lower_priority_rules():
    config = TagRulesConfig(
        rules=[
            KeepSeparateRule(
                id="early-stop",
                priority=100,
                tags=["A"],
                collections=["First"],
                stop=True,
            ),
            CollectRule(
                id="later-collection",
                priority=10,
                tags=["A"],
                collections=["Second"],
                match_any=False,
            ),
        ]
    )
    resolver = _resolver_with(
        ResolvedTag(original="A", resolved="B", status="synonym", changed=True)
    )
    item = TagRulesEngine(config, resolver).apply_one("A")
    assert item.collections == ["First"]
    assert item.applied_rules == ["early-stop"]


def test_load_example_python_rules():
    path = Path(__file__).resolve().parents[1] / "example_tag_rules.py"
    config = load_tag_rules(path)
    assert config.resolve_canonical is True
    assert any(rule.id == "river-song-collection" for rule in config.rules)
    assert any(rule.id == "keep-jegulus" for rule in config.rules)


def test_load_example_yaml_rules():
    path = Path(__file__).resolve().parents[1] / "example_tag_rules.yaml"
    config = load_tag_rules(path)
    assert any(rule.id == "keep-jegulus" for rule in config.rules)
    engine = TagRulesEngine(
        config,
        _resolver_with(
            ResolvedTag(
                original="Jegulus",
                resolved="Regulus Black/James Potter",
                status="synonym",
                changed=True,
            )
        ),
    )
    item = engine.apply_one("Jegulus")
    assert item.mapped == "Jegulus"
    assert item.mapping_action == "keep_separate"


def test_match_spec_and_vs_any():
    and_spec = MatchSpec(contains=["River"], statuses=["synonym"])
    any_spec = MatchSpec(contains=["River"], statuses=["synonym"], match_any=True)
    synonym = RuleContext(
        ResolvedTag(
            original="River Song refs",
            resolved="River Song",
            status="synonym",
            changed=True,
        )
    )
    canonical = RuleContext(
        ResolvedTag(
            original="River Song refs",
            resolved="River Song",
            status="canonical",
            changed=False,
        )
    )
    assert and_spec.matches(synonym)
    assert not and_spec.matches(canonical)
    assert any_spec.matches(canonical)  # contains alone enough
