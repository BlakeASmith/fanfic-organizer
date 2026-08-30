"""Tests for tag enrichment of scrape records."""

from __future__ import annotations

from ao3kit.tags.clean import collect_unique_tag_names, enrich_record, enrich_records
from ao3kit.tags.metadata import ResolvedTag, TagProfile, TagResolver
from ao3kit.tags.rules import CollectRule, KeepSeparateRule, TagRulesConfig, TagRulesEngine


def _resolver_with(*resolved: ResolvedTag) -> TagResolver:
    resolver = TagResolver(
        session=object(), owns_session=False, cache_path=None, persist=False
    )

    def fake_resolve_one(name: str) -> ResolvedTag:
        for item in resolved:
            if item.original == name:
                return item
        return ResolvedTag(
            original=name, resolved=name, status="canonical", changed=False
        )

    resolver.resolve_one = fake_resolve_one  # type: ignore[method-assign]
    return resolver


def test_format_ao3_and_rule_mappings():
    from ao3kit.tags.clean import describe_ruled_result, format_ao3_mapping
    from ao3kit.tags.metadata import ResolvedTag
    from ao3kit.tags.rules import RuledTag, RuledTagsResult

    assert (
        format_ao3_mapping(
            ResolvedTag(
                original="Kisses",
                resolved="Kissing",
                status="synonym",
                changed=True,
            )
        )
        == "Kisses → Kissing"
    )
    assert (
        format_ao3_mapping(
            ResolvedTag(
                original="Fluff", resolved="Fluff", status="canonical", changed=False
            )
        )
        is None
    )

    result = RuledTagsResult(
        original=["Kisses", "Jegulus", "Spam"],
        tags=[
            RuledTag(
                original="Kisses",
                mapped="Kissing",
                status="synonym",
                mapping_action="default",
            ),
            RuledTag(
                original="Jegulus",
                mapped="Jegulus",
                status="synonym",
                mapping_action="keep_separate",
                mapping_rule="keep-jegulus",
                collections=["Jegulus"],
                applied_rules=["keep-jegulus"],
            ),
            RuledTag(
                original="Spam",
                mapped=None,
                status="canonical",
                mapping_action="drop",
                mapping_rule="drop-spam",
                dropped=True,
            ),
        ],
        simplified=["Kissing", "Jegulus"],
        dropped=["Spam"],
        collections={"Jegulus": ["Jegulus"]},
    )
    lines = describe_ruled_result(result)
    assert "Kisses → Kissing  [AO3 synonym]" in lines
    assert "Jegulus → Jegulus  [keep-jegulus: keep separate]" in lines
    assert "Spam → (dropped)  [drop-spam]" in lines
    assert any("collections: Jegulus" in line for line in lines)


def test_enrich_record_attaches_cleaned_payload():
    resolver = _resolver_with(
        ResolvedTag(
            original="Kisses",
            resolved="Kissing",
            status="synonym",
            changed=True,
        ),
        ResolvedTag(
            original="Jegulus",
            resolved="Regulus Black/James Potter",
            status="synonym",
            changed=True,
        ),
    )
    rules = TagRulesConfig(
        resolve_canonical=True,
        rules=[
            KeepSeparateRule(
                id="keep-jegulus",
                priority=100,
                tags_ci=["Jegulus"],
                collections=["Jegulus"],
                stop=True,
            )
        ],
    )
    engine = TagRulesEngine(rules, resolver)
    record = {
        "work_id": "1",
        "title": "T",
        "tags": ["Kisses", "Jegulus", "Fluff"],
        "fandoms": ["Harry Potter - J. K. Rowling"],
    }
    enriched = enrich_record(record, engine)
    assert enriched["tags"] == ["Kisses", "Jegulus", "Fluff"]  # raw preserved
    cleaned = enriched["cleaned"]
    assert cleaned["source"] == "rules"
    assert cleaned["simplified"] == ["Kissing", "Jegulus", "Fluff"]
    assert "Jegulus" in cleaned["collections"]


