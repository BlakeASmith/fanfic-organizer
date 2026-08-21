"""Tests for AO3 tag wrangling metadata extraction."""

from __future__ import annotations

from pathlib import Path

from tag_metadata import (
    TagSearchCriteria,
    build_tag_search_url,
    build_tag_sets_search_url,
    decode_tag_path,
    encode_tag_path,
    parse_tag_page,
    parse_tag_search_page,
    parse_tag_search_url,
    parse_tag_set_page,
    parse_tag_sets_search_page,
    tag_name_from_url,
    tag_url,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_encode_decode_tag_path_roundtrip():
    assert encode_tag_path("Kissing") == "Kissing"
    encoded_hash = encode_tag_path("#kiss")
    assert "*h*" in encoded_hash or "%2Ah%2A" in encoded_hash
    assert decode_tag_path(encoded_hash) == "#kiss"
    assert decode_tag_path(encode_tag_path("A/B")) == "A/B"
    assert decode_tag_path(encode_tag_path("Dr. Who")) == "Dr. Who"


def test_tag_url_and_name_from_url():
    assert tag_url("Kissing") == "https://archiveofourown.org/tags/Kissing"
    assert tag_url("/tags/Kissing") == "https://archiveofourown.org/tags/Kissing"
    assert (
        tag_name_from_url("https://archiveofourown.org/tags/Doctor%20Who%20(2005)")
        == "Doctor Who (2005)"
    )


def test_parse_canonical_fandom_tag():
    profile = parse_tag_page(
        _load("tag_canonical_fandom.html"),
        url="https://archiveofourown.org/tags/Doctor%20Who%20(2005)",
    )
    assert profile.name == "Doctor Who (2005)"
    assert profile.category == "Fandom"
    assert profile.canonical is True
    assert profile.filterable is True
    assert profile.synonym_of is None
    assert any(p.name == "TV Shows" for p in profile.parents)
    assert len(profile.synonyms) >= 1
    assert "Characters" in profile.children
    assert "Relationships" in profile.children
    assert "Additional Tags" in profile.children
    assert profile.children_truncated is True
    mapping = profile.synonym_map()
    assert mapping["Doctor Who (2005)"] == "Doctor Who (2005)"
    assert mapping[profile.synonyms[0].name] == "Doctor Who (2005)"


def test_parse_canonical_character_tag():
    profile = parse_tag_page(_load("tag_canonical_character.html"))
    assert profile.name == "Amy Pond (Doctor Who)"
    assert profile.category == "Character"
    assert profile.canonical is True
    assert any(p.name == "Doctor Who (2005)" for p in profile.parents)
    assert profile.metatags
    assert profile.subtags
    assert "Relationships" in profile.children


def test_parse_canonical_freeform_tag():
    profile = parse_tag_page(_load("tag_canonical_freeform.html"))
    assert profile.name == "Kissing"
    assert profile.category == "Additional Tags"
    assert profile.canonical is True
    assert profile.synonyms
    assert profile.subtags


def test_parse_noncanonical_unmarked_tag():
    profile = parse_tag_page(_load("tag_noncanonical_freeform.html"))
    assert profile.name == "Amy pond - Freeform"
    assert profile.category == "Additional Tags"
    assert profile.canonical is False
    assert profile.filterable is False
    assert profile.synonym_of is None
    assert profile.synonym_map() == {}
    assert any(p.name == "Doctor Who (2005)" for p in profile.parents)


def test_parse_synonym_tag():
    profile = parse_tag_page(
        _load("tag_synonym.html"),
        url="https://archiveofourown.org/tags/Kisses",
    )
    assert profile.name == "Kisses"
    assert profile.canonical is False
    assert profile.filterable is True
    assert profile.synonym_of is not None
    assert profile.synonym_of.name == "Kissing"
    assert profile.synonym_map() == {"Kisses": "Kissing"}


def test_build_and_parse_tag_search_url():
    criteria = TagSearchCriteria(
        name="Naruto",
        fandoms="Naruto (Anime & Manga)",
        type="Character",
        wrangling_status="canonical",
        sort_column="created_at",
        sort_direction="desc",
    )
    url = build_tag_search_url(criteria)
    assert "tag_search%5Bname%5D=Naruto" in url or "tag_search[name]=Naruto" in url
    parsed, page = parse_tag_search_url(url)
    assert page == 1
    assert parsed.name == "Naruto"
    assert parsed.fandoms == "Naruto (Anime & Manga)"
    assert parsed.type == "Character"
    assert parsed.wrangling_status == "canonical"
    assert parsed.sort_column == "created_at"
    assert parsed.sort_direction == "desc"


def test_parse_tag_search_canonical_results():
    page = parse_tag_search_page(_load("tag_search_canonical.html"))
    assert page.total_found == 3
    assert page.hits
    first = page.hits[0]
    assert first.name == "Amy Pond (Doctor Who)"
    assert first.type == "Character"
    assert first.canonical is True
    assert first.uses == 4169


def test_parse_tag_search_mixed_canonical_flag():
    page = parse_tag_search_page(_load("tag_search_mixed.html"))
    assert page.total_found == 1096
    canonical = [h for h in page.hits if h.canonical]
    other = [h for h in page.hits if not h.canonical]
    assert canonical
    assert other


def test_parse_tag_sets_search():
    page = parse_tag_sets_search_page(_load("tag_sets_search.html"))
    assert page.total_found == 13
    assert page.tag_sets
    first = page.tag_sets[0]
    assert first.tag_set_id == 196
    assert first.name == "Naruto Fic Festival"
    assert first.characters == 125
    by_id = {t.tag_set_id: t for t in page.tag_sets}
    assert 3937 in by_id
    assert by_id[3937].name == "Naruto and Kim Possible"


def test_build_tag_sets_search_url():
    url = build_tag_sets_search_url("Naruto")
    assert url.startswith("https://archiveofourown.org/tag_sets?")
    assert "query=Naruto" in url


def test_parse_tag_set_detail():
    detail = parse_tag_set_page(
        _load("tag_set_detail.html"),
        url="https://archiveofourown.org/tag_sets/3937",
    )
    assert detail.tag_set_id == 3937
    assert detail.name == "Naruto and Kim Possible"
    assert detail.created_on == "2020-02-19"
    assert "Orochimaru_Wife23" in detail.maintainers
    assert detail.nominations.get("fandoms") == 20
    assert detail.tags
    assert any("Categories" in key for key in detail.tags)


def _profile(
    name: str,
    *,
    canonical: bool = False,
    synonym_of: str | None = None,
    synonyms: list[str] | None = None,
    category: str = "Additional Tags",
) -> TagProfile:
    from tag_metadata import TagProfile, TagRef

    return TagProfile(
        name=name,
        url=f"https://archiveofourown.org/tags/{name}",
        category=category,  # type: ignore[arg-type]
        canonical=canonical and synonym_of is None,
        filterable=canonical or synonym_of is not None,
        description="",
        synonym_of=(
            TagRef(name=synonym_of, url=f"https://archiveofourown.org/tags/{synonym_of}")
            if synonym_of
            else None
        ),
        synonyms=[
            TagRef(name=s, url=f"https://archiveofourown.org/tags/{s}")
            for s in (synonyms or [])
        ],
    )


def test_tag_resolver_simplifies_synonyms_and_dedupes():
    from tag_metadata import TagResolver

    resolver = TagResolver(
        session=object(), delay=0, owns_session=False, cache_path=None, persist=False
    )
    resolver.warm(
        _profile(
            "Sirius Black/Remus Lupin",
            canonical=True,
            category="Relationship",
            synonyms=["wolfstar", "Wolfstar"],
        )
    )
    resolver.warm(
        _profile("Kisses", synonym_of="Kissing", category="Additional Tags")
    )
    resolver.warm(_profile("Kissing", canonical=True, synonyms=["Kisses"]))
    resolver.warm(
        _profile("Amy pond - Freeform", canonical=False, category="Additional Tags")
    )

    result = resolver.simplify(
        [
            "wolfstar",
            "Sirius Black/Remus Lupin",
            "Kisses",
            "Kissing",
            "Amy pond - Freeform",
        ]
    )
    assert result.simplified == [
        "Sirius Black/Remus Lupin",
        "Kissing",
        "Amy pond - Freeform",
    ]
    # Later duplicates of an already-kept canonical are dropped.
    assert "Sirius Black/Remus Lupin" in result.dropped
    assert "Kissing" in result.dropped

    statuses = {r.original: r.status for r in result.resolved}
    assert statuses["wolfstar"] == "synonym"
    assert statuses["Sirius Black/Remus Lupin"] == "canonical"
    assert statuses["Kisses"] == "synonym"
    assert statuses["Amy pond - Freeform"] == "unmarked"

    by_original = {r.original: r.resolved for r in result.resolved}
    assert by_original["wolfstar"] == "Sirius Black/Remus Lupin"
    assert by_original["Kisses"] == "Kissing"


def test_tag_resolver_drop_unmarked():
    from tag_metadata import TagResolver

    resolver = TagResolver(
        session=object(), delay=0, owns_session=False, cache_path=None, persist=False
    )
    resolver.warm(_profile("Kissing", canonical=True))
    resolver.warm(_profile("custom freeform", canonical=False))

    result = resolver.simplify(
        ["Kissing", "custom freeform"], drop_unmarked=True
    )
    assert result.simplified == ["Kissing"]
    assert result.dropped == ["custom freeform"]


def test_tag_cache_persists_and_fans_out_synonyms(tmp_path: Path):
    from ao3kit.tags.cache import TagCache
    from tag_metadata import TagResolver

    path = tmp_path / "tags.sqlite"
    resolver = TagResolver(
        session=object(),
        delay=0,
        owns_session=False,
        cache_path=path,
        persist=True,
        follow_canonical=False,
        ttl_days=90,
    )
    resolver.warm(
        _profile(
            "Kissing",
            canonical=True,
            synonyms=["Kisses", "#kiss", "smooching"],
        )
    )
    resolver.warm(_profile("custom freeform", canonical=False))
    resolver.close()

    assert path.is_file()
    reloaded = TagCache.load(path, ttl_days=90)
    assert reloaded.lookup("Kisses") == ("Kissing", "synonym")
    assert reloaded.lookup("#kiss") == ("Kissing", "synonym")
    assert reloaded.lookup("Kissing") == ("Kissing", "canonical")
    assert reloaded.lookup("custom freeform") == ("custom freeform", "unmarked")
    reloaded.close()

    resolver2 = TagResolver(
        session=object(),
        delay=0,
        owns_session=False,
        cache_path=path,
        persist=True,
        ttl_days=90,
    )
    hit = resolver2.resolve_one("smooching")
    assert hit.resolved == "Kissing"
    assert hit.status == "synonym"
    assert resolver2.stats.fetches == 0
    assert resolver2.stats.disk_hits == 1
    resolver2.close()


def test_tag_cache_expires_whole_tree(tmp_path: Path):
    from datetime import datetime, timedelta, timezone

    from ao3kit.tags.cache import TagCache
    from tag_metadata import TagResolver

    path = tmp_path / "tags.sqlite"
    resolver = TagResolver(
        session=object(),
        delay=0,
        owns_session=False,
        cache_path=path,
        persist=True,
        ttl_days=30,
    )
    resolver.warm(
        _profile("Kissing", canonical=True, synonyms=["Kisses", "smooching"])
    )
    resolver.close()

    cache = TagCache.load(path, ttl_days=30)
    conn = cache._open()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    conn.execute(
        "UPDATE entries SET fetched_at = ? WHERE root = ?",
        (old, "Kissing"),
    )
    conn.commit()
    cache.close()

    expired = TagCache.load(path, ttl_days=30)
    assert expired.lookup("Kisses") is None
    assert expired.lookup("Kissing") is None
    assert expired.expired_trees >= 1
    expired.close()


def test_tag_cache_migrates_legacy_json(tmp_path: Path):
    import json

    from ao3kit.tags.cache import TagCache

    json_path = tmp_path / "ao3_tag_cache.json"
    sqlite_path = tmp_path / "ao3_tag_cache.sqlite"
    json_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "canonical_for": {"Kisses": "Kissing", "Kissing": "Kissing"},
                "unmarked": ["custom"],
                "categories": {"Kissing": "Freeform", "Kisses": "Freeform"},
                "fetched_at": {
                    "Kissing": "2026-01-01T00:00:00+00:00",
                    "Kisses": "2026-01-01T00:00:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )
    cache = TagCache.load(sqlite_path, ttl_days=0)
    assert cache.lookup("Kisses") == ("Kissing", "synonym")
    assert cache.lookup("custom") == ("custom", "unmarked")
    cache.close()
    assert sqlite_path.is_file()


def test_tag_resolver_follow_canonical_indexes_siblings():
    from tag_metadata import TagResolver

    fetches: list[str] = []

    class FakeSession:
        pass

    resolver = TagResolver(
        session=FakeSession(),
        delay=0,
        owns_session=False,
        cache_path=None,
        persist=False,
        follow_canonical=True,
    )

    def fake_fetch(name: str, *, followed: bool = False):
        fetches.append(name)
        if name == "Kisses":
            profile = _profile("Kisses", synonym_of="Kissing")
        elif name == "Kissing":
            profile = _profile(
                "Kissing",
                canonical=True,
                synonyms=["Kisses", "#kiss", "pecks"],
            )
        else:
            raise AssertionError(name)
        resolver.warm(profile)
        resolver._profiles.setdefault(name, profile)
        if followed:
            resolver.stats.follow_fetches += 1
        else:
            resolver.stats.fetches += 1
        return profile

    resolver._fetch_profile = fake_fetch  # type: ignore[method-assign]

    first = resolver.resolve_one("Kisses")
    assert first.resolved == "Kissing"
    assert fetches == ["Kisses", "Kissing"]

    second = resolver.resolve_one("#kiss")
    assert second.resolved == "Kissing"
    assert second.status == "synonym"
    assert fetches == ["Kisses", "Kissing"]  # no extra fetch

