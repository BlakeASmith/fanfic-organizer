"""Tests for bundled tag-cache seed import and build helpers."""

from __future__ import annotations

import json
from pathlib import Path

from ao3kit.tags.cache import TagCache
from ao3kit.tags.metadata import TagProfile
from ao3kit.tags.seed import (
    import_seed_payload,
    tree_from_profile,
)


def _sample_seed() -> dict:
    return {
        "version": 3,
        "format": "ao3kit-tag-cache-seed",
        "generated_at": "2026-08-24T12:00:00+00:00",
        "ttl_days": 90,
        "trees": [
            {
                "root": "Kissing",
                "fetched_at": "2026-08-24T12:00:00+00:00",
                "entries": [
                    {
                        "name": "Kissing",
                        "canonical": "Kissing",
                        "status": "canonical",
                        "category": "Additional Tags",
                        "metatags": [],
                    },
                    {
                        "name": "Kisses",
                        "canonical": "Kissing",
                        "status": "synonym",
                        "category": "Additional Tags",
                    },
                ],
            },
            {
                "root": "Doctor Who (2005)",
                "fetched_at": "2026-08-24T12:00:00+00:00",
                "entries": [
                    {
                        "name": "Doctor Who (2005)",
                        "canonical": "Doctor Who (2005)",
                        "status": "canonical",
                        "category": "Fandom",
                        "metatags": ["Doctor Who", "Doctor Who & Related Fandoms"],
                    },
                ],
            },
        ],
    }


def test_tree_from_profile_canonical_fans_out_synonyms():
    profile = TagProfile(
        name="Kissing",
        url="https://archiveofourown.org/tags/Kissing",
        category="Additional Tags",
        canonical=True,
        filterable=True,
        description="",
        synonyms=[
            type("R", (), {"name": "Kisses", "url": "", "href": None})(),
        ],
    )
    tree = tree_from_profile(profile, fetched_at="2026-01-01T00:00:00+00:00")
    assert tree["root"] == "Kissing"
    names = {e["name"] for e in tree["entries"]}
    assert names == {"Kissing", "Kisses"}


def test_import_seed_payload_inserts_trees(tmp_path: Path):
    cache_path = tmp_path / "cache.sqlite"
    cache = TagCache.load(cache_path, ttl_days=None)
    result = import_seed_payload(cache, _sample_seed(), merge=True)
    assert result["inserted"] == 3
    assert result["skipped"] == 0
    assert cache.lookup("Kisses") == ("Kissing", "synonym")
    assert cache.metatags_for("Doctor Who (2005)") == [
        "Doctor Who",
        "Doctor Who & Related Fandoms",
    ]
    cache.close()


def test_import_seed_merge_skips_existing_names(tmp_path: Path):
    cache_path = tmp_path / "cache.sqlite"
    cache = TagCache.load(cache_path, ttl_days=None)
    cache.remember_alias("Kisses", "Other", status="synonym", category="Additional Tags")
    result = import_seed_payload(cache, _sample_seed(), merge=True)
    assert result["skipped"] >= 1
    assert cache.lookup("Kisses") == ("Other", "synonym")
    assert cache.lookup("Kissing") == ("Kissing", "canonical")
    cache.close()


def test_bundled_seed_load_on_empty_cache(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "ao3_tag_cache.sqlite"
    monkeypatch.setattr(
        "ao3kit.tags.cache.default_tag_cache_path",
        lambda: cache_path,
    )
    cache = TagCache.load(cache_path, ttl_days=None)
    count = cache._conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
    assert int(count) > 0
    cache.close()


def test_tag_cache_load_merges_bundled_seed(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "ao3_tag_cache.sqlite"
    monkeypatch.setattr(
        "ao3kit.tags.cache.default_tag_cache_path",
        lambda: cache_path,
    )
    cache = TagCache.load(cache_path, ttl_days=None)
    before = cache._conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
    cache.close()
    assert int(before) > 0
    merged = TagCache.load(cache_path, ttl_days=None)
    after = merged._conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
    assert int(after) == int(before)
    merged.close()


def test_rows_for_canonical_uses_canonical_index(tmp_path: Path):
    cache_path = tmp_path / "cache.sqlite"
    cache = TagCache.load(cache_path, ttl_days=None)
    import_seed_payload(cache, _sample_seed(), merge=True)
    rows = cache.rows_for_canonical("Kissing")
    assert len(rows) == 2
    assert {r.name for r in rows} == {"Kissing", "Kisses"}
    cache.close()
