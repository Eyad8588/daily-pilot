"""SQLite store for tasks (from action emails or added by hand) and processed mail."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    due         TEXT,              -- ISO date (YYYY-MM-DD) or NULL
    priority    INTEGER NOT NULL DEFAULT 2,  -- 1 high, 2 normal, 3 low
    est_minutes INTEGER NOT NULL DEFAULT 30,
    status      TEXT NOT NULL DEFAULT 'open',  -- open | done
    source      TEXT NOT NULL DEFAULT 'manual',
    source_id   TEXT,              -- e.g. Gmail message id
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    done_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS tasks_source ON tasks(source, source_id) WHERE source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS processed_mail (
    message_id   TEXT PRIMARY KEY,
    category     TEXT NOT NULL,
    subject      TEXT NOT NULL,
    sender       TEXT NOT NULL,
    reason       TEXT NOT NULL DEFAULT '',
    alerted      INTEGER NOT NULL DEFAULT 0,
    draft_id     TEXT,
    processed_at TEXT NOT NULL
);
"""


@dataclass
class Task:
    id: int
    title: str
    due: date | None
    priority: int
    est_minutes: int
    status: str
    source: str
    source_id: str | None
    notes: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Task:
        return cls(
            id=row["id"],
            title=row["title"],
            due=date.fromisoformat(row["due"]) if row["due"] else None,
            priority=row["priority"],
            est_minutes=row["est_minutes"],
            status=row["status"],
            source=row["source"],
            source_id=row["source_id"],
            notes=row["notes"],
        )


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # --- tasks -----------------------------------------------------------
    def add_task(
        self,
        title: str,
        due: date | None = None,
        priority: int = 2,
        est_minutes: int = 30,
        source: str = "manual",
        source_id: str | None = None,
        notes: str = "",
    ) -> int | None:
        """Insert a task. Returns its id, or None if this source item already has one."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO tasks (title, due, priority, est_minutes, source, source_id, notes, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    title.strip(),
                    due.isoformat() if due else None,
                    int(priority),
                    int(est_minutes),
                    source,
                    source_id,
                    notes,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        return cur.lastrowid if cur.rowcount else None

    def open_tasks(self) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE status = 'open'"
            " ORDER BY due IS NULL, due, priority, id"
        ).fetchall()
        return [Task.from_row(r) for r in rows]

    def complete(self, task_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE tasks SET status='done', done_at=? WHERE id=? AND status='open'",
                (datetime.now().isoformat(timespec="seconds"), task_id),
            )
        return cur.rowcount == 1

    # --- mail ------------------------------------------------------------
    def is_processed(self, message_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM processed_mail WHERE message_id=?", (message_id,)).fetchone() is not None

    def mark_processed(
        self,
        message_id: str,
        category: str,
        subject: str,
        sender: str,
        reason: str = "",
        alerted: bool = False,
        draft_id: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO processed_mail VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    category,
                    subject,
                    sender,
                    reason,
                    int(alerted),
                    draft_id,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def mail_counts_since(self, since: datetime) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT category, COUNT(*) AS n FROM processed_mail WHERE processed_at >= ? GROUP BY category",
            (since.isoformat(timespec="seconds"),),
        ).fetchall()
        return {r["category"]: r["n"] for r in rows}
