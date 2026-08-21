"""Tests for tag enrichment of scrape records."""

from __future__ import annotations

from ao3kit.tags.clean import collect_unique_tag_names, enrich_record, enrich_records
from ao3kit.tags.metadata import ResolvedTag, TagResolver
from ao3kit.tags.rules import KeepSeparateRule, TagRulesConfig, TagRulesEngine


def _resolver_with(*resolved: ResolvedTag) -> TagResolver:
    resolver = TagResolver(
        session=object(), delay=0, owns_session=False, cache_path=None, persist=False
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


def test_collect_unique_tag_names_dedupes_across_works():
    names = collect_unique_tag_names(
        [
            {"tags": ["Fluff", "Angst"], "fandoms": ["HP"]},
            {"tags": ["Fluff", "Hurt/Comfort"], "fandoms": ["HP"]},
        ],
        include_fandoms=True,
    )
    assert names == ["Fluff", "Angst", "HP", "Hurt/Comfort"]


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