def test_enrich_record_merges_fandom_collections():
    resolver = _resolver_with(
        ResolvedTag(
            original="Doctor Who (2005)",
            resolved="Doctor Who (2005)",
            status="canonical",
            changed=False,
        ),
        ResolvedTag(
            original="Fluff",
            resolved="Fluff",
            status="canonical",
            changed=False,
        ),
    )
    rules = TagRulesConfig(
        resolve_canonical=True,
        rules=[
            CollectRule(
                id="dw-collect",
                contains_ci=["Doctor Who"],
                collections=["Doctor Who"],
            )
        ],
    )
    engine = TagRulesEngine(rules, resolver)
    cleaned = enrich_record(
        {"tags": ["Fluff"], "fandoms": ["Doctor Who (2005)"]},
        engine,
    )["cleaned"]
    assert "Doctor Who" in cleaned["collections"]
    assert cleaned["simplified"] == ["Fluff"]


def test_collect_unique_tag_names_dedupes_across_works():
    names = collect_unique_tag_names(
        [
            {"tags": ["Fluff", "Angst"], "fandoms": ["HP"]},
            {"tags": ["Fluff", "Hurt/Comfort"], "fandoms": ["HP"]},
        ],
        include_fandoms=True,
    )
    assert names == ["Fluff", "Angst", "HP", "Hurt/Comfort"]


def test_collect_unique_tag_names_includes_relationships():
    names = collect_unique_tag_names(
        [
            {
                "tags": ["Fluff"],
                "fandoms": ["HP"],
                "relationships": ["Regulus Black/James Potter"],
            },
            {
                "tags": ["Fluff"],
                "relationships": ["Regulus Black/James Potter", "Jegulus"],
            },
        ]
    )
    assert names == ["Fluff", "HP", "Regulus Black/James Potter", "Jegulus"]


def test_enrich_records_with_injected_resolver(tmp_path, monkeypatch):
    # Avoid touching real user config home during enrich_records defaults.
    from ao3kit import config as config_mod

    monkeypatch.setattr(
        config_mod,
        "default_home",
        lambda project_root=None: tmp_path / ".ao3kit",
    )
    cfg = config_mod.init_user_config(home=tmp_path / ".ao3kit")
    # Empty rules still resolve_canonical via defaults in RULES template —
    # replace with empty config module content.
    cfg.write_rule(
        "default",
        "from ao3kit.tags.rules import TagRulesConfig\n"
        "RULES = TagRulesConfig(resolve_canonical=True, rules=[])\n",
    )

    resolver = _resolver_with(
        ResolvedTag(
            original="Kisses", resolved="Kissing", status="synonym", changed=True
        )
    )
    out = enrich_records(
        [{"work_id": "9", "tags": ["Kisses"]}],
        resolver=resolver,
        include_fandoms=False,
    )
    assert out[0]["cleaned"]["simplified"] == ["Kissing"]


def test_enrich_records_drop_unmarked_override(tmp_path, monkeypatch):
    from ao3kit import config as config_mod

    monkeypatch.setattr(
        config_mod,
        "default_home",
        lambda project_root=None: tmp_path / ".ao3kit",
    )
    cfg = config_mod.init_user_config(home=tmp_path / ".ao3kit")
    cfg.update_settings(drop_unmarked=False)
    cfg.write_rule(
        "default",
        "from ao3kit.tags.rules import TagRulesConfig\n"
        "RULES = TagRulesConfig(resolve_canonical=True, rules=[])\n",
    )

    resolver = TagResolver(
        session=object(), owns_session=False, cache_path=None, persist=False
    )
    resolver.warm(
        TagProfile(
            name="custom freeform",
            url="https://archiveofourown.org/tags/custom%20freeform",
            category="Additional Tags",
            canonical=False,
            filterable=True,
            description="",
        )
    )

    out = enrich_records(
        [{"work_id": "9", "tags": ["custom freeform"]}],
        resolver=resolver,
        include_fandoms=False,
        drop_unmarked=True,
    )
    assert out[0]["cleaned"]["simplified"] == []
    assert "custom freeform" in out[0]["cleaned"]["dropped"]


