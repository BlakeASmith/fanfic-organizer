#!/usr/bin/env python3
"""Extract AO3 tag wrangling metadata (canonical maps, search, tag sets)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse

from bs4 import Tag

from ao3kit.htmlsoup import parse_html

from ao3kit.http import AO3_BASE, attach_credentials, create_session, get_text
from ao3kit.rate import configure_min_interval
from ao3kit.tags.cache import (
    DEFAULT_TAG_CACHE_TTL_DAYS,
    TAG_CACHE_VERSION,
    TagCache,
    default_tag_cache_path,
)

_CACHE_UNSET = object()
# AO3 path encoding for tag names (otwarchive Tag#to_param substitutions).
_TAG_PATH_REPLACEMENTS = (
    ("/", "*s*"),
    ("&", "*a*"),
    (".", "*d*"),
    ("?", "*q*"),
    ("#", "*h*"),
)

CATEGORY_RE = re.compile(
    r"This tag belongs to the\s+(.+?)\s+Category",
    re.IGNORECASE,
)
USES_RE = re.compile(r"\((\d[\d,]*)\)\s*$")
TAG_SEARCH_TYPE_RE = re.compile(
    r"^(Fandom|Character|Relationship|Freeform|UnsortedTag):\s*",
    re.IGNORECASE,
)
FOUND_COUNT_RE = re.compile(r"([\d,]+)\s+Found", re.IGNORECASE)
TAG_SETS_COUNT_RE = re.compile(r"([\d,]+)\s+Tag Sets?", re.IGNORECASE)

TagCategory = Literal[
    "Fandom",
    "Character",
    "Relationship",
    "Additional Tags",
    "UnsortedTag",
    "Unknown",
]

WranglingStatus = Literal[
    "",
    "canonical",
    "noncanonical",
    "synonymous",
    "canonical_synonymous",
    "noncanonical_nonsynonymous",
    "noncanonical_nonsynonymous_unwrangleable",
    "unwrangleable",
]

TAG_TYPE_VALUES = ("", "Fandom", "Character", "Relationship", "Freeform")


def is_fandom_category(category: str | None) -> bool:
    """True when AO3 wrangling lists this tag in the Fandom category."""
    return (category or "").strip().casefold() == "fandom"


def is_relationship_category(category: str | None) -> bool:
    """True when AO3 wrangling lists this tag in the Relationship category."""
    return (category or "").strip().casefold() == "relationship"


WRANGLING_STATUS_VALUES: dict[str, str] = {
    "": "",
    "canonical": "canonical",
    "noncanonical": "noncanonical",
    "synonymous": "synonymous",
    "canonical_synonymous": "canonical_synonymous",
    "noncanonical_nonsynonymous": "noncanonical_nonsynonymous",
    "noncanonical_nonsynonymous_unwrangleable": (
        "noncanonical_nonsynonymous_unwrangleable"
    ),
    "unwrangleable": "unwrangleable",
    # Friendly aliases matching AO3 form labels / common shorthand.
    "canonical or synonymous": "canonical_synonymous",
    "non-canonical": "noncanonical",
    "non-canonical and non-synonymous": "noncanonical_nonsynonymous",
    "non-canonical and non-synonymous and not marked unwrangleable": (
        "noncanonical_nonsynonymous_unwrangleable"
    ),
    "any": "",
    "any status": "",
}


@dataclass
class TagRef:
    """A reference to an AO3 tag (name + URL)."""

    name: str
    url: str
    href: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "url": self.url, "href": self.href}


@dataclass
class TagProfile:
    """Deep metadata from an AO3 `/tags/...` profile page."""

    name: str
    url: str
    category: TagCategory
    canonical: bool
    filterable: bool
    description: str
    synonym_of: TagRef | None = None
    parents: list[TagRef] = field(default_factory=list)
    synonyms: list[TagRef] = field(default_factory=list)
    metatags: list[TagRef] = field(default_factory=list)
    subtags: list[TagRef] = field(default_factory=list)
    children: dict[str, list[TagRef]] = field(default_factory=dict)
    children_truncated: bool = False
    works_url: str | None = None
    bookmarks_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "category": self.category,
            "canonical": self.canonical,
            "filterable": self.filterable,
            "description": self.description,
            "synonym_of": self.synonym_of.to_dict() if self.synonym_of else None,
            "parents": [t.to_dict() for t in self.parents],
            "synonyms": [t.to_dict() for t in self.synonyms],
            "metatags": [t.to_dict() for t in self.metatags],
            "subtags": [t.to_dict() for t in self.subtags],
            "children": {
                key: [t.to_dict() for t in refs] for key, refs in self.children.items()
            },
            "children_truncated": self.children_truncated,
            "works_url": self.works_url,
            "bookmarks_url": self.bookmarks_url,
        }

    def metatag_names(self) -> list[str]:
        """Names from the AO3 Metatags section (tree order, already unique)."""
        return [ref.name for ref in self.metatags if ref.name.strip()]

    def synonym_map(self) -> dict[str, str]:
        """Map synonym (and self) names → canonical tag name for cleanup."""
        if self.synonym_of:
            canonical_name = self.synonym_of.name
        elif self.canonical:
            canonical_name = self.name
        else:
            return {}

        mapping = {self.name: canonical_name}
        for syn in self.synonyms:
            mapping[syn.name] = canonical_name
        return mapping


ResolveStatus = Literal["canonical", "synonym", "unmarked", "missing", "error"]


@dataclass
class ResolvedTag:
    """One work tag after wrangling lookup."""

    original: str
    resolved: str
    status: ResolveStatus
    category: TagCategory | None = None
    changed: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SimplifiedTags:
    """Result of collapsing a work's tags onto canonical forms."""

    original: list[str]
    resolved: list[ResolvedTag]
    simplified: list[str]
    dropped: list[str] = field(default_factory=list)
    inserted_metatags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "resolved": [r.to_dict() for r in self.resolved],
            "simplified": self.simplified,
            "dropped": self.dropped,
            "inserted_metatags": list(self.inserted_metatags),
        }


