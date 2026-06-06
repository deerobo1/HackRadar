"""
db.py — HackRadar SQLite database layer
========================================
Single source of truth for all hackathon state:
  - seen       : every hackathon URL we have ever processed (dedup)
  - snoozed    : hackathons to re-notify after a delay
  - dismissed  : permanently skipped hackathons

Usage:
    from db import DB
    db = DB()
    db.mark_seen(hackathon)
    if db.is_seen(url): ...
"""

import sqlite3
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class DB:
    """Thin wrapper around the HackRadar SQLite database."""

    def __init__(self, db_path: str = "hackradar.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """Return a thread-local connection, creating it if necessary."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # Schema initialisation
    # ------------------------------------------------------------------

    def _init_db(self):
        """Create tables if they don't already exist."""
        conn = self._get_conn()
        conn.executescript("""
            -- ----------------------------------------------------------------
            -- seen: every hackathon URL we have ever processed.
            -- Prevents duplicate notifications across runs.
            -- ----------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS seen (
                url             TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                source          TEXT NOT NULL DEFAULT '',
                first_seen_at   TEXT NOT NULL        -- ISO8601 UTC timestamp
            );

            -- ----------------------------------------------------------------
            -- snoozed: hackathons to re-notify after a user-chosen delay.
            -- The full hackathon JSON blob is stored so we can re-send the
            -- original notification without re-scraping.
            -- ----------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS snoozed (
                url             TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                data            TEXT NOT NULL,        -- JSON blob
                fire_at         TEXT NOT NULL,        -- ISO8601 UTC datetime
                snoozed_at      TEXT NOT NULL         -- ISO8601 UTC datetime
            );

            -- ----------------------------------------------------------------
            -- dismissed: permanently skipped hackathons.
            -- We will never notify the user about these again.
            -- ----------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS dismissed (
                url             TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                dismissed_at    TEXT NOT NULL         -- ISO8601 UTC timestamp
            );

            -- ----------------------------------------------------------------
            -- reminder: deadline reminders set when user clicks Register.
            -- snooze.py also checks this table to fire reminder notifications.
            -- ----------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS reminders (
                url             TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                deadline        TEXT,                 -- ISO8601 date of hackathon deadline
                remind_at       TEXT NOT NULL,        -- ISO8601 UTC datetime to fire reminder
                fired           INTEGER NOT NULL DEFAULT 0  -- 0 = pending, 1 = fired
            );
        """)
        conn.commit()
        log.debug("Database initialised at %s", self.db_path)

    # ------------------------------------------------------------------
    # seen helpers
    # ------------------------------------------------------------------

    def is_seen(self, url: str) -> bool:
        """Return True if we have already processed this hackathon URL."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM seen WHERE url = ?", (url,)
        ).fetchone()
        return row is not None

    def mark_seen(self, hackathon: dict) -> None:
        """Record that we have processed this hackathon."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR IGNORE INTO seen (url, name, source, first_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                hackathon["url"],
                hackathon.get("name", ""),
                hackathon.get("source", ""),
                _now_iso(),
            ),
        )
        conn.commit()
        log.debug("Marked seen: %s", hackathon["url"])

    def get_all_seen(self) -> list[dict]:
        """Return all seen hackathon records."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM seen ORDER BY first_seen_at DESC").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # dismissed helpers
    # ------------------------------------------------------------------

    def is_dismissed(self, url: str) -> bool:
        """Return True if the user has permanently dismissed this hackathon."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM dismissed WHERE url = ?", (url,)
        ).fetchone()
        return row is not None

    def dismiss(self, url: str, name: str) -> None:
        """Permanently dismiss a hackathon. Also removes any active snooze."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO dismissed (url, name, dismissed_at)
            VALUES (?, ?, ?)
            """,
            (url, name, _now_iso()),
        )
        # Remove from snoozed if it was there
        conn.execute("DELETE FROM snoozed WHERE url = ?", (url,))
        conn.commit()
        log.info("Dismissed: %s (%s)", name, url)

    def get_all_dismissed(self) -> list[dict]:
        """Return all dismissed hackathon records."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM dismissed ORDER BY dismissed_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # snoozed helpers
    # ------------------------------------------------------------------

    def is_snoozed(self, url: str) -> bool:
        """Return True if this hackathon is currently snoozed."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM snoozed WHERE url = ?", (url,)
        ).fetchone()
        return row is not None

    def snooze(self, hackathon: dict, fire_at: datetime) -> None:
        """Snooze a hackathon — re-fire its notification at fire_at."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO snoozed (url, name, data, fire_at, snoozed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                hackathon["url"],
                hackathon.get("name", ""),
                json.dumps(hackathon),
                fire_at.astimezone(timezone.utc).isoformat(),
                _now_iso(),
            ),
        )
        conn.commit()
        log.info("Snoozed until %s: %s", fire_at.isoformat(), hackathon.get("name"))

    def get_due_snoozed(self) -> list[dict]:
        """Return all snoozed hackathons whose fire_at time has passed."""
        conn = self._get_conn()
        now = _now_iso()
        rows = conn.execute(
            "SELECT * FROM snoozed WHERE fire_at <= ?", (now,)
        ).fetchall()
        results = []
        for row in rows:
            record = dict(row)
            record["data"] = json.loads(record["data"])  # Deserialise blob
            results.append(record)
        return results

    def delete_snoozed(self, url: str) -> None:
        """Remove a hackathon from the snoozed table (after re-firing)."""
        conn = self._get_conn()
        conn.execute("DELETE FROM snoozed WHERE url = ?", (url,))
        conn.commit()
        log.debug("Deleted snooze record for: %s", url)

    # ------------------------------------------------------------------
    # reminder helpers
    # ------------------------------------------------------------------

    def set_reminder(self, hackathon: dict, remind_at: datetime) -> None:
        """Set a deadline reminder for a registered hackathon."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO reminders (url, name, deadline, remind_at, fired)
            VALUES (?, ?, ?, ?, 0)
            """,
            (
                hackathon["url"],
                hackathon.get("name", ""),
                hackathon.get("deadline", ""),
                remind_at.astimezone(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        log.info(
            "Reminder set for %s at %s",
            hackathon.get("name"),
            remind_at.isoformat(),
        )

    def get_due_reminders(self) -> list[dict]:
        """Return all unfired reminders whose remind_at time has passed."""
        conn = self._get_conn()
        now = _now_iso()
        rows = conn.execute(
            "SELECT * FROM reminders WHERE remind_at <= ? AND fired = 0", (now,)
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_reminder_fired(self, url: str) -> None:
        """Mark a reminder as fired so it doesn't re-trigger."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE reminders SET fired = 1 WHERE url = ?", (url,)
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Return row counts for all tables — useful for health checks."""
        conn = self._get_conn()
        return {
            "seen": conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0],
            "snoozed": conn.execute("SELECT COUNT(*) FROM snoozed").fetchone()[0],
            "dismissed": conn.execute("SELECT COUNT(*) FROM dismissed").fetchone()[0],
            "reminders_pending": conn.execute(
                "SELECT COUNT(*) FROM reminders WHERE fired = 0"
            ).fetchone()[0],
        }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Current UTC time as an ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CLI quick-check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    with DB() as db:
        print("DB stats:", db.stats())
        print("Schema created successfully at hackradar.db")
