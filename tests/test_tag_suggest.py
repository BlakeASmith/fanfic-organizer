"""Local tag-name suggestions from the SQLite cache (no AO3 fetch)."""

from __future__ import annotations

import json
from pathlib import Path

from ao3kit.tags.cache import TagCache
from ao3kit.tags.suggest import (
    current_csv_token,
    replace_csv_token,
    suggest_tag_names,
)


def _populate(cache: TagCache) -> None:
    cache.remember_alias(
        "River Song", "River Song", status="canonical", category="Character"
    )
    cache.remember_alias(
        "Melody Pond", "River Song", status="synonym", category="Character"
    )
    cache.remember_alias(
        "Doctor Who (2005)",
        "Doctor Who (2005)",
        status="canonical",
        category="Fandom",
    )
    cache.remember_alias(
        "Hurt/Comfort",
        "Hurt/Comfort",
        status="canonical",
        category="Additional Tags",
    )
    cache.remember_alias(
        "Angst", "Angst", status="canonical", category="Additional Tags"
    )


def test_suggest_ranks_exact_prefix_and_contains(tmp_path: Path) -> None:
    cache = TagCache.load(tmp_path / "tags.sqlite", ttl_days=None)
    _populate(cache)
    names = cache.suggest_names("River")
    assert names[0] == "River Song"
    assert "Melody Pond" not in names[:1]
    contained = cache.suggest_names("Song")
    assert "River Song" in contained
    exact = cache.suggest_names("Angst")
    assert exact[0] == "Angst"
    cache.close()


def test_suggest_filters_category_and_freeform_alias(tmp_path: Path) -> None:
    cache = TagCache.load(tmp_path / "tags.sqlite", ttl_days=None)
    _populate(cache)
    fandoms = cache.suggest_names("Doctor", category="Fandom")
    assert fandoms == ["Doctor Who (2005)"]
    freeforms = cache.suggest_names("Hurt", category="Freeform")
    assert freeforms == ["Hurt/Comfort"]
    none = cache.suggest_names("River", category="Fandom")
    assert none == []
    cache.close()


def test_suggest_merges_extra_names_and_escapes_like(tmp_path: Path) -> None:
    cache = TagCache.load(tmp_path / "tags.sqlite", ttl_days=None)
    _populate(cache)
    mixed = suggest_tag_names(
        "River",
        cache=cache,
        extra=["Riverdale", "Fluff"],
    )
    assert "River Song" in mixed
    assert "Riverdale" in mixed
    assert "Fluff" not in mixed
    cache.remember_alias("100% Fluff", "100% Fluff", status="canonical")
    assert suggest_tag_names("100%", cache=cache) == ["100% Fluff"]
    assert suggest_tag_names("missing", cache=cache) == []
    assert suggest_tag_names("", cache=cache) == []
    cache.close()


def test_suggest_from_missing_cache_uses_extras_only(tmp_path: Path) -> None:
    names = suggest_tag_names(
        "Ang",
        cache_path=tmp_path / "nope.sqlite",
        extra=["Angst", "Fluff"],
    )
    assert names == ["Angst"]


def test_csv_token_replace_current_value() -> None:
    text = "Fluff, Hurt/Com, Angst"
    start, end, token = current_csv_token(text, cursor=len("Fluff, Hurt/Com"))
    assert token == "Hurt/Com"
    new, pos = replace_csv_token(text, "Hurt/Comfort", cursor=start + 3)
    assert new == "Fluff, Hurt/Comfort, Angst"
    assert new[:pos] == "Fluff, Hurt/Comfort"


def test_tags_suggest_cli_json(tmp_path: Path, capsys) -> None:
    cache = TagCache.load(tmp_path / "tags.sqlite", ttl_days=None)
    _populate(cache)
    cache.close()
    from ao3kit.tags.metadata import main as tags_main

    code = tags_main(
        [
            "suggest",
            "River",
            "--cache",
            str(tmp_path / "tags.sqlite"),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0] == "River Song"
