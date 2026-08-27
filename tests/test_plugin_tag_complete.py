"""Plugin tag-name autocomplete helpers (Calibre-free)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ao3kit.tags.cache import TagCache
from ao3kit.tags.suggest import suggest_tag_names as library_suggest

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin" / "tag_complete.py"


def load_plugin_tag_complete():
    spec = importlib.util.spec_from_file_location("ao3_plugin_tag_complete", PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _populate(cache: TagCache) -> None:
    cache.remember_alias(
        "River Song", "River Song", status="canonical", category="Character"
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


def test_plugin_suggest_matches_library(tmp_path: Path) -> None:
    cache_path = tmp_path / "tags.sqlite"
    cache = TagCache.load(cache_path, ttl_days=None)
    _populate(cache)
    cache.close()
    mod = load_plugin_tag_complete()
    extra = ["Riverdale"]
    lib = library_suggest("River", cache_path=cache_path, extra=extra)
    plugin = mod.suggest_tag_names("River", cache_path=cache_path, extra=extra)
    assert plugin == lib
    assert "River Song" in plugin
    assert "Riverdale" in plugin
    assert mod.suggest_tag_names(
        "Doctor", cache_path=cache_path, category="Fandom"
    ) == ["Doctor Who (2005)"]


def test_collection_match_category_and_extras() -> None:
    mod = load_plugin_tag_complete()
    assert mod.category_for_collection_match("mentions") is None
    assert mod.category_for_collection_match("is_ci") is None
    assert mod.category_for_collection_match("fandom_mentions") == "Fandom"
    assert mod.category_for_collection_match("author_ci") == "Author"
    assert mod.category_for_collection_match("work_id") is False
    assert mod.category_for_collection_match("calibre_uuid") is False
    vocab = {
        "tags": ["Angst"],
        "fandoms": ["The Pitt"],
        "relationships": ["A/B"],
        "authors": ["Moffat"],
    }
    authors = mod.extras_for_collection_match("author_ci", vocab)
    assert authors == ["Moffat"]
    fandoms = mod.extras_for_collection_match("fandom_mentions", vocab)
    assert "The Pitt" in fandoms
    assert "Angst" not in fandoms
    tags = mod.extras_for_collection_match("mentions", vocab)
    assert "Angst" in tags
    assert "The Pitt" in tags


def test_plugin_csv_token_helpers_match_library() -> None:
    from ao3kit.tags.suggest import current_csv_token, replace_csv_token

    mod = load_plugin_tag_complete()
    text = "Fluff, Hurt/Com, Angst"
    assert mod.current_csv_token(text, 14) == current_csv_token(text, 14)
    assert mod.replace_csv_token(text, "Hurt/Comfort", 14) == replace_csv_token(
        text, "Hurt/Comfort", 14
    )


def test_library_vocab_reads_id_maps() -> None:
    mod = load_plugin_tag_complete()

    class _Api:
        def get_id_map(self, lookup: str):
            return {
                "tags": {1: "Fluff", 2: "Angst"},
                "#fandom": {3: "Doctor Who (2005)"},
                "#relationships": {4: "Amy/Rory"},
                "authors": {5: "Moffat"},
            }[lookup]

    class _Db:
        new_api = _Api()

    vocab = mod.library_vocab(_Db())
    assert vocab["tags"] == ["Fluff", "Angst"]
    assert vocab["fandoms"] == ["Doctor Who (2005)"]
    assert vocab["authors"] == ["Moffat"]
    assert mod.library_vocab(None) == {}
