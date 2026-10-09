"""SQLite state store for tracked repos, issues, PRs and releases."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

_DEFAULT_DB = Path.home() / ".releaseradar" / "state.db"

DDL = """
CREATE TABLE IF NOT EXISTS repos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner       TEXT NOT NULL,
    name        TEXT NOT NULL,
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner, name)
);

CREATE TABLE IF NOT EXISTS issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    number      INTEGER NOT NULL,
    title       TEXT NOT NULL,
    state       TEXT NOT NULL,         -- open | closed
    html_url    TEXT NOT NULL,
    opened_at   TEXT NOT NULL,
    closed_at   TEXT,
    updated_at  TEXT NOT NULL,
    UNIQUE(repo_id, number)
);

CREATE TABLE IF NOT EXISTS pull_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id         INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    number          INTEGER NOT NULL,
    title           TEXT NOT NULL,
    state           TEXT NOT NULL,     -- open | closed | merged
    html_url        TEXT NOT NULL,
    opened_at       TEXT NOT NULL,
    merged_at       TEXT,
    updated_at      TEXT NOT NULL,
    -- linked issues (comma-separated numbers, extracted from body/title)
    linked_issues   TEXT,
    UNIQUE(repo_id, number)
);

CREATE TABLE IF NOT EXISTS releases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id         INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    tag_name        TEXT NOT NULL,
    name            TEXT,
    html_url        TEXT NOT NULL,
    published_at    TEXT NOT NULL,
    prerelease      INTEGER NOT NULL DEFAULT 0,
    body            TEXT,
    UNIQUE(repo_id, tag_name)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,         -- issue_opened | issue_closed | pr_opened | pr_merged | release_published
    ref_number  INTEGER,               -- issue or PR number, NULL for releases
    ref_tag     TEXT,                  -- release tag, NULL for issues/PRs
    payload     TEXT,                  -- JSON blob for extra context
    seen_at     TEXT NOT NULL DEFAULT (datetime('now')),
    notified    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_notified ON events(notified);
CREATE INDEX IF NOT EXISTS idx_events_repo     ON events(repo_id, seen_at DESC);
"""


def init(db_path: Path = _DEFAULT_DB) -> None:
    """Create tables if they don't exist yet."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(DDL)


@contextmanager
def _connect(db_path: Path = _DEFAULT_DB) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def open_db(db_path: Path = _DEFAULT_DB) -> "_DB":
    return _DB(db_path)


