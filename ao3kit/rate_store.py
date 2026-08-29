"""Host-wide AO3 rate-limit coordination (SQLite).

CLI and the Calibre plugin subprocess share
one on-disk limiter so concurrent processes pace requests together.

Override path with ``AO3KIT_RATE_DB``. Default: ``$XDG_STATE_HOME/fanfic-organizer/ao3_rate.sqlite``.

The same file stores:

- ``rate_events`` — recent attempts (status, wait, interval, Retry-After)
- ``rate_hourly`` — durable rollups for tuning pacing over weeks/months
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EVENT_TTL_SECONDS = 30 * 24 * 3600
EVENT_MAX_ROWS = 50_000
HOURLY_TTL_SECONDS = 180 * 24 * 3600
_OUTCOMES = ("ok", "429", "5xx", "cloudflare", "timeout", "error")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  next_allowed_at REAL NOT NULL,
  base_interval REAL NOT NULL,
  tag_interval REAL NOT NULL,
  success_streak INTEGER NOT NULL,
  crawl_delay REAL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  status INTEGER,
  outcome TEXT NOT NULL,
  wait_s REAL NOT NULL,
  interval_s REAL,
  elapsed_s REAL,
  retry_after_s REAL,
  retry_after_from_header INTEGER,
  attempt INTEGER NOT NULL,
  base_interval REAL,
  tag_interval REAL,
  success_streak INTEGER,
  pid INTEGER
);
CREATE INDEX IF NOT EXISTS rate_events_ts ON rate_events(ts);
CREATE TABLE IF NOT EXISTS rate_hourly (
  hour_ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  requests INTEGER NOT NULL DEFAULT 0,
  ok INTEGER NOT NULL DEFAULT 0,
  n429 INTEGER NOT NULL DEFAULT 0,
  n5xx INTEGER NOT NULL DEFAULT 0,
  cloudflare INTEGER NOT NULL DEFAULT 0,
  timeout INTEGER NOT NULL DEFAULT 0,
  error INTEGER NOT NULL DEFAULT 0,
  wait_sum REAL NOT NULL DEFAULT 0,
  elapsed_sum REAL NOT NULL DEFAULT 0,
  interval_sum REAL NOT NULL DEFAULT 0,
  interval_min REAL,
  interval_max REAL,
  retry_after_header INTEGER NOT NULL DEFAULT 0,
  retry_after_missing INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (hour_ts, kind)
);
CREATE INDEX IF NOT EXISTS rate_hourly_hour ON rate_hourly(hour_ts);
CREATE TABLE IF NOT EXISTS robots_cache (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  body TEXT NOT NULL,
  fetched_at REAL NOT NULL
);
"""

_EVENT_EXTRA_COLUMNS = {
    "base_interval": "REAL",
    "tag_interval": "REAL",
    "success_streak": "INTEGER",
    "pid": "INTEGER",
}


def default_rate_db_path() -> Path:
    override = os.environ.get("AO3KIT_RATE_DB", "").strip()
    if override:
        return Path(override).expanduser()
    from ao3kit.paths import rate_db_file

    return rate_db_file()


