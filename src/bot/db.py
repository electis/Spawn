"""SQLite database shared by the bot: Sessions, the group registry, and the group message log.

Schema changes are listed in MIGRATIONS. Each one runs in its own transaction and the
schema version is kept in PRAGMA user_version. WAL mode lets a separate API process read
while the bot writes. Writes are synchronous calls on the event loop thread: each one is a
small local transaction.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        """
        CREATE TABLE users (
            owner_key  TEXT PRIMARY KEY,  -- str(owner key); group keys exceed 64-bit integers
            active_sid TEXT
        )
        """,
        """
        CREATE TABLE sessions (
            owner_key     TEXT NOT NULL REFERENCES users(owner_key) ON DELETE CASCADE,
            sid           TEXT NOT NULL,
            name          TEXT NOT NULL,
            session_id    TEXT NOT NULL,
            context_usage REAL,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            persona       TEXT NOT NULL,
            PRIMARY KEY (owner_key, sid)
        )
        """,
        """
        CREATE TABLE group_chats (
            chat_id INTEGER PRIMARY KEY,
            title   TEXT NOT NULL,
            present INTEGER NOT NULL CHECK (present IN (0, 1)),
            allowed INTEGER NOT NULL CHECK (allowed IN (0, 1))
        )
        """,
        """
        CREATE TABLE group_messages (
            chat_id    INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            sent_us    INTEGER NOT NULL,  -- microseconds since the Unix epoch, UTC
            author     TEXT NOT NULL,
            text       TEXT NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        )
        """,
        """
        CREATE INDEX group_messages_recent ON group_messages (chat_id, sent_us, message_id)
        """,
        """
        CREATE TABLE meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
    ),
)


class DatabaseError(Exception):
    """The database file cannot be opened, or its schema is not one this bot understands."""


class Database:
    """One SQLite connection for the bot process. Opening it applies pending migrations."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        try:
            self.conn = sqlite3.connect(self.path)
            self.conn.row_factory = sqlite3.Row
            self._configure()
            self._migrate()
            if self.path != ":memory:":
                # The database holds Kiro Session ids and group data: owner-only, as the JSON was.
                for suffix in ("", "-wal", "-shm"):
                    if os.path.exists(self.path + suffix):
                        os.chmod(self.path + suffix, 0o600)
        except sqlite3.Error as exc:
            raise DatabaseError(f"cannot open database {self.path}: {exc}") from exc

    def _configure(self) -> None:
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise DatabaseError(
                f"database schema {version} is newer than this bot ({SCHEMA_VERSION})"
            )
        for number in range(version, SCHEMA_VERSION):
            # DDL does not open a transaction implicitly in sqlite3, so BEGIN is explicit.
            self.conn.execute("BEGIN")
            try:
                for statement in MIGRATIONS[number]:
                    self.conn.execute(statement)
                self.conn.execute(f"PRAGMA user_version = {number + 1}")
                self.conn.execute("COMMIT")
            except sqlite3.Error:
                self.conn.execute("ROLLBACK")
                raise

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def close(self) -> None:
        self.conn.close()
