"""Durable SQLite tag cache with TTL-based tree expiry.

Replaces the flat JSON cache. Each synonym fan-out from a canonical profile is
one *tree* (rows sharing ``root`` + the same ``fetched_at``). Expired trees are
deleted so stale wrangling data is re-fetched from AO3.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Literal

if TYPE_CHECKING:
    from ao3kit.tags.metadata import ResolveStatus, TagProfile

TAG_CACHE_VERSION = 3
DEFAULT_TAG_CACHE_TTL_DAYS = 90.0


def default_tag_cache_path() -> Path:
    from ao3kit.paths import tag_cache_file

    return tag_cache_file()


def _legacy_json_path() -> Path:
    from ao3kit.paths import tag_cache_legacy_json

    return tag_cache_legacy_json()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
  name TEXT PRIMARY KEY,
  canonical TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('canonical', 'synonym', 'unmarked')),
  category TEXT,
  root TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  metatags TEXT
);

CREATE INDEX IF NOT EXISTS idx_entries_root ON entries(root);
CREATE INDEX IF NOT EXISTS idx_entries_fetched_at ON entries(fetched_at);
"""

_LOOKUP_CHUNK = 400


@dataclass(frozen=True)
class CacheRow:
    """One tag-cache row (name → canonical, plus stored metatags)."""

    name: str
    canonical: str
    status: str
    category: str | None
    metatags: list[str] | None