def hour_bucket(ts: float) -> int:
    return int(ts // 3600) * 3600


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_opt(row: sqlite3.Row, key: str) -> Any:
    if key not in row.keys():
        return None
    return row[key]


@dataclass
class RateSnapshot:
    next_allowed_at: float
    base_interval: float
    tag_interval: float
    success_streak: int
    crawl_delay: float | None
    retry_after_until: float | None = None


@dataclass
class RateEvent:
    ts: float
    kind: str
    method: str
    path: str
    status: int | None
    outcome: str
    wait_s: float
    interval_s: float | None
    elapsed_s: float | None
    retry_after_s: float | None
    retry_after_from_header: bool | None
    attempt: int
    id: int | None = None
    base_interval: float | None = None
    tag_interval: float | None = None
    success_streak: int | None = None
    pid: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "at": _iso(self.ts),
            "kind": self.kind,
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "outcome": self.outcome,
            "wait_s": round(self.wait_s, 3),
            "interval_s": (
                None if self.interval_s is None else round(self.interval_s, 3)
            ),
            "elapsed_s": (
                None if self.elapsed_s is None else round(self.elapsed_s, 3)
            ),
            "retry_after_s": self.retry_after_s,
            "retry_after_from_header": self.retry_after_from_header,
            "attempt": self.attempt,
            "base_interval": self.base_interval,
            "tag_interval": self.tag_interval,
            "success_streak": self.success_streak,
            "pid": self.pid,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RateEvent:
        header = row["retry_after_from_header"]
        base = _row_opt(row, "base_interval")
        tag = _row_opt(row, "tag_interval")
        streak = _row_opt(row, "success_streak")
        pid = _row_opt(row, "pid")
        return cls(
            id=int(row["id"]),
            ts=float(row["ts"]),
            kind=str(row["kind"]),
            method=str(row["method"]),
            path=str(row["path"]),
            status=None if row["status"] is None else int(row["status"]),
            outcome=str(row["outcome"]),
            wait_s=float(row["wait_s"]),
            interval_s=(
                None if row["interval_s"] is None else float(row["interval_s"])
            ),
            elapsed_s=(
                None if row["elapsed_s"] is None else float(row["elapsed_s"])
            ),
            retry_after_s=(
                None
                if row["retry_after_s"] is None
                else float(row["retry_after_s"])
            ),
            retry_after_from_header=None if header is None else bool(header),
            attempt=int(row["attempt"]),
            base_interval=None if base is None else float(base),
            tag_interval=None if tag is None else float(tag),
            success_streak=None if streak is None else int(streak),
            pid=None if pid is None else int(pid),
        )


def _empty_counts() -> dict[str, int]:
    return {"requests": 0, **{name: 0 for name in _OUTCOMES}}


def _ensure_event_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(rate_events)")}
    for name, typ in _EVENT_EXTRA_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE rate_events ADD COLUMN {name} {typ}")


def _ensure_state_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(rate_state)")}
    if "retry_after_until" not in existing:
        conn.execute("ALTER TABLE rate_state ADD COLUMN retry_after_until REAL")


def _retry_if_locked(fn, *, attempts: int = 20, delay: float = 0.05):
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            last = exc
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))
    raise last  # pragma: no cover