@dataclass
class TagCacheStats:
    """Counters for how resolution avoided (or required) network fetches."""

    memory_hits: int = 0
    disk_hits: int = 0
    fetches: int = 0
    follow_fetches: int = 0
    expired_trees: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class TagResolver:
    """Resolve tag names to canonical forms via AO3 tag profile pages.

    Uses a bidirectional cache:

    1. Raw tag miss → fetch profile.
    2. If synonym, record merger target; optionally fetch the canonical page
       to index *all* synonyms in one go.
    3. Later raw tags that appear in that synonym list resolve with no fetch.
    """

    def __init__(
        self,
        session=None,
        *,
        username: str | None = None,
        password: str | None = None,
        delay: float = 2.0,
        on_status=None,
        owns_session: bool | None = None,
        cache_path: Path | None | object = _CACHE_UNSET,
        follow_canonical: bool = True,
        persist: bool = True,
        ttl_days: float | None = DEFAULT_TAG_CACHE_TTL_DAYS,
    ) -> None:
        if cache_path is _CACHE_UNSET:
            cache_path = default_tag_cache_path() if persist else None
        self._owns_session = session is None if owns_session is None else owns_session
        self._username = username
        self._password = password
        self.session = session
        if session is not None and username and password:
            attach_credentials(session, username, password)
        self.delay = delay
        self.on_status = on_status
        self.follow_canonical = follow_canonical
        self.persist = persist and cache_path is not None
        self.ttl_days = ttl_days
        self.cache = TagCache.load(
            cache_path if self.persist else None,
            ttl_days=ttl_days if self.persist else None,
        )
        self._profiles: dict[str, TagProfile] = {}
        self._errors: dict[str, str] = {}
        self.stats = TagCacheStats(expired_trees=self.cache.expired_trees)
        if delay and delay > 0:
            configure_min_interval(delay)

    def close(self) -> None:
        if self.persist:
            self.cache.save()
        self.cache.close()
        if self._owns_session and self.session is not None:
            self.session.close()

    def __enter__(self) -> TagResolver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def warm(self, profile: TagProfile) -> None:
        """Seed the cache from an already-parsed profile (also used by tests)."""
        self.cache.remember_profile(profile)
        self._profiles[profile.name] = profile

    def _status(self, message: str) -> None:
        if self.on_status:
            self.on_status(message)

    def _ensure_session(self):
        if self.session is None:
            self.session = create_session(
                self._username, self._password, on_status=self.on_status, login=False
            )
        return self.session

    def _fetch_profile(self, name: str, *, followed: bool = False) -> TagProfile | None:
        if name in self._profiles:
            self.stats.memory_hits += 1
            return self._profiles[name]
        if name in self._errors:
            return None

        self._status(
            f"{'Following canonical' if followed else 'Fetching tag profile'}: {name}"
        )
        try:
            url = tag_url(name)
            html = get_text(
                self._ensure_session(),
                url,
                on_status=self.on_status,
                login_if_needed=True,
            )
            profile = parse_tag_page(html, url=url)
            self.warm(profile)
            self._profiles.setdefault(name, profile)
            if followed:
                self.stats.follow_fetches += 1
            else:
                self.stats.fetches += 1
            return profile
        except Exception as exc:  # noqa: BLE001 - surface per-tag failures
            self._profiles.pop(name, None)
            self._errors[name] = str(exc)
            self._status(f"Failed to resolve {name!r}: {exc}")
            return None

    def _follow_canonical(self, canonical_name: str) -> TagProfile | None:
        if not self.follow_canonical:
            return None
        if canonical_name in self._profiles:
            return self._profiles[canonical_name]
        # A synonym fetch inserts a stub canonical row (metatags NULL). Only skip
        # the network when we previously fetched that canonical page.
        if self.cache.metatags_for(canonical_name) is not None:
            self.stats.disk_hits += 1
            return None
        return self._fetch_profile(canonical_name, followed=True)

    def resolve_one(self, name: str) -> ResolvedTag:
        """Resolve a single tag name to its canonical form when possible."""
        name = name.strip()
        if not name:
            return ResolvedTag(
                original=name,
                resolved=name,
                status="missing",
                changed=False,
                error="empty tag",
            )

        cached = self.cache.lookup(name)
        if cached is not None:
            resolved_name, status = cached
            if name in self._profiles or resolved_name in self._profiles:
                self.stats.memory_hits += 1
            else:
                self.stats.disk_hits += 1
            category = self.cache.category_for(name) or self.cache.category_for(
                resolved_name
            )
            return ResolvedTag(
                original=name,
                resolved=resolved_name,
                status=status,
                category=category,  # type: ignore[arg-type]
                changed=resolved_name != name,
            )

        profile = self._fetch_profile(name)
        if profile is None:
            return ResolvedTag(
                original=name,
                resolved=name,
                status="error",
                changed=False,
                error=self._errors.get(name, "unknown error"),
            )

        if profile.synonym_of is not None:
            canonical = profile.synonym_of.name
            followed = self._follow_canonical(canonical)
            category = (
                followed.category if followed is not None else profile.category
            )
            self.cache.remember_alias(
                name, canonical, status="synonym", category=category
            )
            return ResolvedTag(
                original=name,
                resolved=canonical,
                status="synonym",
                category=category,
                changed=canonical != name,
            )

        if profile.canonical:
            self.cache.remember_alias(
                name,
                profile.name,
                status="canonical" if name == profile.name else "synonym",
                category=profile.category,
            )
            return ResolvedTag(
                original=name,
                resolved=profile.name,
                status="canonical",
                category=profile.category,
                changed=profile.name != name,
            )

        self.cache.remember_alias(
            name,
            profile.name,
            status="unmarked" if name == profile.name else "synonym",
            category=profile.category,
        )
        return ResolvedTag(
            original=name,
            resolved=name,
            status="unmarked",
            category=profile.category,
            changed=False,
        )

    def metatags_for(self, name: str) -> list[str]:
        """AO3 metatags listed on the tag's (canonical) profile page.

        Uses an in-memory profile or cached names. Legacy cache rows that
        predate metatag storage are re-fetched once. Missing cache entries
        (tests that stub ``resolve_one``) return an empty list without a
        network call.
        """
        name = name.strip()
        if not name:
            return []

        resolved = self.resolve_one(name)
        lookup = resolved.resolved or name

        names = self._metatags_from_memory(lookup)
        if names is None and lookup != name:
            names = self._metatags_from_memory(name)
        if names is not None:
            return names

        known = self.cache.metatags_for(lookup)
        if known is not None:
            return known
        known = self.cache.metatags_for(name)
        if known is not None:
            return known

        if self.cache.lookup(lookup) is None and self.cache.lookup(name) is None:
            return []

        fetched = self._follow_canonical(lookup) or self._fetch_profile(
            lookup, followed=True
        )
        if fetched is None and lookup != name:
            fetched = self._fetch_profile(name, followed=True)
        if fetched is None:
            return []
        if fetched.synonym_of is not None:
            parent = self._follow_canonical(fetched.synonym_of.name)
            src = parent or fetched
        else:
            src = fetched
        return src.metatag_names()

    def _metatags_from_memory(self, name: str) -> list[str] | None:
        profile = self._profiles.get(name)
        if profile is None:
            return None
        if profile.synonym_of is not None:
            parent = self._profiles.get(profile.synonym_of.name)
            if parent is None:
                return None
            return parent.metatag_names()
        return profile.metatag_names()

    def resolve_many(self, names: list[str]) -> list[ResolvedTag]:
        return [self.resolve_one(name) for name in names]

    def simplify(
        self,
        names: list[str],
        *,
        drop_unmarked: bool = False,
        drop_errors: bool = False,
        include_metatags: bool = True,
    ) -> SimplifiedTags:
        """Collapse tags to canonical names and dedupe.

        Synonyms become their canonical. Canonical tags stay. Unmarked tags are
        kept unless ``drop_unmarked`` is set. Failed lookups are kept unless
        ``drop_errors`` is set. When ``include_metatags`` is set, names from
        each kept **fandom** tag's AO3 Metatags section are appended if not
        already present (e.g. Spider-Man → Marvel). Character and freeform
        metatags are ignored.
        """
        original = list(names)
        resolved = self.resolve_many(original)

        simplified: list[str] = []
        dropped: list[str] = []
        seen: set[str] = set()

        kept: list[ResolvedTag] = []
        for item in resolved:
            drop = False
            if item.status == "unmarked" and drop_unmarked:
                drop = True
            if item.status == "error" and drop_errors:
                drop = True
            if item.status == "missing":
                drop = True

            if drop:
                dropped.append(item.original)
                continue
            if item.resolved in seen:
                dropped.append(item.original)
                continue
            seen.add(item.resolved)
            simplified.append(item.resolved)
            kept.append(item)

        inserted: list[str] = []
        if include_metatags:
            for item in kept:
                if not is_fandom_category(item.category):
                    continue
                for meta in self.metatags_for(item.original):
                    if not meta or meta in seen:
                        continue
                    seen.add(meta)
                    simplified.append(meta)
                    inserted.append(meta)

        return SimplifiedTags(
            original=original,
            resolved=resolved,
            simplified=simplified,
            dropped=dropped,
            inserted_metatags=inserted,
        )


