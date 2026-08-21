"""Host-wide AO3 rate-limit coordination (SQLite).

All ao3kit interfaces (CLI, web UI, REST API, Calibre plugin subprocess) share
one on-disk limiter so concurrent processes pace requests together.

Override path with ``AO3KIT_RATE_DB``. Default: ``<project>/.cache/ao3_rate.sqlite``.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_RATE_DB_PATH = (
    Path(__file__).resolve().parents[1] / ".cache" / "ao3_rate.sqlite"
)

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
"""


def default_rate_db_path() -> Path:
    override = os.environ.get("AO3KIT_RATE_DB", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_RATE_DB_PATH


@dataclass
class RateSnapshot:
    next_allowed_at: float
    base_interval: float
    tag_interval: float
    success_streak: int
    crawl_delay: float | None


class SharedRateStore:
    """Serialize rate-limit claims across processes via SQLite ``BEGIN IMMEDIATE``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._local = threading.Lock()
        self._conn: sqlite3.Connection | None = None

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
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
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
                (now, 1.0, 0.5, now),
            )
            conn.commit()
        self._conn = conn
        return conn

    def _fetch(self, conn: sqlite3.Connection) -> RateSnapshot:
        row = conn.execute("SELECT * FROM rate_state WHERE id = 1").fetchone()
        assert row is not None
        crawl = row["crawl_delay"]
        return RateSnapshot(
            next_allowed_at=float(row["next_allowed_at"]),
            base_interval=float(row["base_interval"]),
            tag_interval=float(row["tag_interval"]),
            success_streak=int(row["success_streak"]),
            crawl_delay=float(crawl) if crawl is not None else None,
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
              updated_at = ?
            WHERE id = 1
            """,
            (
                snap.next_allowed_at,
                snap.base_interval,
                snap.tag_interval,
                snap.success_streak,
                snap.crawl_delay,
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

    def claim_slot(self, interval: float) -> tuple[float, RateSnapshot]:
        """Reserve the next request slot. Returns (seconds_to_wait, snapshot)."""
        now = time.time()
        wait_holder = [0.0]

        def mutator(snap: RateSnapshot) -> RateSnapshot:
            wait = max(0.0, snap.next_allowed_at - now)
            wait_holder[0] = wait
            next_at = max(snap.next_allowed_at, now) + max(float(interval), 0.0)
            return RateSnapshot(
                next_allowed_at=next_at,
                base_interval=snap.base_interval,
                tag_interval=snap.tag_interval,
                success_streak=snap.success_streak,
                crawl_delay=snap.crawl_delay,
            )

        snap = self.update(mutator)
        return wait_holder[0], snap