class _DB:
    """Thin DAO around the SQLite connection."""

    def __init__(self, path: Path = _DEFAULT_DB) -> None:
        self.path = path
        init(path)

    # ── repos ────────────────────────────────────────────────────────────────

    def add_repo(self, owner: str, name: str) -> int:
        with _connect(self.path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO repos(owner, name) VALUES(?, ?)", (owner, name)
            )
            if cur.lastrowid and cur.rowcount:
                return cur.lastrowid
            row = conn.execute(
                "SELECT id FROM repos WHERE owner=? AND name=?", (owner, name)
            ).fetchone()
            return row["id"]

    def remove_repo(self, owner: str, name: str) -> bool:
        with _connect(self.path) as conn:
            cur = conn.execute(
                "DELETE FROM repos WHERE owner=? AND name=?", (owner, name)
            )
            return cur.rowcount > 0

    def list_repos(self) -> list[sqlite3.Row]:
        with _connect(self.path) as conn:
            return conn.execute(
                "SELECT * FROM repos ORDER BY owner, name"
            ).fetchall()

    def get_repo_id(self, owner: str, name: str) -> int | None:
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT id FROM repos WHERE owner=? AND name=?", (owner, name)
            ).fetchone()
            return row["id"] if row else None

    # ── upsert helpers ───────────────────────────────────────────────────────

    def upsert_issue(self, repo_id: int, data: dict) -> bool:
        """Returns True if the issue is new or changed state."""
        with _connect(self.path) as conn:
            existing = conn.execute(
                "SELECT state, updated_at FROM issues WHERE repo_id=? AND number=?",
                (repo_id, data["number"]),
            ).fetchone()
            changed = existing is None or existing["updated_at"] != data["updated_at"]
            conn.execute(
                """INSERT INTO issues(repo_id, number, title, state, html_url,
                       opened_at, closed_at, updated_at)
                   VALUES(:repo_id, :number, :title, :state, :html_url,
                          :opened_at, :closed_at, :updated_at)
                   ON CONFLICT(repo_id, number) DO UPDATE SET
                       title=excluded.title, state=excluded.state,
                       closed_at=excluded.closed_at, updated_at=excluded.updated_at""",
                {**data, "repo_id": repo_id},
            )
            return changed

    def upsert_pr(self, repo_id: int, data: dict) -> bool:
        with _connect(self.path) as conn:
            existing = conn.execute(
                "SELECT state, updated_at FROM pull_requests WHERE repo_id=? AND number=?",
                (repo_id, data["number"]),
            ).fetchone()
            changed = existing is None or existing["updated_at"] != data["updated_at"]
            conn.execute(
                """INSERT INTO pull_requests(repo_id, number, title, state, html_url,
                       opened_at, merged_at, updated_at, linked_issues)
                   VALUES(:repo_id, :number, :title, :state, :html_url,
                          :opened_at, :merged_at, :updated_at, :linked_issues)
                   ON CONFLICT(repo_id, number) DO UPDATE SET
                       title=excluded.title, state=excluded.state,
                       merged_at=excluded.merged_at, updated_at=excluded.updated_at,
                       linked_issues=excluded.linked_issues""",
                {**data, "repo_id": repo_id},
            )
            return changed

    def upsert_release(self, repo_id: int, data: dict) -> bool:
        with _connect(self.path) as conn:
            existing = conn.execute(
                "SELECT id FROM releases WHERE repo_id=? AND tag_name=?",
                (repo_id, data["tag_name"]),
            ).fetchone()
            is_new = existing is None
            conn.execute(
                """INSERT INTO releases(repo_id, tag_name, name, html_url,
                       published_at, prerelease, body)
                   VALUES(:repo_id, :tag_name, :name, :html_url,
                          :published_at, :prerelease, :body)
                   ON CONFLICT(repo_id, tag_name) DO NOTHING""",
                {**data, "repo_id": repo_id},
            )
            return is_new

    def add_event(self, repo_id: int, kind: str, ref_number: int | None = None,
                  ref_tag: str | None = None, payload: str | None = None) -> None:
        with _connect(self.path) as conn:
            conn.execute(
                """INSERT INTO events(repo_id, kind, ref_number, ref_tag, payload)
                   VALUES(?, ?, ?, ?, ?)""",
                (repo_id, kind, ref_number, ref_tag, payload),
            )

    def pending_events(self) -> list[sqlite3.Row]:
        with _connect(self.path) as conn:
            return conn.execute(
                """SELECT e.*, r.owner, r.name
                   FROM events e JOIN repos r ON r.id = e.repo_id
                   WHERE e.notified = 0
                   ORDER BY e.seen_at""",
            ).fetchall()

    def mark_notified(self, event_ids: list[int]) -> None:
        if not event_ids:
            return
        placeholders = ",".join("?" * len(event_ids))
        with _connect(self.path) as conn:
            conn.execute(
                f"UPDATE events SET notified=1 WHERE id IN ({placeholders})",
                event_ids,
            )

    # ── read helpers for status display ──────────────────────────────────────

    def repo_summary(self, owner: str, name: str) -> dict:
        with _connect(self.path) as conn:
            repo_id = self.get_repo_id(owner, name)
            if repo_id is None:
                return {}
            open_issues = conn.execute(
                "SELECT COUNT(*) FROM issues WHERE repo_id=? AND state='open'", (repo_id,)
            ).fetchone()[0]
            open_prs = conn.execute(
                "SELECT COUNT(*) FROM pull_requests WHERE repo_id=? AND state='open'", (repo_id,)
            ).fetchone()[0]
            latest_release = conn.execute(
                "SELECT tag_name, published_at FROM releases WHERE repo_id=? ORDER BY published_at DESC LIMIT 1",
                (repo_id,),
            ).fetchone()
            return {
                "open_issues": open_issues,
                "open_prs": open_prs,
                "latest_release": dict(latest_release) if latest_release else None,
            }