@dataclass
class TagSearchCriteria:
    name: str = ""
    fandoms: str = ""
    type: str = ""
    wrangling_status: str = "canonical"
    sort_column: str = "name"
    sort_direction: str = "asc"

    def normalized_status(self) -> str:
        key = (self.wrangling_status or "").strip().lower()
        if key in WRANGLING_STATUS_VALUES:
            return WRANGLING_STATUS_VALUES[key]
        # Pass through AO3 param values directly.
        return self.wrangling_status.strip()


@dataclass
class TagSearchHit:
    name: str
    url: str
    type: str | None = None
    uses: int | None = None
    canonical: bool = False
    href: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TagSearchPage:
    hits: list[TagSearchHit]
    total_found: int | None = None
    query_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": [h.to_dict() for h in self.hits],
            "total_found": self.total_found,
            "query_url": self.query_url,
        }


@dataclass
class TagSetBlurb:
    tag_set_id: int
    name: str
    url: str
    created_on: str | None = None
    owners: list[str] = field(default_factory=list)
    summary: str | None = None
    fandoms: int | None = None
    characters: int | None = None
    relationships: int | None = None
    freeforms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TagSetsSearchPage:
    tag_sets: list[TagSetBlurb]
    total_found: int | None = None
    query_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag_sets": [t.to_dict() for t in self.tag_sets],
            "total_found": self.total_found,
            "query_url": self.query_url,
        }


@dataclass
class TagSetDetail:
    tag_set_id: int
    name: str
    url: str
    created_on: str | None = None
    maintainers: list[str] = field(default_factory=list)
    description: str | None = None
    status: str | None = None
    nominations: dict[str, int] = field(default_factory=dict)
    tags: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def encode_tag_path(name: str) -> str:
    """Encode a tag name for use in `/tags/{encoded}` paths."""
    encoded = name
    for src, dst in _TAG_PATH_REPLACEMENTS:
        encoded = encoded.replace(src, dst)
    return quote(encoded, safe="")


def decode_tag_path(path_segment: str) -> str:
    """Decode a `/tags/{segment}` path segment back to a tag name."""
    decoded = unquote(path_segment)
    for src, dst in reversed(_TAG_PATH_REPLACEMENTS):
        decoded = decoded.replace(dst, src)
    return decoded


def tag_url(name_or_url: str) -> str:
    """Build an absolute AO3 tag profile URL from a name or URL."""
    value = name_or_url.strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/tags/"):
        return urljoin(AO3_BASE, value)
    return f"{AO3_BASE}/tags/{encode_tag_path(value)}"


