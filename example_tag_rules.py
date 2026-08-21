"""Preferred tag rules config: arbitrary Python.

Load with:
  python -m ao3kit tags apply --rules example_tag_rules.py ...

Export ``RULES`` as a ``TagRulesConfig`` (or a list of ``TagRule``s),
or define ``build_rules()``.
"""

from __future__ import annotations

from ao3kit.tags.rules import (
    CollectRule,
    KeepSeparateRule,
    MapToRule,
    RuleContext,
    RuleEffect,
    TagRule,
    TagRulesConfig,
    rule,
)


class RiverSongCollection(TagRule):
    """Anything River Song–related joins the River Song collection."""

    id = "river-song-collection"
    priority = 50
    description = "Collect River Song tags (canonical or name mention)"

    def apply(self, ctx: RuleContext) -> RuleEffect | None:
        original_l = ctx.original.lower()
        canonical_l = ctx.canonical.lower()
        if (
            "river song" in original_l
            or "melody pond" in original_l
            or canonical_l == "river song"
            or "river song" in canonical_l
        ):
            return RuleEffect(collections=["River Song"])
        return None


@rule(id="drop-empty-freeform-noise", priority=20)
def drop_placeholder_tags(ctx: RuleContext) -> RuleEffect | None:
    """Example function rule: drop a few noisy placeholders."""
    if ctx.original.strip().lower() in {"tbd", "tags to be added", "n/a"}:
        return RuleEffect(drop=True, stop=True)
    return None


RULES = TagRulesConfig(
    resolve_canonical=True,
    drop_unmarked=False,
    rules=[
        # Keep a ship nickname from collapsing to the canonical relationship.
        KeepSeparateRule(
            id="keep-jegulus",
            priority=100,
            tags_ci=["Jegulus"],
            collections=["Jegulus"],
            stop=True,
        ),
        # Force a specific raw tag onto a preferred label.
        MapToRule(
            id="melody-pond-to-river",
            priority=80,
            map_to="River Song",
            tags_ci=["Melody Pond"],
            collections=["River Song"],
        ),
        # Custom code rule (preferred style for non-trivial logic).
        RiverSongCollection(),
        # Built-in collect helper (OR across match fields by default).
        CollectRule(
            id="doctor-who-mentions",
            priority=40,
            collections=["Doctor Who"],
            contains_ci=["doctor who"],
            canonical_ci=["Doctor Who (2005)", "Doctor Who"],
        ),
        drop_placeholder_tags,
    ],
)