def _parse_metatags_json(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    try:
        names = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(names, list):
        return None
    return [str(item) for item in names if str(item).strip()]


def _row_to_cache_row(row: sqlite3.Row) -> CacheRow:
    category = row["category"]
    return CacheRow(
        name=str(row["name"]),
        canonical=str(row["canonical"]),
        status=str(row["status"]),
        category=str(category) if category else None,
        metatags=_parse_metatags_json(row["metatags"]),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _synonym_map_from_profile(profile: Any) -> dict[str, str]:
    """Name → canonical map from a profile, without requiring TagProfile methods."""
    method = getattr(profile, "synonym_map", None)
    if callable(method):
        mapping = method()
        if isinstance(mapping, dict):
            return {str(key): str(value) for key, value in mapping.items()}
    synonym_of = getattr(profile, "synonym_of", None)
    if synonym_of is not None:
        canonical_name = str(getattr(synonym_of, "name", "") or synonym_of)
    elif getattr(profile, "canonical", False):
        canonical_name = str(getattr(profile, "name", "") or "")
    else:
        return {}
    if not canonical_name:
        return {}
    mapping = {str(profile.name): canonical_name}
    for syn in getattr(profile, "synonyms", None) or []:
        syn_name = str(getattr(syn, "name", "") or syn)
        if syn_name:
            mapping[syn_name] = canonical_name
    return mapping


def resolve_cache_path(path: Path | None) -> Path | None:
    """Normalize a cache path; ``*.json`` becomes sibling ``*.sqlite``."""
    if path is None:
        return None
    path = Path(path)
    if path.suffix.lower() == ".json":
        return path.with_suffix(".sqlite")
    if path.suffix.lower() not in {".sqlite", ".db", ".sqlite3"}:
        # Treat extensionless / odd paths as a sqlite file as-is.
        return path
    return path


class TagCache:
    """SQLite-backed tag→canonical cache with optional TTL expiry.

    Lookups hit the database (not a fully loaded in-memory map), so the cache
    can grow large without blowing process RAM. Writes are transactional.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        ttl_days: float | None = DEFAULT_TAG_CACHE_TTL_DAYS,
    ) -> None:
        self.path = resolve_cache_path(path)
        self.ttl_days = ttl_days
        self._conn: sqlite3.Connection | None = None
        self.dirty = False
        self.expired_trees = 0
        self.expired_rows = 0

    @classmethod
    def load(
        cls,
        path: Path | None,
        *,
        ttl_days: float | None = DEFAULT_TAG_CACHE_TTL_DAYS,
    ) -> TagCache:
        cache = cls(path=path, ttl_days=ttl_days)
        cache._open()
        cache._maybe_migrate_legacy_json()
        cache.purge_expired()
        return cache

    def _open(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if self.path is None:
            conn = sqlite3.connect(":memory:")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        if self.path is not None:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'version'"
        ).fetchone()
        if version is None:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('version', ?)",
                (str(TAG_CACHE_VERSION),),
            )
            conn.commit()
        self._migrate_schema(conn)
        self._conn = conn
        return conn

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the original SQLite cache."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(entries)").fetchall()}
        if "metatags" not in cols:
            conn.execute("ALTER TABLE entries ADD COLUMN metatags TEXT")
        version_row = conn.execute(
            "SELECT value FROM meta WHERE key = 'version'"
        ).fetchone()
        current = int(version_row["value"]) if version_row else 0
        if current < TAG_CACHE_VERSION:
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('version', ?)",
                (str(TAG_CACHE_VERSION),),
            )
        conn.commit()

    def _maybe_migrate_legacy_json(self) -> None:
        """Import v1 JSON cache once if present beside the sqlite file."""
        if self.path is None:
            return
        if self._conn is None:
            self._open()
        assert self._conn is not None
        count = self._conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()
        if count and int(count["n"]) > 0:
            return

        candidates: list[Path] = []
        json_sibling = self.path.with_suffix(".json")
        candidates.append(json_sibling)
        if self.path.resolve() == default_tag_cache_path().resolve():
            candidates.append(_legacy_json_path())

        for json_path in candidates:
            if not json_path.is_file():
                continue
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if int(data.get("version", 0)) not in {1, TAG_CACHE_VERSION}:
                continue
            self._import_json_payload(data)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('migrated_from', ?)",
                (str(json_path),),
            )
            self._conn.commit()
            return

    def _import_json_payload(self, data: dict[str, Any]) -> None:
        conn = self._open()
        canonical_for = {
            str(k): str(v) for k, v in (data.get("canonical_for") or {}).items()
        }
        unmarked = {str(x) for x in (data.get("unmarked") or [])}
        categories = {
            str(k): str(v) for k, v in (data.get("categories") or {}).items()
        }
        fetched_at = {
            str(k): str(v) for k, v in (data.get("fetched_at") or {}).items()
        }
        fallback_ts = data.get("updated_at") or _utc_now().isoformat()

        rows: list[tuple[str, str, str, str | None, str, str, str | None]] = []
        seen: set[str] = set()

        for name, canonical in canonical_for.items():
            status = "canonical" if canonical == name else "synonym"
            root = canonical
            ts = fetched_at.get(name) or fetched_at.get(canonical) or fallback_ts
            rows.append(
                (name, canonical, status, categories.get(name), root, ts, None)
            )
            seen.add(name)

        for name in unmarked:
            if name in seen:
                continue
            ts = fetched_at.get(name) or fallback_ts
            rows.append(
                (name, name, "unmarked", categories.get(name), name, ts, None)
            )

        if not rows:
            return
        conn.executemany(
            """
            INSERT OR REPLACE INTO entries
              (name, canonical, status, category, root, fetched_at, metatags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        self.dirty = False

    def close(self) -> None:
        self.save()
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def save(self) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('updated_at', ?)",
            (_utc_now().isoformat(),),
        )
        self._conn.commit()
        self.dirty = False

    def purge_expired(self) -> int:
        """Delete expired *trees* (all rows sharing an expired ``root``).

        A tree is expired when its newest ``fetched_at`` among root-linked rows
        is older than ``ttl_days``. Returns number of deleted rows.
        """
        if self.ttl_days is None or self.ttl_days <= 0:
            return 0
        conn = self._open()
        cutoff = (_utc_now() - timedelta(days=float(self.ttl_days))).isoformat()
        expired_roots = [
            row["root"]
            for row in conn.execute(
                """
                SELECT root, MAX(fetched_at) AS newest
                FROM entries
                GROUP BY root
                HAVING newest < ?
                """,
                (cutoff,),
            ).fetchall()
        ]
        if not expired_roots:
            return 0
        deleted = 0
        for root in expired_roots:
            cur = conn.execute("DELETE FROM entries WHERE root = ?", (root,))
            deleted += cur.rowcount
        conn.commit()
        self.expired_trees += len(expired_roots)
        self.expired_rows += deleted
        self.dirty = True
        return deleted

    def lookup(self, name: str) -> tuple[str, ResolveStatus] | None:
        """Return (resolved_name, status) if present and not past TTL."""
        conn = self._open()
        row = conn.execute(
            "SELECT canonical, status, fetched_at, root FROM entries WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        if self._row_expired(conn, row):
            conn.execute("DELETE FROM entries WHERE root = ?", (row["root"],))
            conn.commit()
            self.expired_trees += 1
            return None
        status: ResolveStatus = row["status"]  # type: ignore[assignment]
        return str(row["canonical"]), status

    def category_for(self, name: str) -> str | None:
        conn = self._open()
        row = conn.execute(
            "SELECT category FROM entries WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        value = row["category"]
        return str(value) if value else None

    def metatags_for(self, name: str) -> list[str] | None:
        """Return stored metatag names, or ``None`` if unknown / not cached.

        ``None`` means we have never recorded metatags (legacy rows). An empty
        list means the profile was fetched and had no Metatags section.
        Looked up on the canonical/root row so synonyms share one list.
        """
        conn = self._open()
        row = conn.execute(
            "SELECT canonical, metatags FROM entries WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        canonical = str(row["canonical"])
        raw = row["metatags"]
        if canonical != name:
            canon_row = conn.execute(
                "SELECT metatags FROM entries WHERE name = ?",
                (canonical,),
            ).fetchone()
            if canon_row is not None:
                raw = canon_row["metatags"]
        return _parse_metatags_json(raw)

    def get_row(self, name: str) -> CacheRow | None:
        """Return the cache row for ``name``, or ``None`` if uncached."""
        rows = self.get_rows([name])
        return rows.get(name)

    def get_rows(self, names: Iterable[str]) -> dict[str, CacheRow]:
        """Lookup many names. Missing names are omitted."""
        wanted: list[str] = []
        seen: set[str] = set()
        for name in names:
            text = str(name).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            wanted.append(text)
        if not wanted:
            return {}
        conn = self._open()
        found: dict[str, CacheRow] = {}
        for start in range(0, len(wanted), _LOOKUP_CHUNK):
            chunk = wanted[start : start + _LOOKUP_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"""
                SELECT name, canonical, status, category, metatags
                FROM entries
                WHERE name IN ({placeholders})
                """,
                chunk,
            ):
                parsed = _row_to_cache_row(row)
                found[parsed.name] = parsed
        return found

    def rows_for_canonical(self, canonical: str) -> list[CacheRow]:
        """Canonical row plus every cached synonym of that name."""
        text = str(canonical).strip()
        if not text:
            return []
        conn = self._open()
        return [
            _row_to_cache_row(row)
            for row in conn.execute(
                """
                SELECT name, canonical, status, category, metatags
                FROM entries
                WHERE canonical = ?
                ORDER BY name
                """,
                (text,),
            )
        ]

    def iter_root_rows(self) -> Iterator[CacheRow]:
        """Canonical and unmarked rows (one per synonym tree)."""
        conn = self._open()
        for row in conn.execute(
            """
            SELECT name, canonical, status, category, metatags
            FROM entries
            WHERE status != 'synonym'
            ORDER BY name
            """
        ):
            yield _row_to_cache_row(row)

    def _row_expired(self, conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
        if self.ttl_days is None or self.ttl_days <= 0:
            return False
        newest = conn.execute(
            "SELECT MAX(fetched_at) AS newest FROM entries WHERE root = ?",
            (row["root"],),
        ).fetchone()
        ts_raw = (newest["newest"] if newest else None) or row["fetched_at"]
        ts = _parse_iso(str(ts_raw))
        if ts is None:
            return True
        return ts < _utc_now() - timedelta(days=float(self.ttl_days))

    def remember_profile(self, profile: TagProfile) -> None:
        """Index a fetched profile; synonym lists form one expiry tree."""
        conn = self._open()
        now = _utc_now().isoformat()
        rows: list[tuple[str, str, str, str | None, str, str, str | None]] = []
        meta_json = json.dumps(
            [t.name for t in profile.metatags], ensure_ascii=False
        )

        if profile.synonym_of is not None:
            canonical = profile.synonym_of.name
            root = canonical
            rows.append(
                (
                    profile.name,
                    canonical,
                    "synonym",
                    profile.category,
                    root,
                    now,
                    None,
                )
            )
            # Ensure root row exists (may be filled when canonical is followed).
            existing = conn.execute(
                "SELECT 1 FROM entries WHERE name = ?", (canonical,)
            ).fetchone()
            if existing is None:
                rows.append(
                    (
                        canonical,
                        canonical,
                        "canonical",
                        profile.category,
                        root,
                        now,
                        None,
                    )
                )
        elif profile.canonical:
            root = profile.name
            # Keep queried synonyms that AO3 redirected here but did not list
            # on the canonical page (otherwise follow-canonical deletes them
            # and the warmer treats the same name as uncached forever).
            extras = [
                (str(row["name"]), row["category"])
                for row in conn.execute(
                    "SELECT name, category FROM entries WHERE root = ?",
                    (root,),
                ).fetchall()
            ]
            # Replace prior tree for this canonical so synonym list stays fresh.
            conn.execute("DELETE FROM entries WHERE root = ?", (root,))
            mapping = _synonym_map_from_profile(profile)
            for name, canonical in mapping.items():
                status = "canonical" if name == canonical else "synonym"
                stored_meta = meta_json if status == "canonical" else None
                rows.append(
                    (
                        name,
                        canonical,
                        status,
                        profile.category,
                        root,
                        now,
                        stored_meta,
                    )
                )
            if profile.name not in mapping:
                rows.append(
                    (
                        profile.name,
                        profile.name,
                        "canonical",
                        profile.category,
                        root,
                        now,
                        meta_json,
                    )
                )
            covered = set(mapping) | {profile.name}
            for extra_name, extra_cat in extras:
                if extra_name in covered:
                    continue
                rows.append(
                    (
                        extra_name,
                        profile.name,
                        "synonym",
                        extra_cat or profile.category,
                        root,
                        now,
                        None,
                    )
                )
        else:
            root = profile.name
            conn.execute("DELETE FROM entries WHERE name = ?", (profile.name,))
            rows.append(
                (
                    profile.name,
                    profile.name,
                    "unmarked",
                    profile.category,
                    root,
                    now,
                    meta_json,
                )
            )

        conn.executemany(
            """
            INSERT OR REPLACE INTO entries
              (name, canonical, status, category, root, fetched_at, metatags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        self.dirty = True

    def remember_alias(
        self,
        name: str,
        canonical: str,
        *,
        status: str = "synonym",
        category: str | None = None,
    ) -> None:
        """Keep a queried form on the canonical tree if AO3 omitted it.

        No-op when ``name`` is already cached. Used after follow-canonical so
        a redirect that is not listed as a synonym still counts as cached.
        """
        name = (name or "").strip()
        canonical = (canonical or "").strip()
        if not name or not canonical:
            return
        if self.lookup(name) is not None:
            return
        if name != canonical:
            stored_status = "synonym"
            root = canonical
        elif status == "unmarked":
            stored_status = "unmarked"
            root = name
        else:
            stored_status = "canonical"
            root = name
        conn = self._open()
        now = _utc_now().isoformat()
        conn.execute(
            """
            INSERT OR REPLACE INTO entries
              (name, canonical, status, category, root, fetched_at, metatags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, canonical, stored_status, category, root, now, None),
        )
        conn.commit()
        self.dirty = True

    def stats_snapshot(self) -> dict[str, Any]:
        if self.path is None or self._conn is None:
            return {"entries": 0, "trees": 0, "path": None}
        conn = self._open()
        entries = conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
        trees = conn.execute(
            "SELECT COUNT(DISTINCT root) AS n FROM entries"
        ).fetchone()["n"]
        return {
            "entries": int(entries),
            "trees": int(trees),
            "path": str(self.path),
            "ttl_days": self.ttl_days,
            "expired_trees": self.expired_trees,
            "expired_rows": self.expired_rows,
        }


# Re-export Literal helper for type checkers without importing ResolveStatus at runtime
ResolveStatus = Literal["canonical", "synonym", "unmarked", "missing", "error"]