def tag_name_from_url(url: str) -> str | None:
    """Extract the tag name from a `/tags/...` URL if present."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "tags":
        return decode_tag_path(parts[1])
    return None


def _absolute_url(href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(AO3_BASE, href)


def _parse_tag_ref(anchor: Tag) -> TagRef | None:
    name = anchor.get_text(strip=True)
    href = anchor.get("href")
    if not name or not href:
        return None
    return TagRef(name=name, url=_absolute_url(href) or "", href=href)


def _parse_tag_list(container: Tag | None) -> list[TagRef]:
    if container is None:
        return []
    refs: list[TagRef] = []
    seen: set[str] = set()
    for anchor in container.select("a.tag"):
        ref = _parse_tag_ref(anchor)
        if ref is None or ref.name in seen:
            continue
        seen.add(ref.name)
        refs.append(ref)
    return refs


def _parse_category(text: str) -> TagCategory:
    match = CATEGORY_RE.search(text)
    if not match:
        return "Unknown"
    raw = match.group(1).strip()
    known: dict[str, TagCategory] = {
        "Fandom": "Fandom",
        "Character": "Character",
        "Relationship": "Relationship",
        "Additional Tags": "Additional Tags",
        "UnsortedTag": "UnsortedTag",
        "Unsorted Tag": "UnsortedTag",
    }
    return known.get(raw, "Unknown")


def parse_tag_page(html: str, *, url: str | None = None) -> TagProfile:
    """Parse an AO3 tag profile page into structured wrangling metadata."""
    soup = parse_html(html)
    profile = soup.select_one("div.tag.home.profile")
    if profile is None:
        raise ValueError("Not an AO3 tag profile page (missing div.tag.home.profile)")

    header = profile.select_one("div.primary.header h2, h2.heading")
    name = header.get_text(strip=True) if header else ""
    page_url = url or (tag_url(name) if name else "")

    if not name and page_url:
        name = tag_name_from_url(page_url) or ""

    paragraphs = [
        p.get_text(" ", strip=True)
        for p in profile.select(":scope > p")
        if p.get_text(strip=True)
    ]
    description = " ".join(paragraphs)
    category = _parse_category(description)
    canonical = "canonical tag" in description.lower()

    synonym_of: TagRef | None = None
    merger = profile.select_one("div.merger")
    if merger is not None:
        merge_link = merger.select_one("a.tag")
        if merge_link is not None:
            synonym_of = _parse_tag_ref(merge_link)
            canonical = False

    # Canonical and synonymous tags participate in filters; unmarked tags do not.
    if "can't be filtered" in description.lower():
        filterable = False
    else:
        filterable = canonical or synonym_of is not None

    parents = _parse_tag_list(profile.select_one("div.parent"))
    synonyms = _parse_tag_list(profile.select_one("div.synonym"))
    metatags = _parse_tag_list(profile.select_one("div.meta"))
    subtags = _parse_tag_list(profile.select_one("div.sub"))

    children: dict[str, list[TagRef]] = {}
    children_truncated = False
    child_box = profile.select_one("div.child")
    if child_box is not None:
        heading = child_box.select_one("h3")
        if heading and "first 300" in heading.get_text(" ", strip=True).lower():
            children_truncated = True
        typed = child_box.select(":scope > div.listbox")
        if typed:
            for section in typed:
                key_el = section.select_one("h4, h3")
                key = key_el.get_text(strip=True) if key_el else "Other"
                children[key] = _parse_tag_list(section)
        else:
            children["Other"] = _parse_tag_list(child_box)

    works_url = bookmarks_url = None
    for anchor in profile.select("div.primary.header a, ul.navigation a"):
        text = anchor.get_text(strip=True).lower()
        href = _absolute_url(anchor.get("href"))
        if text == "works":
            works_url = href
        elif text == "bookmarks":
            bookmarks_url = href

    return TagProfile(
        name=name,
        url=page_url,
        category=category,
        canonical=canonical,
        filterable=filterable,
        description=description,
        synonym_of=synonym_of,
        parents=parents,
        synonyms=synonyms,
        metatags=metatags,
        subtags=subtags,
        children=children,
        children_truncated=children_truncated,
        works_url=works_url,
        bookmarks_url=bookmarks_url,
    )


def _deferred_tag_session(session, username, password, on_status):
    """Anonymous session that can log in later if a tag page is locked."""
    owns = session is None
    if session is None:
        session = create_session(
            username, password, on_status=on_status, login=False
        )
    elif username and password:
        attach_credentials(session, username, password)
    return session, owns


def fetch_tag_profile(
    name_or_url: str,
    *,
    session=None,
    username: str | None = None,
    password: str | None = None,
    on_status=None,
) -> TagProfile:
    """Fetch and parse a tag profile page."""
    session, owns_session = _deferred_tag_session(
        session, username, password, on_status
    )
    url = tag_url(name_or_url)
    try:
        html = get_text(session, url, on_status=on_status, login_if_needed=True)
        return parse_tag_page(html, url=url)
    finally:
        if owns_session:
            session.close()


def build_tag_search_url(criteria: TagSearchCriteria, *, page: int | None = None) -> str:
    """Build an AO3 tag search URL from criteria."""
    params: list[tuple[str, str]] = [
        ("tag_search[name]", criteria.name),
        ("tag_search[fandoms]", criteria.fandoms),
        ("tag_search[type]", criteria.type),
        ("tag_search[wrangling_status]", criteria.normalized_status()),
        ("tag_search[sort_column]", criteria.sort_column),
        ("tag_search[sort_direction]", criteria.sort_direction),
        ("commit", "Search Tags"),
    ]
    if page is not None and page > 1:
        params.append(("page", str(page)))
    return f"{AO3_BASE}/tags/search?{urlencode(params)}"


def parse_tag_search_url(url: str) -> tuple[TagSearchCriteria, int]:
    """Parse a tag search URL into criteria + page number."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    def first(key: str, default: str = "") -> str:
        values = params.get(key)
        return values[0] if values else default

    page_raw = first("page", "1")
    try:
        page = max(1, int(page_raw))
    except ValueError:
        page = 1

    criteria = TagSearchCriteria(
        name=first("tag_search[name]"),
        fandoms=first("tag_search[fandoms]"),
        type=first("tag_search[type]"),
        wrangling_status=first("tag_search[wrangling_status]", "canonical"),
        sort_column=first("tag_search[sort_column]", "name"),
        sort_direction=first("tag_search[sort_direction]", "asc"),
    )
    return criteria, page


def parse_tag_search_page(html: str, *, query_url: str | None = None) -> TagSearchPage:
    """Parse an AO3 tag search results page."""
    soup = parse_html(html)
    hits: list[TagSearchHit] = []

    for li in soup.select("ol.tag.index li"):
        span = li.find("span")
        if span is None:
            continue
        anchor = span.select_one("a.tag")
        if anchor is None:
            continue
        ref = _parse_tag_ref(anchor)
        if ref is None:
            continue

        text = span.get_text(" ", strip=True)
        tag_type = None
        type_match = TAG_SEARCH_TYPE_RE.match(text)
        if type_match:
            tag_type = type_match.group(1)
            if tag_type.lower() == "freeform":
                tag_type = "Freeform"

        uses = None
        uses_match = USES_RE.search(text)
        if uses_match:
            uses = int(uses_match.group(1).replace(",", ""))

        classes = span.get("class") or []
        hits.append(
            TagSearchHit(
                name=ref.name,
                url=ref.url,
                href=ref.href,
                type=tag_type,
                uses=uses,
                canonical="canonical" in classes,
            )
        )

    total_found = None
    for heading in soup.select("#main h3.heading, h3.heading"):
        match = FOUND_COUNT_RE.search(heading.get_text(" ", strip=True))
        if match:
            total_found = int(match.group(1).replace(",", ""))
            break

    return TagSearchPage(hits=hits, total_found=total_found, query_url=query_url)


def fetch_tag_search(
    criteria: TagSearchCriteria,
    *,
    page: int = 1,
    session=None,
    username: str | None = None,
    password: str | None = None,
    on_status=None,
) -> TagSearchPage:
    """Fetch and parse a tag search results page."""
    session, owns_session = _deferred_tag_session(
        session, username, password, on_status
    )
    url = build_tag_search_url(criteria, page=page)
    try:
        html = get_text(session, url, on_status=on_status, login_if_needed=True)
        return parse_tag_search_page(html, query_url=url)
    finally:
        if owns_session:
            session.close()


def build_tag_sets_search_url(query: str, *, page: int | None = None) -> str:
    params: list[tuple[str, str]] = [("query", query), ("commit", "Search")]
    if page is not None and page > 1:
        params.append(("page", str(page)))
    return f"{AO3_BASE}/tag_sets?{urlencode(params)}"


