"""CLI: python -m ao3kit library estimate."""

from __future__ import annotations

import json
from pathlib import Path

from ao3kit.library import estimate_records, main
from ao3kit.tags.cache import TagCache
from ao3kit.tags.metadata import TagProfile


def _seed_cache(path: Path, names: list[str]) -> None:
    cache = TagCache.load(path)
    for name in names:
        cache.remember_profile(
            TagProfile(
                name=name,
                url="",
                category="Additional Tags",
                canonical=True,
                filterable=True,
                description="",
            )
        )
    cache.save()
    cache.close()


def test_estimate_records_counts_uncached(tmp_path: Path):
    cache = TagCache.load(tmp_path / "cache.sqlite")
    cache.remember_profile(
        TagProfile(
            name="Fluff",
            url="",
            category="Additional Tags",
            canonical=True,
            filterable=True,
            description="",
        )
    )
    cache.save()
    records = [
        {
            "work_id": "1",
            "title": "A",
            "tags": ["Fluff", "Angst"],
            "fandoms": ["Harry Potter - J. K. Rowling"],
        },
        {
            "work_id": "2",
            "title": "B",
            "tags": ["Fluff"],
            "epub_file": "epubs/2.epub",
            "series": [
                {
                    "series_id": "9",
                    "name": "A Series",
                    "url": "",
                    "position": 1,
                }
            ],
        },
    ]
    payload = estimate_records(records, cache=cache, request_interval=2.0)
    cache.close()
    assert payload["works"] == 2
    assert payload["unique_tags"] == 3
    assert payload["cached_tags"] == 1
    assert payload["uncached_tags"] == 2
    assert payload["missing_epub"] == 1
    assert payload["has_epub"] == 1
    assert payload["series_complete"] == 1
    assert payload["series_incomplete"] == 1
    assert payload["tag_fetch_seconds"] == 4.0


def test_library_estimate_cli(tmp_path: Path, capsys):
    jsonl = tmp_path / "works.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "work_id": "1",
                "title": "A",
                "tags": ["Fluff", "Angst"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "cache.sqlite"
    _seed_cache(cache_path, ["Fluff"])
    code = main(
        [
            "estimate",
            "--jsonl",
            str(jsonl),
            "--cache",
            str(cache_path),
            "--interval",
            "1",
        ]
    )
    assert code == 0
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload["works"] == 1
    assert payload["uncached_tags"] == 1
    assert "unmatched in cache" in out.err