class SharedRateStore:
    """Serialize rate-limit claims across processes via SQLite ``BEGIN IMMEDIATE``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._local = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._inserts = 0

    def close(self) -> None:
        with self._local:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if self.path is None:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self.path),
                timeout=60,
                check_same_thread=False,
            )
            conn.execute("PRAGMA busy_timeout=60000")

            def _enable_wal() -> None:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")

            _retry_if_locked(_enable_wal)
        # Autocommit except for explicit BEGIN IMMEDIATE in update/claim_slot.
        # Implicit DEFERRED transactions can upgrade locks and let two jobs
        # overlap if ``now`` was sampled before the exclusive lock.
        conn.isolation_level = None
        conn.execute("PRAGMA busy_timeout=60000")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        _ensure_event_columns(conn)
        _ensure_state_columns(conn)
        row = conn.execute("SELECT 1 FROM rate_state WHERE id = 1").fetchone()
        if row is None:
            now = time.time()
            conn.execute(
                """
                INSERT INTO rate_state (
                  id, next_allowed_at, base_interval, tag_interval,
                  success_streak, crawl_delay, updated_at
                ) VALUES (1, ?, ?, ?, 0, NULL, ?)
                """,
                (now, 1.5, 1.5, now),
            )
        self._conn = conn
        return conn

    def _fetch(self, conn: sqlite3.Connection) -> RateSnapshot:
        row = conn.execute("SELECT * FROM rate_state WHERE id = 1").fetchone()
        assert row is not None
        crawl = row["crawl_delay"]
        retry_until = row["retry_after_until"] if "retry_after_until" in row.keys() else None
        return RateSnapshot(
            next_allowed_at=float(row["next_allowed_at"]),
            base_interval=float(row["base_interval"]),
            tag_interval=float(row["tag_interval"]),
            success_streak=int(row["success_streak"]),
            crawl_delay=float(crawl) if crawl is not None else None,
            retry_after_until=(
                None if retry_until is None else float(retry_until)
            ),
        )

    def _write(self, conn: sqlite3.Connection, snap: RateSnapshot) -> None:
        conn.execute(
            """
            UPDATE rate_state SET
              next_allowed_at = ?,
              base_interval = ?,
              tag_interval = ?,
              success_streak = ?,
              crawl_delay = ?,
              retry_after_until = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (
                snap.next_allowed_at,
                snap.base_interval,
                snap.tag_interval,
                snap.success_streak,
                snap.crawl_delay,
                snap.retry_after_until,
                time.time(),
            ),
        )

    def read(self) -> RateSnapshot:
        with self._local:
            conn = self._connect()
            return self._fetch(conn)

    def update(self, mutator: Any) -> RateSnapshot:
        """Run ``mutator(snapshot) -> snapshot`` under an exclusive DB lock."""
        with self._local:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                snap = self._fetch(conn)
                new_snap = mutator(snap)
                if new_snap is None:
                    new_snap = snap
                self._write(conn, new_snap)
                conn.commit()
                return new_snap
            except Exception:
                conn.rollback()
                raise

    def claim_slot(
        self,
        interval: float,
        *,
        max_wait: float | None = None,
        stale_after: float | None = None,
        stale_wait: float | None = None,
    ) -> tuple[float, RateSnapshot]:
        """Reserve the next request slot. Returns (seconds_to_wait, snapshot).

        ``now`` is sampled *inside* the exclusive lock so a job that waited
        for SQLite cannot rewind ``next_allowed_at`` into the past and let
        another process claim an overlapping slot.

        ``max_wait`` caps leftover delay (login). ``stale_after`` / ``stale_wait``
        rewind a far-future lock (crash / cancelled 429) without punching
        through a live Retry-After that is still within ``stale_after``.
        """
        wait_holder = [0.0]
        interval = max(float(interval), 0.0)

        def mutator(snap: RateSnapshot) -> RateSnapshot:
            now = time.time()
            leftover = max(0.0, snap.next_allowed_at - now)
            wait = leftover
            retry_until = snap.retry_after_until
            if retry_until is not None and now >= retry_until:
                retry_until = None
            active_retry_after = (
                retry_until is not None and now < retry_until
            )
            if (
                stale_after is not None
                and leftover > float(stale_after)
                and not active_retry_after
            ):
                cap = leftover if stale_wait is None else max(float(stale_wait), 0.0)
                wait = min(wait, cap)
                retry_until = None
            if max_wait is not None:
                wait = min(wait, max(float(max_wait), 0.0))
            wait_holder[0] = wait
            next_at = now + wait + interval
            return RateSnapshot(
                next_allowed_at=next_at,
                base_interval=snap.base_interval,
                tag_interval=snap.tag_interval,
                success_streak=snap.success_streak,
                crawl_delay=snap.crawl_delay,
                retry_after_until=retry_until,
            )

        snap = self.update(mutator)
        return wait_holder[0], snap

    def _maybe_prune(self, conn: sqlite3.Connection) -> None:
        self._inserts += 1
        if self._inserts % 50 != 1:
            return
        now = time.time()
        conn.execute(
            "DELETE FROM rate_events WHERE ts < ?",
            (now - EVENT_TTL_SECONDS,),
        )
        count_row = conn.execute("SELECT COUNT(*) AS n FROM rate_events").fetchone()
        count = int(count_row["n"] if count_row else 0)
        extra = count - EVENT_MAX_ROWS
        if extra > 0:
            conn.execute(
                """
                DELETE FROM rate_events WHERE id IN (
                  SELECT id FROM rate_events ORDER BY id ASC LIMIT ?
                )
                """,
                (extra,),
            )
        conn.execute(
            "DELETE FROM rate_hourly WHERE hour_ts < ?",
            (int(now - HOURLY_TTL_SECONDS),),
        )

    def _bump_hourly(self, conn: sqlite3.Connection, event: RateEvent) -> None:
        outcome = event.outcome
        interval = event.interval_s
        header_inc = 0
        missing_inc = 0
        if outcome == "429":
            if event.retry_after_from_header:
                header_inc = 1
            else:
                missing_inc = 1
        conn.execute(
            """
            INSERT INTO rate_hourly (
              hour_ts, kind, requests,
              ok, n429, n5xx, cloudflare, timeout, error,
              wait_sum, elapsed_sum, interval_sum,
              interval_min, interval_max,
              retry_after_header, retry_after_missing
            ) VALUES (
              ?, ?, 1,
              ?, ?, ?, ?, ?, ?,
              ?, ?, ?,
              ?, ?,
              ?, ?
            )
            ON CONFLICT(hour_ts, kind) DO UPDATE SET
              requests = rate_hourly.requests + 1,
              ok = rate_hourly.ok + excluded.ok,
              n429 = rate_hourly.n429 + excluded.n429,
              n5xx = rate_hourly.n5xx + excluded.n5xx,
              cloudflare = rate_hourly.cloudflare + excluded.cloudflare,
              timeout = rate_hourly.timeout + excluded.timeout,
              error = rate_hourly.error + excluded.error,
              wait_sum = rate_hourly.wait_sum + excluded.wait_sum,
              elapsed_sum = rate_hourly.elapsed_sum + excluded.elapsed_sum,
              interval_sum = rate_hourly.interval_sum + excluded.interval_sum,
              interval_min = CASE
                WHEN excluded.interval_min IS NULL THEN rate_hourly.interval_min
                WHEN rate_hourly.interval_min IS NULL THEN excluded.interval_min
                ELSE MIN(rate_hourly.interval_min, excluded.interval_min)
              END,
              interval_max = CASE
                WHEN excluded.interval_max IS NULL THEN rate_hourly.interval_max
                WHEN rate_hourly.interval_max IS NULL THEN excluded.interval_max
                ELSE MAX(rate_hourly.interval_max, excluded.interval_max)
              END,
              retry_after_header = rate_hourly.retry_after_header
                + excluded.retry_after_header,
              retry_after_missing = rate_hourly.retry_after_missing
                + excluded.retry_after_missing
            """,
            (
                hour_bucket(event.ts),
                event.kind,
                1 if outcome == "ok" else 0,
                1 if outcome == "429" else 0,
                1 if outcome == "5xx" else 0,
                1 if outcome == "cloudflare" else 0,
                1 if outcome == "timeout" else 0,
                1 if outcome == "error" else 0,
                float(event.wait_s),
                float(event.elapsed_s or 0.0),
                float(interval or 0.0),
                interval,
                interval,
                header_inc,
                missing_inc,
            ),
        )

    def record_event(self, event: RateEvent) -> None:
        header: int | None
        if event.retry_after_from_header is None:
            header = None
        else:
            header = 1 if event.retry_after_from_header else 0
        with self._local:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO rate_events (
                      ts, kind, method, path, status, outcome, wait_s,
                      interval_s, elapsed_s, retry_after_s,
                      retry_after_from_header, attempt,
                      base_interval, tag_interval, success_streak, pid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.ts,
                        event.kind,
                        event.method,
                        event.path,
                        event.status,
                        event.outcome,
                        event.wait_s,
                        event.interval_s,
                        event.elapsed_s,
                        event.retry_after_s,
                        header,
                        event.attempt,
                        event.base_interval,
                        event.tag_interval,
                        event.success_streak,
                        event.pid,
                    ),
                )
                self._bump_hourly(conn, event)
                self._maybe_prune(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def recent_events(
        self,
        *,
        limit: int = 50,
        since: float | None = None,
    ) -> list[RateEvent]:
        limit = max(0, int(limit))
        with self._local:
            conn = self._connect()
            if since is None:
                rows = conn.execute(
                    "SELECT * FROM rate_events ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM rate_events
                    WHERE ts >= ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (since, limit),
                ).fetchall()
        return [RateEvent.from_row(row) for row in rows]

    def export_events(self, *, since: float | None = None) -> list[dict[str, Any]]:
        with self._local:
            conn = self._connect()
            if since is None:
                rows = conn.execute(
                    "SELECT * FROM rate_events ORDER BY id ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM rate_events WHERE ts >= ? ORDER BY id ASC",
                    (since,),
                ).fetchall()
        return [RateEvent.from_row(row).to_dict() for row in rows]

    def _stats_from_kind_rows(
        self,
        kind_rows: Iterable[sqlite3.Row],
        retry_rows: Iterable[sqlite3.Row],
    ) -> dict[str, Any]:
        totals = _empty_counts()
        by_kind: dict[str, dict[str, Any]] = {}
        for row in kind_rows:
            kind = str(row["kind"])
            n = int(row["n"] or 0)
            n429 = int(row["n429"] or 0)
            bucket = {
                **_empty_counts(),
                "avg_wait_s": round(float(row["avg_wait"] or 0.0), 3),
                "avg_elapsed_s": round(float(row["avg_elapsed"] or 0.0), 3),
                "avg_interval_s": round(float(row["avg_interval"] or 0.0), 3),
                "429_rate": round((n429 / n) if n else 0.0, 4),
            }
            bucket["requests"] = n
            bucket["ok"] = int(row["ok"] or 0)
            bucket["429"] = n429
            bucket["5xx"] = int(row["n5xx"] or 0)
            bucket["cloudflare"] = int(row["cloudflare"] or 0)
            bucket["timeout"] = int(row["timeout"] or 0)
            bucket["error"] = int(row["error"] or 0)
            by_kind[kind] = bucket
            totals["requests"] += n
            for name in _OUTCOMES:
                totals[name] += int(bucket[name])

        retry_values: list[dict[str, Any]] = []
        with_header = 0
        missing_header = 0
        for row in retry_rows:
            n = int(row["n"] or 0)
            from_header = bool(row["retry_after_from_header"])
            if from_header:
                with_header += n
            else:
                missing_header += n
            retry_values.append(
                {
                    "seconds": (
                        None
                        if row["retry_after_s"] is None
                        else float(row["retry_after_s"])
                    ),
                    "from_header": from_header,
                    "count": n,
                }
            )

        total_n = totals["requests"]
        return {
            "totals": {
                **totals,
                "429_rate": round(
                    (totals["429"] / total_n) if total_n else 0.0, 4
                ),
            },
            "by_kind": by_kind,
            "retry_after": {
                "count": with_header + missing_header,
                "with_header": with_header,
                "missing_header": missing_header,
                "values": retry_values,
            },
        }

    def event_stats(self, *, since: float | None = None) -> dict[str, Any]:
        where = ""
        params: list[Any] = []
        if since is not None:
            where = "WHERE ts >= ?"
            params.append(since)

        with self._local:
            conn = self._connect()
            kind_rows = conn.execute(
                f"""
                SELECT
                  kind,
                  COUNT(*) AS n,
                  AVG(wait_s) AS avg_wait,
                  AVG(elapsed_s) AS avg_elapsed,
                  AVG(interval_s) AS avg_interval,
                  SUM(CASE WHEN outcome = 'ok' THEN 1 ELSE 0 END) AS ok,
                  SUM(CASE WHEN outcome = '429' THEN 1 ELSE 0 END) AS n429,
                  SUM(CASE WHEN outcome = '5xx' THEN 1 ELSE 0 END) AS n5xx,
                  SUM(CASE WHEN outcome = 'cloudflare' THEN 1 ELSE 0 END) AS cloudflare,
                  SUM(CASE WHEN outcome = 'timeout' THEN 1 ELSE 0 END) AS timeout,
                  SUM(CASE WHEN outcome = 'error' THEN 1 ELSE 0 END) AS error
                FROM rate_events {where}
                GROUP BY kind
                """,
                params,
            ).fetchall()
            retry_rows = conn.execute(
                f"""
                SELECT
                  retry_after_s,
                  retry_after_from_header,
                  COUNT(*) AS n
                FROM rate_events
                {where}{" AND" if where else "WHERE"} outcome = '429'
                GROUP BY retry_after_s, retry_after_from_header
                ORDER BY n DESC
                """,
                params,
            ).fetchall()
        return self._stats_from_kind_rows(kind_rows, retry_rows)

    def interval_outcome_table(
        self, *, since: float | None = None
    ) -> list[dict[str, Any]]:
        """429 rate at each ~0.1s interval bucket, by path kind."""
        where = "WHERE interval_s IS NOT NULL"
        params: list[Any] = []
        if since is not None:
            where += " AND ts >= ?"
            params.append(since)
        with self._local:
            conn = self._connect()
            rows = conn.execute(
                f"""
                SELECT
                  kind,
                  ROUND(interval_s, 1) AS interval_bucket,
                  COUNT(*) AS n,
                  SUM(CASE WHEN outcome = 'ok' THEN 1 ELSE 0 END) AS ok,
                  SUM(CASE WHEN outcome = '429' THEN 1 ELSE 0 END) AS n429,
                  SUM(CASE WHEN outcome = 'cloudflare' THEN 1 ELSE 0 END)
                    AS cloudflare,
                  SUM(CASE WHEN outcome = '5xx' THEN 1 ELSE 0 END) AS n5xx
                FROM rate_events
                {where}
                GROUP BY kind, interval_bucket
                ORDER BY kind, interval_bucket
                """,
                params,
            ).fetchall()
        table: list[dict[str, Any]] = []
        for row in rows:
            n = int(row["n"] or 0)
            n429 = int(row["n429"] or 0)
            table.append(
                {
                    "kind": str(row["kind"]),
                    "interval_s": float(row["interval_bucket"]),
                    "requests": n,
                    "ok": int(row["ok"] or 0),
                    "429": n429,
                    "cloudflare": int(row["cloudflare"] or 0),
                    "5xx": int(row["n5xx"] or 0),
                    "429_rate": round((n429 / n) if n else 0.0, 4),
                }
            )
        return table

    def hourly_series(
        self,
        *,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if since is not None:
            where = "WHERE hour_ts >= ?"
            params.append(int(since))
        sql = f"""
            SELECT * FROM rate_hourly
            {where}
            ORDER BY hour_ts DESC, kind ASC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._local:
            conn = self._connect()
            rows = conn.execute(sql, params).fetchall()
        series: list[dict[str, Any]] = []
        for row in rows:
            n = int(row["requests"] or 0)
            n429 = int(row["n429"] or 0)
            series.append(
                {
                    "hour_ts": int(row["hour_ts"]),
                    "at": _iso(float(row["hour_ts"])),
                    "kind": str(row["kind"]),
                    "requests": n,
                    "ok": int(row["ok"] or 0),
                    "429": n429,
                    "5xx": int(row["n5xx"] or 0),
                    "cloudflare": int(row["cloudflare"] or 0),
                    "timeout": int(row["timeout"] or 0),
                    "error": int(row["error"] or 0),
                    "avg_wait_s": round(
                        (float(row["wait_sum"] or 0.0) / n) if n else 0.0, 3
                    ),
                    "avg_elapsed_s": round(
                        (float(row["elapsed_sum"] or 0.0) / n) if n else 0.0, 3
                    ),
                    "avg_interval_s": round(
                        (float(row["interval_sum"] or 0.0) / n) if n else 0.0, 3
                    ),
                    "interval_min": (
                        None
                        if row["interval_min"] is None
                        else float(row["interval_min"])
                    ),
                    "interval_max": (
                        None
                        if row["interval_max"] is None
                        else float(row["interval_max"])
                    ),
                    "retry_after_header": int(row["retry_after_header"] or 0),
                    "retry_after_missing": int(row["retry_after_missing"] or 0),
                    "429_rate": round((n429 / n) if n else 0.0, 4),
                }
            )
        return series

    def clear_events(self) -> int:
        with self._local:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT COUNT(*) AS n FROM rate_events").fetchone()
                n = int(row["n"] if row else 0)
                conn.execute("DELETE FROM rate_events")
                conn.commit()
                return n
            except Exception:
                conn.rollback()
                raise

    def clear_hourly(self) -> int:
        with self._local:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT COUNT(*) AS n FROM rate_hourly").fetchone()
                n = int(row["n"] if row else 0)
                conn.execute("DELETE FROM rate_hourly")
                conn.commit()
                return n
            except Exception:
                conn.rollback()
                raise

    def read_robots(self) -> tuple[str, float] | None:
        """Return ``(robots.txt body, fetched_at wall-clock)`` or None."""
        with self._local:
            conn = self._connect()
            row = conn.execute(
                "SELECT body, fetched_at FROM robots_cache WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return str(row["body"]), float(row["fetched_at"])

    def write_robots(self, body: str, fetched_at: float) -> None:
        with self._local:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO robots_cache (id, body, fetched_at) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  body = excluded.body,
                  fetched_at = excluded.fetched_at
                """,
                (body, fetched_at),
            )
            conn.commit()