def _parse_stats_dl(dl: Tag | None) -> dict[str, int]:
    stats: dict[str, int] = {}
    if dl is None:
        return stats
    for dt in dl.select("dt"):
        key = dt.get_text(strip=True).rstrip(":").lower()
        dd = dt.find_next_sibling("dd")
        if dd is None:
            continue
        raw = dd.get_text(strip=True).replace(",", "")
        try:
            stats[key] = int(raw)
        except ValueError:
            continue
    return stats


def parse_tag_sets_search_page(
    html: str, *, query_url: str | None = None
) -> TagSetsSearchPage:
    """Parse an owned tag-sets search listing."""
    soup = parse_html(html)
    results: list[TagSetBlurb] = []

    for li in soup.select("li.tagset"):
        link = li.select_one("h4.heading a[href*='/tag_sets/']")
        if link is None:
            continue
        href = link.get("href") or ""
        match = re.search(r"/tag_sets/(\d+)", href)
        if not match:
            continue
        tag_set_id = int(match.group(1))
        stats = _parse_stats_dl(li.select_one("dl.stats"))
        summary_el = li.select_one("blockquote.summary")
        summary = summary_el.get_text(" ", strip=True) if summary_el else None
        created = li.select_one("p.datetime")
        owners = [
            a.get_text(strip=True)
            for a in li.select("ul.mods a")
            if a.get_text(strip=True)
        ]
        results.append(
            TagSetBlurb(
                tag_set_id=tag_set_id,
                name=link.get_text(strip=True),
                url=_absolute_url(href) or "",
                created_on=created.get_text(strip=True) if created else None,
                owners=owners,
                summary=summary or None,
                fandoms=stats.get("fandoms"),
                characters=stats.get("characters"),
                relationships=stats.get("relationships"),
                freeforms=stats.get("additional tags"),
            )
        )

    total_found = None
    for heading in soup.select("#main h2.heading, h2.heading"):
        match = TAG_SETS_COUNT_RE.search(heading.get_text(" ", strip=True))
        if match:
            total_found = int(match.group(1).replace(",", ""))
            break

    return TagSetsSearchPage(
        tag_sets=results, total_found=total_found, query_url=query_url
    )


def fetch_tag_sets_search(
    query: str,
    *,
    page: int = 1,
    session=None,
    username: str | None = None,
    password: str | None = None,
    on_status=None,
) -> TagSetsSearchPage:
    session, owns_session = _deferred_tag_session(
        session, username, password, on_status
    )
    url = build_tag_sets_search_url(query, page=page)
    try:
        html = get_text(session, url, on_status=on_status, login_if_needed=True)
        return parse_tag_sets_search_page(html, query_url=url)
    finally:
        if owns_session:
            session.close()


def _parse_tagset_nested_list(ol: Tag | None) -> list[Any]:
    """Parse nested tag-set listing lists into plain structures."""
    if ol is None:
        return []
    items: list[Any] = []
    for li in ol.find_all("li", recursive=False):
        nested = li.find("ol", recursive=False)
        heading = li.select_one(":scope > h3.heading, :scope > h4.heading")
        if nested is not None:
            title = heading.get_text(" ", strip=True) if heading else ""
            # Drop expand/contract button text noise.
            title = re.sub(r"[\u2193\u2191\u21c4]+", "", title)
            title = re.sub(r"\bExpand All\b|\bContract All\b", "", title)
            title = re.sub(r"\s+", " ", title).strip()
            items.append({"name": title, "items": _parse_tagset_nested_list(nested)})
            continue
        text = li.get_text(" ", strip=True)
        if text:
            items.append(text)
    return items


def parse_tag_set_page(html: str, *, url: str | None = None) -> TagSetDetail:
    """Parse an owned tag set detail page."""
    soup = parse_html(html)
    home = soup.select_one("div.tagset.home")
    if home is None:
        raise ValueError("Not an AO3 tag set page (missing div.tagset.home)")

    heading = home.select_one("h2.heading")
    raw_name = heading.get_text(strip=True) if heading else ""
    name = re.sub(r"^About\s+", "", raw_name).strip() or raw_name

    page_url = url or ""
    tag_set_id = 0
    if page_url:
        match = re.search(r"/tag_sets/(\d+)", page_url)
        if match:
            tag_set_id = int(match.group(1))

    meta = home.select_one("dl.meta")
    created_on = None
    maintainers: list[str] = []
    description = None
    status = None
    nominations: dict[str, int] = {}

    if meta is not None:
        created = meta.select_one("dd.datetime")
        created_on = created.get_text(strip=True) if created else None
        maintainers = [
            a.get_text(strip=True)
            for a in meta.select("ul.mods a")
            if a.get_text(strip=True)
        ]
        # Description is the dd after the Description dt.
        for dt in meta.select("dt"):
            label = dt.get_text(strip=True).rstrip(":").lower()
            dd = dt.find_next_sibling("dd")
            if dd is None:
                continue
            if label == "description":
                description = dd.get_text(" ", strip=True) or None
            elif label == "status":
                status = dd.get_text(" ", strip=True) or None
            elif label == "stats":
                nominations = _parse_stats_dl(dd.select_one("dl.stats"))

    tags: dict[str, Any] = {}
    listing = home.select_one("ol.tagset.index")
    if listing is not None:
        for li in listing.find_all("li", recursive=False):
            heading_el = li.select_one(":scope > h3.heading")
            section_name = (
                heading_el.get_text(" ", strip=True) if heading_el else "Section"
            )
            section_name = re.sub(r"[\u2193\u2191\u21c4]+", "", section_name)
            section_name = re.sub(r"\bExpand All\b|\bContract All\b", "", section_name)
            section_name = re.sub(r"\s+", " ", section_name).strip()
            nested = li.find("ol", recursive=False)
            tags[section_name] = _parse_tagset_nested_list(nested)

    return TagSetDetail(
        tag_set_id=tag_set_id,
        name=name,
        url=page_url,
        created_on=created_on,
        maintainers=maintainers,
        description=description,
        status=status,
        nominations=nominations,
        tags=tags,
    )


def fetch_tag_set(
    tag_set_id: int,
    *,
    session=None,
    username: str | None = None,
    password: str | None = None,
    on_status=None,
) -> TagSetDetail:
    session, owns_session = _deferred_tag_session(
        session, username, password, on_status
    )
    url = f"{AO3_BASE}/tag_sets/{tag_set_id}"
    try:
        html = get_text(session, url, on_status=on_status, login_if_needed=True)
        return parse_tag_set_page(html, url=url)
    finally:
        if owns_session:
            session.close()


def _print_json(data: Any) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _cache_path_from_args(args: argparse.Namespace, *, enabled: bool = True) -> Path | None:
    if not enabled:
        return None
    if getattr(args, "cache", None) is not None:
        return Path(args.cache)
    return default_tag_cache_path()