def test_enrich_record_appends_fandom_metatags():
    from ao3kit.tags.metadata import TagProfile, TagRef

    resolver = TagResolver(
        session=object(), owns_session=False, cache_path=None, persist=False
    )
    resolver.warm(
        TagProfile(
            name="Spider-Man - All Media Types",
            url="https://archiveofourown.org/tags/Spider-Man - All Media Types",
            category="Fandom",
            canonical=True,
            filterable=True,
            description="",
            metatags=[
                TagRef(
                    name="Marvel",
                    url="https://archiveofourown.org/tags/Marvel",
                )
            ],
        )
    )
    resolver.warm(
        TagProfile(
            name="Marvel",
            url="https://archiveofourown.org/tags/Marvel",
            category="Fandom",
            canonical=True,
            filterable=True,
            description="",
        )
    )
    engine = TagRulesEngine(TagRulesConfig(resolve_canonical=True), resolver)
    enriched = enrich_record(
        {
            "work_id": "1",
            "title": "T",
            "tags": ["Fluff"],
            "fandoms": ["Spider-Man - All Media Types"],
        },
        engine,
    )
    assert enriched["cleaned"]["simplified"] == ["Fluff"]
    assert enriched["cleaned"]["fandoms"] == [
        "Spider-Man - All Media Types",
        "Marvel",
    ]
    spider = next(
        item
        for item in enriched["cleaned"]["fandoms_detail"]["tags"]
        if item["original"] == "Spider-Man - All Media Types"
    )
    assert spider["metatags"] == ["Marvel"]
    assert "Marvel" not in enriched["cleaned"]["simplified"]


def test_enrich_record_splits_relationship_tags_and_simplifies_column():
    resolver = _resolver_with(
        ResolvedTag(
            original="Drarry",
            resolved="Harry Potter/Draco Malfoy",
            status="synonym",
            changed=True,
            category="Relationship",
        ),
        ResolvedTag(
            original="Harry Potter/Draco Malfoy",
            resolved="Harry Potter/Draco Malfoy",
            status="canonical",
            changed=False,
            category="Relationship",
        ),
        ResolvedTag(
            original="Hurt/Comfort",
            resolved="Hurt/Comfort",
            status="canonical",
            changed=False,
            category="Additional Tags",
        ),
        ResolvedTag(
            original='Melissa "Mel" King/Frank Langdon',
            resolved="Frank Langdon/Mel King",
            status="synonym",
            changed=True,
            category="Relationship",
        ),
    )
    engine = TagRulesEngine(TagRulesConfig(resolve_canonical=True), resolver)
    enriched = enrich_record(
        {
            "work_id": "1",
            "title": "T",
            "tags": ["Drarry", "Hurt/Comfort"],
            "fandoms": [],
            "relationships": ['Melissa "Mel" King/Frank Langdon'],
        },
        engine,
        include_fandoms=False,
    )
    cleaned = enriched["cleaned"]
    assert cleaned["simplified"] == ["Hurt/Comfort"]
    assert cleaned["relationships"] == [
        "Harry Potter/Draco Malfoy",
        "Frank Langdon/Mel King",
    ]
    assert "Drarry" not in cleaned["simplified"]
    extra = cleaned["relationships_detail"]["tags"]
    assert extra[0]["original"] == 'Melissa "Mel" King/Frank Langdon'
    assert extra[0]["mapped"] == "Frank Langdon/Mel King"


def test_enrich_record_drops_non_relationship_tags_from_relationships_column():
    resolver = _resolver_with(
        ResolvedTag(
            original="James 'Bucky' Barnes/Steve Rogers",
            resolved="James 'Bucky' Barnes/Steve Rogers",
            status="canonical",
            changed=False,
            category="Relationship",
        ),
        ResolvedTag(
            original="Hurt/Comfort",
            resolved="Hurt/Comfort",
            status="canonical",
            changed=False,
            category="Additional Tags",
        ),
        ResolvedTag(
            original="Angst",
            resolved="Angst",
            status="canonical",
            changed=False,
            category="Additional Tags",
        ),
    )
    engine = TagRulesEngine(TagRulesConfig(resolve_canonical=True), resolver)
    enriched = enrich_record(
        {
            "work_id": "1",
            "title": "T",
            "tags": ["Hurt/Comfort", "Angst"],
            "relationships": [
                "James 'Bucky' Barnes/Steve Rogers",
                "Hurt/Comfort",
                "Angst",
            ],
        },
        engine,
        include_fandoms=False,
    )
    cleaned = enriched["cleaned"]
    assert cleaned["simplified"] == ["Hurt/Comfort", "Angst"]
    assert cleaned["relationships"] == ["James 'Bucky' Barnes/Steve Rogers"]


