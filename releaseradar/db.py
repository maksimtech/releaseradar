"""SQLite state store for tracked repos, issues, PRs and releases.

One connection per operation by default, so a one-off call from the CLI is
simple; one connection for a whole sync through `transaction()`, so seven
thousand rows do not mean seven thousand commits — measured at ~24 ms each on
Windows, which turned a 105-request sync into three minutes of fsync.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

_DEFAULT_DB = Path.home() / ".releaseradar" / "state.db"

DDL = """
CREATE TABLE IF NOT EXISTS repos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner           TEXT NOT NULL,
    name            TEXT NOT NULL,
    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    -- NULL until the first sync: that sync seeds the state and reports nothing.
    last_synced_at  TEXT,
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
    kind        TEXT NOT NULL,         -- one of EVENT_KINDS below
    ref_number  INTEGER,               -- issue or PR number, NULL for releases
    ref_tag     TEXT,                  -- release tag, NULL for issues/PRs
    payload     TEXT,                  -- JSON blob for extra context
    seen_at     TEXT NOT NULL DEFAULT (datetime('now')),
    notified    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_notified ON events(notified);
CREATE INDEX IF NOT EXISTS idx_events_repo     ON events(repo_id, seen_at DESC);
"""

EVENT_KINDS = ("issue_opened", "issue_closed", "pr_opened", "pr_merged", "pr_closed", "release_published")


def init(db_path: Path = _DEFAULT_DB) -> None:
    """Create tables if they don't exist yet, and bring an older file up to date."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(DDL)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(repos)")}
        if "last_synced_at" not in columns:
            conn.execute("ALTER TABLE repos ADD COLUMN last_synced_at TEXT")


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


def open_db(db_path: Path = _DEFAULT_DB) -> _DB:
    return _DB(db_path)