def _add_auth_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--username", help="AO3 username (optional)")
    parser.add_argument("--password", help="AO3 password (optional)")


def _add_cache_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Tag cache SQLite path (default: XDG cache dir / wranglekit)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable persistent tag cache for this run",
    )
    parser.add_argument(
        "--cache-ttl-days",
        type=float,
        default=None,
        help=(
            "Expire synonym/canonical trees older than this many days "
            f"(default: config or {DEFAULT_TAG_CACHE_TTL_DAYS}; 0 = never)"
        ),
    )


def _add_metatags_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-metatags",
        action="store_true",
        help=(
            "Do not append AO3 fandom metatags (e.g. Marvel for "
            "Spider-Man - All Media Types)"
        ),
    )


def _include_metatags(args: argparse.Namespace, user_cfg) -> bool:
    if getattr(args, "no_metatags", False):
        return False
    return bool(user_cfg.settings.include_metatags)


def _resolve_ttl_days(args: argparse.Namespace, user_cfg=None) -> float | None:
    if getattr(args, "cache_ttl_days", None) is not None:
        return float(args.cache_ttl_days)
    if user_cfg is not None:
        return float(user_cfg.settings.tag_cache_ttl_days)
    return DEFAULT_TAG_CACHE_TTL_DAYS


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "warm":
        from ao3kit.tags.warm import main as warm_main

        return warm_main(argv[1:])
    if argv and argv[0] == "graph":
        from ao3kit.tags.graph import main as graph_main

        return graph_main(argv[1:])

    parser = argparse.ArgumentParser(
        description="Extract AO3 tag wrangling metadata (profiles, search, tag sets)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    tag_p = sub.add_parser("tag", help="Fetch a tag profile by name or URL")
    tag_p.add_argument("tag", help="Tag name or /tags/... URL")
    tag_p.add_argument(
        "--synonym-map",
        action="store_true",
        help="Print only synonym→canonical name map",
    )
    _add_auth_args(tag_p)

    search_p = sub.add_parser("search", help="Search tags (AO3 tag search)")
    search_p.add_argument("--name", default="", help="Tag name query")
    search_p.add_argument("--fandoms", default="", help="Restrict to fandom(s)")
    search_p.add_argument(
        "--type",
        default="",
        choices=TAG_TYPE_VALUES,
        help="Tag type filter",
    )
    search_p.add_argument(
        "--status",
        default="canonical",
        help="Wrangling status (canonical, synonymous, noncanonical, ...)",
    )
    search_p.add_argument("--sort", default="name", dest="sort_column")
    search_p.add_argument(
        "--direction", default="asc", choices=("asc", "desc"), dest="sort_direction"
    )
    search_p.add_argument("--page", type=int, default=1)
    search_p.add_argument("--url", help="Existing tag search URL (overrides flags)")
    _add_auth_args(search_p)

    sets_p = sub.add_parser("tag-sets", help="Search owned tag sets by name")
    sets_p.add_argument("query", help="Tag set name query")
    sets_p.add_argument("--page", type=int, default=1)
    _add_auth_args(sets_p)

    set_p = sub.add_parser("tag-set", help="Fetch an owned tag set by ID")
    set_p.add_argument("tag_set_id", type=int)
    _add_auth_args(set_p)

    resolve_p = sub.add_parser(
        "resolve",
        help="Resolve tag names (or a work's tags) to canonical forms",
    )
    resolve_p.add_argument(
        "tags",
        nargs="*",
        help="Tag names to resolve (optional if --jsonl/--work-id used)",
    )
    resolve_p.add_argument(
        "--jsonl",
        help="JSONL file of scraped works; resolve tags for matching works",
    )
    resolve_p.add_argument(
        "--work-id",
        help="With --jsonl, only resolve this work_id",
    )
    resolve_p.add_argument(
        "--drop-unmarked",
        action="store_true",
        help="Omit tags that are not canonical and not synonyms",
    )
    resolve_p.add_argument(
        "--include-fandoms",
        action="store_true",
        help="Also resolve/include work fandoms in the simplified list",
    )
    resolve_p.add_argument(
        "--include-relationships",
        action="store_true",
        help="Also resolve work relationship tags (from the relationships field)",
    )
    resolve_p.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Minimum seconds between AO3 requests (app-wide; ~2s default, heavier for search/downloads)",
    )
    resolve_p.add_argument(
        "--verbose",
        action="store_true",
        help="Print fetch progress to stderr",
    )
    _add_cache_args(resolve_p)
    resolve_p.add_argument(
        "--no-follow-canonical",
        action="store_true",
        help="Do not fetch canonical pages to fan-out synonym maps",
    )
    _add_metatags_arg(resolve_p)
    _add_auth_args(resolve_p)

    apply_p = sub.add_parser(
        "apply",
        help="Apply tag mapping/collection rules (after canonical resolve)",
    )
    apply_p.add_argument(
        "--rules",
        type=Path,
        help="Rules module (.py) or YAML; default: active rules from user config",
    )
    apply_p.add_argument("tags", nargs="*", help="Tag names (optional with --jsonl)")
    apply_p.add_argument("--jsonl", help="JSONL of scraped works")
    apply_p.add_argument("--work-id", help="Only this work_id from --jsonl")
    apply_p.add_argument(
        "--include-fandoms",
        action="store_true",
        help="Also run rules against work fandoms",
    )
    apply_p.add_argument(
        "--include-relationships",
        action="store_true",
        help="Also run rules against work relationships",
    )
    apply_p.add_argument(
        "--collections-only",
        action="store_true",
        help="Print collection assignments only (JSONL: work_id, title, collections)",
    )
    apply_p.add_argument("--delay", type=float, default=None)
    apply_p.add_argument("--verbose", action="store_true")
    _add_cache_args(apply_p)
    apply_p.add_argument("--no-follow-canonical", action="store_true")
    _add_metatags_arg(apply_p)
    _add_auth_args(apply_p)

    sub.add_parser(
        "warm",
        help="Background-fetch uncached tags into the SQLite cache",
    )
    sub.add_parser(
        "graph",
        help="Graph tag relationships (synonyms, metatags) from the cache",
    )

    enrich_p = sub.add_parser(
        "enrich",
        help="Enrich scrape JSONL with cleaned tags; prints a remapping summary",
    )
    enrich_p.add_argument(
        "--jsonl",
        required=True,
        help="Input scrape JSONL",
    )
    enrich_p.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output JSONL path (enriched records)",
    )
    enrich_p.add_argument(
        "--rules",
        type=Path,
        help="Rules module; default: active rules from user config",
    )
    enrich_p.add_argument("--delay", type=float, default=None)
    enrich_p.add_argument("--verbose", action="store_true")
    _add_cache_args(enrich_p)
    enrich_p.add_argument("--no-follow-canonical", action="store_true")
    enrich_p.add_argument(
        "--no-fandoms",
        action="store_true",
        help="Do not simplify fandoms",
    )
    enrich_p.add_argument(
        "--no-relationships",
        action="store_true",
        help="Do not simplify relationships",
    )
    _add_metatags_arg(enrich_p)
    _add_auth_args(enrich_p)

    coll_p = sub.add_parser(
        "collections",
        help="Compute collection membership from rules (no AO3 tag fetch)",
    )
    coll_p.add_argument("--jsonl", required=True, help="Input JSONL")
    coll_p.add_argument("-o", "--output", required=True, help="Output JSONL")
    coll_p.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress to stderr",
    )
    coll_p.add_argument(
        "--no-capture-pins",
        action="store_true",
        help="Do not save hand-added collections as per-work pin rules",
    )
    coll_p.add_argument(
        "--explain",
        action="store_true",
        help="Write JSON explaining which rules put each work in each collection",
    )
    # Accepted for older plugin argv; collections no longer fetch AO3 or map tags.
    coll_p.add_argument("--rules", type=Path, help=argparse.SUPPRESS)
    coll_p.add_argument("--delay", type=float, default=None, help=argparse.SUPPRESS)
    coll_p.add_argument("--no-follow-canonical", action="store_true", help=argparse.SUPPRESS)
    coll_p.add_argument("--no-fandoms", action="store_true", help=argparse.SUPPRESS)
    coll_p.add_argument("--no-relationships", action="store_true", help=argparse.SUPPRESS)
    _add_cache_args(coll_p)
    _add_metatags_arg(coll_p)
    _add_auth_args(coll_p)

    args = parser.parse_args(argv)

    if args.command == "tag":
        profile = fetch_tag_profile(
            args.tag, username=args.username, password=args.password
        )
        if args.synonym_map:
            _print_json(profile.synonym_map())
        else:
            _print_json(profile.to_dict())
        return 0

    if args.command == "search":
        if args.url:
            criteria, page = parse_tag_search_url(args.url)
        else:
            criteria = TagSearchCriteria(
                name=args.name,
                fandoms=args.fandoms,
                type=args.type,
                wrangling_status=args.status,
                sort_column=args.sort_column,
                sort_direction=args.sort_direction,
            )
            page = args.page
        page_data = fetch_tag_search(
            criteria,
            page=page,
            username=args.username,
            password=args.password,
        )
        _print_json(page_data.to_dict())
        return 0

    if args.command == "tag-sets":
        page_data = fetch_tag_sets_search(
            args.query,
            page=args.page,
            username=args.username,
            password=args.password,
        )
        _print_json(page_data.to_dict())
        return 0

    if args.command == "tag-set":
        detail = fetch_tag_set(
            args.tag_set_id, username=args.username, password=args.password
        )
        _print_json(detail.to_dict())
        return 0

    if args.command == "resolve":
        from ao3kit.config import load_user_config

        on_status = (lambda msg: print(msg, file=sys.stderr)) if args.verbose else None
        cache_path = _cache_path_from_args(args, enabled=not args.no_cache)
        user_cfg = load_user_config(ensure=True)
        with TagResolver(
            username=args.username,
            password=args.password,
            delay=args.delay,
            on_status=on_status,
            cache_path=cache_path,
            follow_canonical=not args.no_follow_canonical,
            persist=cache_path is not None,
            ttl_days=_resolve_ttl_days(args, user_cfg),
        ) as resolver:
            if args.jsonl:
                results = []
                for line in Path(args.jsonl).read_text().splitlines():
                    if not line.strip():
                        continue
                    work = json.loads(line)
                    if args.work_id and str(work.get("work_id")) != str(args.work_id):
                        continue
                    tags = list(work.get("tags") or [])
                    payload = {
                        "work_id": work.get("work_id"),
                        "title": work.get("title"),
                        "tags": resolver.simplify(
                            tags,
                            drop_unmarked=args.drop_unmarked,
                            include_metatags=False,
                        ).to_dict(),
                    }
                    if args.include_fandoms:
                        payload["fandoms"] = resolver.simplify(
                            list(work.get("fandoms") or []),
                            include_metatags=_include_metatags(args, user_cfg),
                        ).to_dict()
                    else:
                        payload["fandoms"] = work.get("fandoms")
                    if args.include_relationships:
                        payload["relationships"] = resolver.simplify(
                            list(work.get("relationships") or []),
                            include_metatags=False,
                        ).to_dict()
                    else:
                        payload["relationships"] = work.get("relationships")
                    results.append(payload)
                    if args.work_id:
                        break
                if not results:
                    print("No matching works found.", file=sys.stderr)
                    return 1
                if args.verbose:
                    print(
                        f"Cache stats: {resolver.stats.to_dict()}",
                        file=sys.stderr,
                    )
                _print_json(results if not args.work_id else results[0])
                return 0

            if not args.tags:
                resolve_p.error("Provide tag names and/or --jsonl")
            simplified = resolver.simplify(
                list(args.tags),
                drop_unmarked=args.drop_unmarked,
                include_metatags=_include_metatags(args, user_cfg),
            )
            if args.verbose:
                print(f"Cache stats: {resolver.stats.to_dict()}", file=sys.stderr)
            _print_json(simplified.to_dict())
            return 0

    if args.command == "apply":
        from ao3kit.config import load_user_config
        from ao3kit.tags.rules import TagRulesEngine, load_tag_rules

        on_status = (lambda msg: print(msg, file=sys.stderr)) if args.verbose else None
        user_cfg = load_user_config(ensure=True)
        if args.rules is not None:
            rules = user_cfg.with_mappings(load_tag_rules(args.rules))
        else:
            rules = user_cfg.load_active_rules()
            if args.verbose:
                print(
                    f"Using active rules: {user_cfg.active_rules_path()}",
                    file=sys.stderr,
                )

        # Prefer explicit flags; otherwise inherit user settings.
        rules.resolve_canonical = (
            rules.resolve_canonical
            if args.rules is not None
            else user_cfg.settings.resolve_canonical
        )
        if args.rules is None:
            rules.drop_unmarked = user_cfg.settings.drop_unmarked
            rules.drop_errors = user_cfg.settings.drop_errors
        rules.include_metatags = _include_metatags(args, user_cfg)

        delay = (
            args.delay
            if args.delay is not None
            else user_cfg.settings.request_delay
        )
        follow_canonical = (
            False
            if args.no_follow_canonical
            else user_cfg.settings.follow_canonical
        )
        use_cache = (not args.no_cache) and user_cfg.settings.tag_cache_enabled
        cache_path = _cache_path_from_args(args, enabled=use_cache)

        with TagResolver(
            username=args.username,
            password=args.password,
            delay=delay,
            on_status=on_status,
            cache_path=cache_path,
            follow_canonical=follow_canonical,
            persist=cache_path is not None,
            ttl_days=_resolve_ttl_days(args, user_cfg),
        ) as resolver:
            engine = TagRulesEngine(rules, resolver)
            include_fandoms = bool(args.include_fandoms or args.collections_only)
            include_relationships = bool(
                args.include_relationships or args.collections_only
            )
            if args.jsonl:
                results = []
                for line in Path(args.jsonl).read_text().splitlines():
                    if not line.strip():
                        continue
                    work = json.loads(line)
                    if args.work_id and str(work.get("work_id")) != str(args.work_id):
                        continue
                    tag_result = engine.apply(
                        list(work.get("tags") or []), include_metatags=False
                    )
                    fandom_result = None
                    if include_fandoms:
                        fandom_result = engine.apply(list(work.get("fandoms") or []))
                    rel_result = None
                    if include_relationships:
                        rel_result = engine.apply(
                            list(work.get("relationships") or []),
                            include_metatags=False,
                        )
                    if args.collections_only:
                        from ao3kit.tags.clean import merge_collection_maps

                        merged = merge_collection_maps(
                            tag_result.collections,
                            None if fandom_result is None else fandom_result.collections,
                            None if rel_result is None else rel_result.collections,
                        )
                        payload: dict[str, Any] = {
                            "work_id": work.get("work_id"),
                            "title": work.get("title"),
                            "collections": list(merged),
                        }
                    else:
                        payload = {
                            "work_id": work.get("work_id"),
                            "title": work.get("title"),
                            "tags": tag_result.to_dict(),
                        }
                        if include_fandoms and fandom_result is not None:
                            payload["fandoms"] = fandom_result.to_dict()
                        else:
                            payload["fandoms"] = work.get("fandoms")
                        if include_relationships and rel_result is not None:
                            payload["relationships"] = rel_result.to_dict()
                        else:
                            payload["relationships"] = work.get("relationships")
                    results.append(payload)
                    if args.work_id:
                        break
                if not results:
                    print("No matching works found.", file=sys.stderr)
                    return 1
                if args.verbose:
                    print(
                        f"Cache stats: {resolver.stats.to_dict()}",
                        file=sys.stderr,
                    )
                _print_json(results if not args.work_id else results[0])
                return 0

            if not args.tags:
                apply_p.error("Provide tag names and/or --jsonl")
            outcome = engine.apply(list(args.tags))
            if args.verbose:
                print(f"Cache stats: {resolver.stats.to_dict()}", file=sys.stderr)
            if args.collections_only:
                from ao3kit.tags.clean import collection_names_from_result

                _print_json({"collections": collection_names_from_result(outcome)})
            else:
                _print_json(outcome.to_dict())
            return 0

    if args.command == "enrich":
        from ao3kit.config import load_user_config
        from ao3kit.tags.clean import enrich_records
        from ao3kit.tags.rules import load_tag_rules

        on_status = (lambda msg: print(msg, file=sys.stderr)) if args.verbose else None
        user_cfg = load_user_config(ensure=True)
        rules = (
            user_cfg.with_mappings(load_tag_rules(args.rules))
            if args.rules is not None
            else user_cfg.load_active_rules()
        )
        if args.rules is None:
            rules.resolve_canonical = user_cfg.settings.resolve_canonical
            rules.drop_unmarked = user_cfg.settings.drop_unmarked
            rules.drop_errors = user_cfg.settings.drop_errors
        rules.include_metatags = _include_metatags(args, user_cfg)

        delay = (
            args.delay
            if args.delay is not None
            else user_cfg.settings.request_delay
        )
        follow_canonical = (
            False
            if args.no_follow_canonical
            else user_cfg.settings.follow_canonical
        )
        use_cache = (not args.no_cache) and user_cfg.settings.tag_cache_enabled
        cache_path = _cache_path_from_args(args, enabled=use_cache)

        records: list[dict[str, Any]] = []
        for line in Path(args.jsonl).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(json.loads(line))

        username = args.username or os.environ.get("AO3_USERNAME")
        password = args.password or os.environ.get("AO3_PASSWORD")
        with TagResolver(
            username=username,
            password=password,
            delay=delay,
            on_status=on_status,
            cache_path=cache_path,
            follow_canonical=follow_canonical,
            persist=cache_path is not None,
            ttl_days=_resolve_ttl_days(args, user_cfg),
        ) as resolver:
            enriched = enrich_records(
                records,
                rules=rules,
                resolver=resolver,
                include_fandoms=not args.no_fandoms,
                include_relationships=not args.no_relationships,
                on_status=on_status,
            )
            if args.verbose:
                print(f"Cache stats: {resolver.stats.to_dict()}", file=sys.stderr)

        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            for record in enriched:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if args.verbose:
            print(f"Wrote {len(enriched)} enriched works to {out_path}", file=sys.stderr)
        from ao3kit.tags.clean import emit_remapping_summary

        emit_remapping_summary(enriched, lambda msg: print(msg, file=sys.stderr))
        return 0

    if args.command == "collections":
        from ao3kit.config import load_user_config
        from ao3kit.tags.collections import recompute_records

        on_status = (lambda msg: print(msg, file=sys.stderr)) if args.verbose else None
        user_cfg = load_user_config(ensure=True)
        records = []
        for line in Path(args.jsonl).read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        if args.explain:
            from ao3kit.tags.collections import explain_records

            payload = explain_records(records, user_cfg.load_collection_rules())
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            if on_status:
                on_status(
                    f"Wrote collection explanations for {len(payload)} work(s) "
                    f"to {out_path}"
                )
            return 0
        if on_status:
            on_status(
                f"Computing collections for {len(records)} work(s) from rules "
                "(no AO3 tag lookup)."
            )
        remember = bool(user_cfg.settings.collections_remember_manual_adds) and (
            not args.no_capture_pins
        )
        overlayed, coll_rules, pins = recompute_records(
            records,
            user_cfg.load_collection_rules(),
            remember_adds=remember,
        )
        if pins:
            user_cfg.save_collection_rules(coll_rules)
            if on_status:
                on_status(
                    f"Saved {len(pins)} collection pin(s) from hand-added membership."
                )
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            for record in overlayed:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if on_status:
            print(
                f"Wrote {len(overlayed)} works with computed collections to {out_path}",
                file=sys.stderr,
            )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