def test_collect_remapping_lines_includes_relationship_column():
    from ao3kit.tags.clean import collect_remapping_lines

    lines = collect_remapping_lines(
        [
            {
                "cleaned": {
                    "relationships_detail": {
                        "tags": [
                            {
                                "original": 'Melissa "Mel" King/Frank Langdon',
                                "mapped": "Frank Langdon/Mel King",
                                "status": "synonym",
                                "mapping_action": "default",
                            }
                        ]
                    }
                }
            }
        ]
    )
    assert (
        'Melissa "Mel" King/Frank Langdon → Frank Langdon/Mel King  [AO3 synonym]'
        in lines
    )


def test_enrich_record_applies_keep_separate_to_relationships():
    resolver = _resolver_with(
        ResolvedTag(
            original="Jegulus",
            resolved="Regulus Black/James Potter",
            status="synonym",
            changed=True,
            category="Relationship",
        )
    )
    rules = TagRulesConfig(
        resolve_canonical=True,
        rules=[
            KeepSeparateRule(
                id="keep-jegulus",
                priority=100,
                tags_ci=["Jegulus"],
                collections=["Jegulus"],
                stop=True,
            )
        ],
    )
    engine = TagRulesEngine(rules, resolver)
    cleaned = enrich_record(
        {"tags": [], "relationships": ["Jegulus"]},
        engine,
        include_fandoms=False,
    )["cleaned"]
    assert cleaned["relationships"] == ["Jegulus"]
    assert "Jegulus" in cleaned["collections"]


def test_enrich_record_does_not_append_character_metatags_to_tags():
    from ao3kit.tags.metadata import TagProfile, TagRef

    resolver = TagResolver(
        session=object(), owns_session=False, cache_path=None, persist=False
    )
    resolver.warm(
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
    engine = TagRulesEngine(TagRulesConfig(resolve_canonical=True), resolver)
    enriched = enrich_record(
        {
            "work_id": "1",
            "title": "T",
            "tags": ["Amy Pond (Doctor Who)"],
        },
        engine,
        include_fandoms=False,
    )
    assert enriched["cleaned"]["simplified"] == ["Amy Pond (Doctor Who)"]
    assert "Amy" not in enriched["cleaned"]["simplified"]


def test_collect_remapping_lines_includes_metatag_inserts():
    from ao3kit.tags.clean import collect_remapping_lines

    lines = collect_remapping_lines(
        [
            {
                "cleaned": {
                    "fandoms_detail": {
                        "tags": [
                            {
                                "original": "Spider-Man - All Media Types",
                                "mapped": "Spider-Man - All Media Types",
                                "status": "canonical",
                                "mapping_action": "default",
                                "metatags": ["Marvel"],
                            }
                        ]
                    }
                }
            }
        ]
    )
    assert "Spider-Man - All Media Types → +Marvel  [metatag]" in lines


def test_collect_remapping_lines_unique_across_works():
    from ao3kit.tags.clean import collect_remapping_lines

    records = [
        {
            "cleaned": {
                "tags": [
                    {
                        "original": "Kisses",
                        "mapped": "Kissing",
                        "status": "synonym",
                        "mapping_action": "default",
                    },
                    {
                        "original": "Fluff",
                        "mapped": "Fluff",
                        "status": "canonical",
                        "mapping_action": "default",
                    },
                ]
            }
        },
        {
            "cleaned": {
                "tags": [
                    {
                        "original": "Kisses",
                        "mapped": "Kissing",
                        "status": "synonym",
                        "mapping_action": "default",
                    },
                    {
                        "original": "Spam",
                        "mapped": None,
                        "dropped": True,
                        "mapping_action": "drop",
                        "mapping_rule": "drop-spam",
                    },
                ]
            }
        },
    ]
    lines = collect_remapping_lines(records)
    assert "Kisses → Kissing  [AO3 synonym]  (2 works)" in lines
    assert "Spam → (dropped)  [drop-spam]" in lines
    assert not any("Fluff →" in line for line in lines)

    captured: list[str] = []
    from ao3kit.tags.clean import emit_remapping_summary, format_remapping_summary

    emit_remapping_summary(records, captured.append)
    assert captured[0] == "Tag remappings (2 unique):"
    assert captured[1].startswith("  Kisses → Kissing")
    text = format_remapping_summary(records)
    assert "Tag remappings (2 unique):" in text
    assert "Kisses → Kissing  [AO3 synonym]  (2 works)" in text
    assert format_remapping_summary([]) == (
        "Tag remappings: none (all tags already canonical)"
    )
