"""Tests for code-first tag rules and loaders."""

from __future__ import annotations

from pathlib import Path

from ao3kit.tags.metadata import ResolvedTag, TagResolver
from ao3kit.tags.rules import (
    CollectRule,
    DropRule,
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
        session=object(), owns_session=False, cache_path=None, persist=False
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
        drop_unmarked=False,
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


def test_load_python_rules_from_file(tmp_path: Path):
    path = tmp_path / "rules.py"
    path.write_text(
        "from ao3kit.tags.rules import KeepSeparateRule, TagRulesConfig\n"
        "RULES = TagRulesConfig(\n"
        "    resolve_canonical=True,\n"
        "    rules=[KeepSeparateRule(id='keep-jegulus', tags_ci=['Jegulus'])],\n"
        ")\n",
        encoding="utf-8",
    )
    config = load_tag_rules(path)
    assert config.resolve_canonical is True
    assert any(rule.id == "keep-jegulus" for rule in config.rules)


def test_load_yaml_rules_from_file(tmp_path: Path):
    path = tmp_path / "rules.yaml"
    path.write_text(
        "version: 1\n"
        "resolve_canonical: true\n"
        "rules:\n"
        "  - use: keep_separate\n"
        "    id: keep-jegulus\n"
        "    tags_ci: [Jegulus]\n",
        encoding="utf-8",
    )
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


def _warmed_resolver(*profiles):
    resolver = TagResolver(
        session=object(), owns_session=False, cache_path=None, persist=False
    )
    for profile in profiles:
        resolver.warm(profile)
    return resolver


def _fandom_profile(name: str, *, metatags: list[str] | None = None, synonym_of: str | None = None):
    from ao3kit.tags.metadata import TagProfile, TagRef

    return TagProfile(
        name=name,
        url=f"https://archiveofourown.org/tags/{name}",
        category="Fandom",
        canonical=synonym_of is None,
        filterable=True,
        description="",
        synonym_of=(
            TagRef(name=synonym_of, url=f"https://archiveofourown.org/tags/{synonym_of}")
            if synonym_of
            else None
        ),
        metatags=[
            TagRef(name=m, url=f"https://archiveofourown.org/tags/{m}")
            for m in (metatags or [])
        ],
    )


def test_engine_appends_metatags_after_original_tags():
    resolver = _warmed_resolver(
        _fandom_profile("Spider-Man - All Media Types", metatags=["Marvel"]),
        _fandom_profile("Marvel"),
        _fandom_profile("Fluff"),
    )
    result = TagRulesEngine(TagRulesConfig(), resolver).apply(
        ["Spider-Man - All Media Types", "Fluff"]
    )
    assert result.simplified == [
        "Spider-Man - All Media Types",
        "Fluff",
        "Marvel",
    ]
    spider = next(t for t in result.tags if t.original == "Spider-Man - All Media Types")
    assert spider.metatags == ["Marvel"]
    assert result.inserted_metatags == ["Marvel"]


def test_engine_skips_metatags_when_source_tag_is_dropped():
    resolver = _warmed_resolver(
        _fandom_profile("Spider-Man - All Media Types", metatags=["Marvel"]),
        _fandom_profile("Marvel"),
    )
    config = TagRulesConfig(
        rules=[DropRule(id="drop-spider", tags=["Spider-Man - All Media Types"])]
    )
    result = TagRulesEngine(config, resolver).apply(["Spider-Man - All Media Types"])
    assert result.simplified == []
    assert result.inserted_metatags == []


def test_engine_respects_include_metatags_false():
    resolver = _warmed_resolver(
        _fandom_profile("Spider-Man - All Media Types", metatags=["Marvel"]),
        _fandom_profile("Marvel"),
    )
    result = TagRulesEngine(
        TagRulesConfig(include_metatags=False), resolver
    ).apply(["Spider-Man - All Media Types"])
    assert result.simplified == ["Spider-Man - All Media Types"]
    assert result.inserted_metatags == []


def test_engine_skips_metatags_for_non_fandom_tags():
    from ao3kit.tags.metadata import TagProfile, TagRef

    resolver = _warmed_resolver(
        TagProfile(
            name="Amy Pond (Doctor Who)",
            url="https://archiveofourown.org/tags/Amy Pond (Doctor Who)",
            category="Character",
            canonical=True,
            filterable=True,
            description="",
            metatags=[
                TagRef(name="Amy", url="https://archiveofourown.org/tags/Amy")
            ],
        )
    )
    result = TagRulesEngine(TagRulesConfig(), resolver).apply(
        ["Amy Pond (Doctor Who)"]
    )
    assert result.simplified == ["Amy Pond (Doctor Who)"]
    assert result.inserted_metatags == []


def test_engine_drop_unmarked_after_mapping():
    config = TagRulesConfig(resolve_canonical=True, drop_unmarked=True)
    resolver = _resolver_with(
        ResolvedTag(
            original="custom freeform",
            resolved="custom freeform",
            status="unmarked",
            changed=False,
        ),
        ResolvedTag(original="Fluff", resolved="Fluff", status="canonical"),
    )
    result = TagRulesEngine(config, resolver).apply(["custom freeform", "Fluff"])
    assert result.simplified == ["Fluff"]
    assert "custom freeform" in result.dropped