class _DB:
    """Thin DAO around the SQLite connection."""

    def __init__(self, path: Path = _DEFAULT_DB) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None
        init(path)

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """One connection, one commit, for everything inside the block."""
        if self._conn is not None:
            yield
            return
        with _connect(self.path) as conn:
            self._conn = conn
            try:
                yield
            finally:
                self._conn = None

    @contextmanager
    def _cursor(self) -> Generator[sqlite3.Connection, None, None]:
        if self._conn is not None:
            yield self._conn
            return
        with _connect(self.path) as conn:
            yield conn

    # ── repos ────────────────────────────────────────────────────────────────

    def add_repo(self, owner: str, name: str) -> int:
        with self._cursor() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO repos(owner, name) VALUES(?, ?)", (owner, name)
            )
            if cur.lastrowid and cur.rowcount:
                return cur.lastrowid
            row = conn.execute(
                "SELECT id FROM repos WHERE owner=? AND name=?", (owner, name)
            ).fetchone()
            return int(row["id"])

    def remove_repo(self, owner: str, name: str) -> bool:
        with self._cursor() as conn:
            cur = conn.execute("DELETE FROM repos WHERE owner=? AND name=?", (owner, name))
            return cur.rowcount > 0

    def list_repos(self) -> list[sqlite3.Row]:
        with self._cursor() as conn:
            return conn.execute(
                "SELECT id, owner, name, added_at, last_synced_at FROM repos ORDER BY owner, name"
            ).fetchall()

    def get_repo(self, owner: str, name: str) -> sqlite3.Row | None:
        with self._cursor() as conn:
            return conn.execute(
                "SELECT id, owner, name, added_at, last_synced_at FROM repos WHERE owner=? AND name=?",
                (owner, name),
            ).fetchone()

    def get_repo_id(self, owner: str, name: str) -> int | None:
        row = self.get_repo(owner, name)
        return int(row["id"]) if row else None

    def set_last_synced(self, repo_id: int, when: str) -> None:
        with self._cursor() as conn:
            conn.execute("UPDATE repos SET last_synced_at=? WHERE id=?", (when, repo_id))

    # ── issues / PRs / releases ──────────────────────────────────────────────
    #
    # The upserts answer the one question a sync has: what was the state before?
    # None means the row is new; anything else is the previous `state`.

    def upsert_issue(self, repo_id: int, data: dict) -> str | None:
        with self._cursor() as conn:
            existing = conn.execute(
                "SELECT state FROM issues WHERE repo_id=? AND number=?",
                (repo_id, data["number"]),
            ).fetchone()
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
            return None if existing is None else str(existing["state"])

    def upsert_pr(self, repo_id: int, data: dict) -> str | None:
        with self._cursor() as conn:
            existing = conn.execute(
                "SELECT state FROM pull_requests WHERE repo_id=? AND number=?",
                (repo_id, data["number"]),
            ).fetchone()
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
            return None if existing is None else str(existing["state"])

    def upsert_release(self, repo_id: int, data: dict) -> bool:
        """True when the tag was not stored before. Drafts have no `published_at`
        and are the caller's to skip: the table wants a date, because a release
        that is not published is not a release."""
        with self._cursor() as conn:
            existing = conn.execute(
                "SELECT id FROM releases WHERE repo_id=? AND tag_name=?",
                (repo_id, data["tag_name"]),
            ).fetchone()
            conn.execute(
                """INSERT INTO releases(repo_id, tag_name, name, html_url,
                       published_at, prerelease, body)
                   VALUES(:repo_id, :tag_name, :name, :html_url,
                          :published_at, :prerelease, :body)
                   ON CONFLICT(repo_id, tag_name) DO NOTHING""",
                {k: data.get(k) for k in ("tag_name", "name", "html_url", "published_at", "prerelease", "body")}
                | {"repo_id": repo_id},
            )
            return existing is None

    # ── events ───────────────────────────────────────────────────────────────

    def add_event(self, repo_id: int, kind: str, ref_number: int | None = None,
                  ref_tag: str | None = None, payload: str | None = None) -> None:
        with self._cursor() as conn:
            conn.execute(
                """INSERT INTO events(repo_id, kind, ref_number, ref_tag, payload)
                   VALUES(?, ?, ?, ?, ?)""",
                (repo_id, kind, ref_number, ref_tag, payload),
            )

    def recent_events(self, limit: int = 20) -> list[sqlite3.Row]:
        """Newest first. By id, not by `seen_at`: one sync writes its events
        within the same second, and a timestamp cannot order them."""
        with self._cursor() as conn:
            return conn.execute(
                """SELECT e.*, r.owner, r.name
                   FROM events e JOIN repos r ON r.id = e.repo_id
                   ORDER BY e.id DESC LIMIT ?""",
                (max(0, int(limit)),),
            ).fetchall()

    def pending_events(self) -> list[sqlite3.Row]:
        with self._cursor() as conn:
            return conn.execute(
                """SELECT e.*, r.owner, r.name
                   FROM events e JOIN repos r ON r.id = e.repo_id
                   WHERE e.notified = 0
                   ORDER BY e.id""",
            ).fetchall()

    def mark_notified(self, event_ids: list[int]) -> None:
        if not event_ids:
            return
        # One statement per id rather than an `IN (...)` built from the list:
        # the same work for SQLite, and no SQL assembled from a string.
        with self._cursor() as conn:
            conn.executemany("UPDATE events SET notified=1 WHERE id=?", [(int(i),) for i in event_ids])

    # ── read helpers for status display ──────────────────────────────────────

    def repo_summary(self, owner: str, name: str) -> dict:
        with self._cursor() as conn:
            repo = conn.execute(
                "SELECT id, last_synced_at FROM repos WHERE owner=? AND name=?", (owner, name)
            ).fetchone()
            if repo is None:
                return {}
            repo_id = repo["id"]
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
                "last_synced_at": repo["last_synced_at"],
            }
